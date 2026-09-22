"""
Pull der 60 RB/HO-Terminkontrakte (PX_LAST, taeglich) direkt ueber die Bloomberg
Desktop API. Alternative zu Weg A (Excel-Vorlage) fuer einen Bloomberg-PC mit Python.

Aufruf (auf dem Bloomberg-PC bei laufendem, angemeldetem Terminal; vom Projektstamm
oder aus bloomberg/ heraus, Pfade werden ueber src/spread/config.py aufgeloest):
    py bloomberg/pull_bloomberg_api.py
    py bloomberg/pull_bloomberg_api.py --start 2012-06-01 --end 2026-09-22 --out-dir .
    py bloomberg/pull_bloomberg_api.py --dry-run      # nur Tickerplan, kein Bloomberg noetig

Backend
-------
Bevorzugt xbbg (blp.bdh / blp.bdp). Ist xbbg nicht importierbar, rohes blpapi:
HistoricalDataRequest an //blp/refdata (localhost:8194), periodicitySelection=DAILY,
nonTradingDayFillOption=ACTIVE_DAYS_ONLY (nur Handelstage, kein Auffuellen).
Ist keines von beiden importierbar, werden Installationshinweise ausgegeben, Exit-Code 1.

Tickerform
----------
Erst zweistellige Jahresform (XBM13 Comdty). Kommt nichts zurueck, einstellige Form
(XBM3 Comdty, config.bbg_ticker(..., two_digit=False)); das betrifft normalerweise nur
noch laufende Kontrakte (Dez 2026, Jun 2027, Dez 2027). Welche Form gegriffen hat, wird
geloggt und steht in der Zusammenfassung. Bei der einstelligen Form wird zusaetzlich
LAST_TRADEABLE_DT abgefragt und gewarnt, wenn das Jahr nicht zum Jahrgang passt (die
einstellige Form zeigt immer auf den naechsten Kontrakt mit dieser Endziffer).

Ausgabe (relativ zu --out-dir, Standard = Projektstamm)
--------------------------------------------------------
data/raw/bloomberg_export.csv     breit, "eine Datumsspalte": Spalte "date" (ISO), dann je
                                  Ticker eine Spalte. Header = zweistellige Tickerform wie in
                                  config.all_tickers(), unabhaengig von der geholten Form.
                                  Alle 60 Spalten sind vorhanden, auch wenn ein Ticker leer blieb.
data/raw/bloomberg_export_long.csv    lang, exakt die Spalten config.PRICES_LONG_COLUMNS:
                                  date (ISO yyyy-mm-dd), ticker, product, month_code,
                                  contract_year, px.

EINHEIT (wichtig)
-----------------
Bloomberg notiert XB und HO in US-Cent je Gallone (USd/gal), PX_LAST ist also z.B. 245.30
und nicht 2.4530. Dieses Skript rechnet NICHT um: px steht in BEIDEN Ausgabedateien in der
rohen Bloomberg-Einheit. Die Normalisierung auf $/gal (wie in config.PRICES_LONG_COLUMNS
dokumentiert) macht ausschliesslich der Loader (src/spread/load.py). Wer prices_long.csv
aus diesem Skript direkt weiterverwendet, muss px durch 100 teilen.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from spread import config  # noqa: E402

import pandas as pd  # noqa: E402

FIELD = "PX_LAST"
EXPIRY_FIELD = "LAST_TRADEABLE_DT"
REFDATA = "//blp/refdata"
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8194
REQUEST_TIMEOUT_S = 180
EXPIRY_MONTH = {"M": 5, "Z": 11}      # Jun-Kontrakt endet Ende Mai, Dez-Kontrakt Ende Nov
PLAUSIBLE_CENTS = (10.0, 1000.0)      # USd/gal; ausserhalb -> Warnung (Einheit pruefen)

INSTALL_HINT = """\
Weder xbbg noch blpapi ist importierbar. Auf dem Bloomberg-PC installieren (Terminal muss laufen):
    py -m pip install blpapi --index-url https://blpapi.bloomberg.com/repository/releases/python/simple/
    py -m pip install xbbg
Danach erneut starten. Ohne API bleibt Weg A (Excel-Vorlage bloomberg_pull_template.xlsx).
Zum Testen ohne Bloomberg:  py bloomberg/pull_bloomberg_api.py --dry-run"""

log = logging.getLogger("bbg_pull")


# ----------------------------------------------------------------------------
# Tickerplan
# ----------------------------------------------------------------------------
@dataclass
class Leg:
    ticker: str            # zweistellige Form, Header in der Ausgabe
    fallback: str          # einstellige Form
    product: str           # "RB" | "HO"
    month_code: str        # "M" | "Z"
    leg: str               # "rb_jun" ...
    contract_year: int
    expiry: date           # nomineller letzter Handelstag
    used: str | None = None
    series: pd.Series | None = None
    note: str = ""


def nominal_last_trade_date(month_code: str, contract_year: int) -> date:
    """Letzter Werktag des Vormonats des Kontraktmonats (CME: last business day of the
    month prior to the contract month), Memorial Day beruecksichtigt, sonst 'ca.'."""
    m = EXPIRY_MONTH[month_code]
    d = date(contract_year, m + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    if m == 5 and d.weekday() == 0:
        d -= timedelta(days=3)
    return d


def build_plan() -> list[Leg]:
    plan = []
    for v in config.VINTAGES:
        for product, mc, leg in config.LEGS:
            plan.append(Leg(
                ticker=config.bbg_ticker(product, mc, v),
                fallback=config.bbg_ticker(product, mc, v, two_digit=False),
                product=product, month_code=mc, leg=leg, contract_year=v,
                expiry=nominal_last_trade_date(mc, v),
            ))
    assert [l.ticker for l in plan] == config.all_tickers(), "Reihenfolge weicht von config.all_tickers() ab"
    return plan


def print_plan(plan: list[Leg], start: date, end: date, out_dir: Path, backend_status: str) -> None:
    print(f"Tickerplan: {len(plan)} Ticker, Feld {FIELD}, taeglich, {start} bis {end}")
    print(f"Backend   : {backend_status}")
    print(f"Ausgabe   : {out_dir / 'data' / 'raw' / 'bloomberg_export.csv'}")
    print(f"            {out_dir / 'data' / 'raw' / 'bloomberg_export_long.csv'}")
    print(f"Einheit   : px in US-Cent je Gallone (rohe Bloomberg-Einheit), Loader normalisiert")
    print()
    hdr = f"{'#':>2}  {'Ticker':<13} {'Ersatz':<12} {'Prod':<4} {'Bein':<6} {'Jahrg.':>6}  {'letzter Handelstag ca.':<22} Status"
    print(hdr)
    print("-" * len(hdr))
    for i, l in enumerate(plan, start=1):
        status = "abgelaufen" if l.expiry < end else "laufend -> ggf. einstellige Form"
        print(f"{i:>2}  {l.ticker:<13} {l.fallback:<12} {l.product:<4} {l.leg:<6} {l.contract_year:>6}  "
              f"{l.expiry.isoformat():<22} {status}")
    n_active = sum(l.expiry >= end for l in plan)
    print(f"\n{len(plan) - n_active} abgelaufene, {n_active} laufende Kontrakte (Stand {end}).")


# ----------------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------------
def _to_series(dates, values, name: str) -> pd.Series:
    s = pd.Series(values, index=pd.to_datetime(pd.Index(dates)), name=name, dtype="float64")
    s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
    s.index = s.index.normalize()
    return s


class XbbgBackend:
    name = "xbbg"

    def __init__(self) -> None:
        from xbbg import blp
        self.blp = blp

    def close(self) -> None:
        pass

    def bdh(self, tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
        df = self.blp.bdh(tickers=tickers, flds=FIELD,
                          start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"))
        out: dict[str, pd.Series] = {}
        if df is None or df.empty:
            return out
        for t in tickers:
            col = None
            if isinstance(df.columns, pd.MultiIndex):
                matches = [c for c in df.columns if c[0] == t and str(c[1]).upper() == FIELD]
                col = matches[0] if matches else None
            elif len(tickers) == 1 and len(df.columns) >= 1:
                col = df.columns[0]
            if col is None:
                continue
            s = _to_series(df.index, df[col].to_numpy(), t)
            if not s.empty:
                out[t] = s
        return out

    def bdp(self, ticker: str, fld: str):
        df = self.blp.bdp(tickers=ticker, flds=fld)
        if df is None or df.empty:
            return None
        for c in df.columns:
            if str(c).upper() == fld.upper():
                return df[c].iloc[0]
        return df.iloc[0, 0]


class BlpapiBackend:
    name = "blpapi"

    def __init__(self, host: str, port: int) -> None:
        import blpapi
        self.blpapi = blpapi
        opts = blpapi.SessionOptions()
        opts.setServerHost(host)
        opts.setServerPort(port)
        self.session = blpapi.Session(opts)
        if not self.session.start():
            raise RuntimeError(f"Bloomberg-Session zu {host}:{port} konnte nicht gestartet werden (Terminal an?)")
        if not self.session.openService(REFDATA):
            self.session.stop()
            raise RuntimeError(f"Service {REFDATA} nicht verfuegbar")
        self.svc = self.session.getService(REFDATA)

    def close(self) -> None:
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001
            pass

    def _messages(self, request):
        """Sendet request und liefert alle Nachrichten bis zur RESPONSE (inkl. PARTIAL)."""
        Event = self.blpapi.Event
        self.session.sendRequest(request)
        deadline = time.monotonic() + REQUEST_TIMEOUT_S
        while True:
            ev = self.session.nextEvent(1000)
            et = ev.eventType()
            if et == Event.TIMEOUT:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Bloomberg hat innerhalb von {REQUEST_TIMEOUT_S}s nicht geantwortet")
                continue
            if et in (Event.PARTIAL_RESPONSE, Event.RESPONSE):
                deadline = time.monotonic() + REQUEST_TIMEOUT_S
                for msg in ev:
                    yield msg
                if et == Event.RESPONSE:
                    return

    def bdh(self, tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
        req = self.svc.createRequest("HistoricalDataRequest")
        for t in tickers:
            req.getElement("securities").appendValue(t)
        req.getElement("fields").appendValue(FIELD)
        req.set("periodicitySelection", "DAILY")
        req.set("startDate", start.strftime("%Y%m%d"))
        req.set("endDate", end.strftime("%Y%m%d"))
        req.set("nonTradingDayFillOption", "ACTIVE_DAYS_ONLY")
        raw: dict[str, tuple[list, list]] = {}
        for msg in self._messages(req):
            if msg.hasElement("responseError"):
                log.error("responseError: %s", msg.getElement("responseError"))
                continue
            if not msg.hasElement("securityData"):
                continue
            sd = msg.getElement("securityData")
            sec = sd.getElementAsString("security")
            if sd.hasElement("securityError"):
                log.debug("securityError %s: %s", sec, sd.getElement("securityError"))
                continue
            fd = sd.getElement("fieldData")
            dates, vals = raw.setdefault(sec, ([], []))
            for i in range(fd.numValues()):
                row = fd.getValueAsElement(i)
                if row.hasElement(FIELD):
                    dates.append(row.getElementAsDatetime("date"))
                    vals.append(row.getElementAsFloat(FIELD))
        out: dict[str, pd.Series] = {}
        for sec, (dates, vals) in raw.items():
            if dates:
                # Bloomberg gibt den Security-Namen so zurueck, wie er angefragt wurde
                s = _to_series(dates, vals, sec)
                if not s.empty:
                    out[sec] = s
        return out

    def bdp(self, ticker: str, fld: str):
        req = self.svc.createRequest("ReferenceDataRequest")
        req.getElement("securities").appendValue(ticker)
        req.getElement("fields").appendValue(fld)
        for msg in self._messages(req):
            if not msg.hasElement("securityData"):
                continue
            arr = msg.getElement("securityData")
            for i in range(arr.numValues()):
                sd = arr.getValueAsElement(i)
                fd = sd.getElement("fieldData")
                if fd.hasElement(fld):
                    el = fd.getElement(fld)
                    try:
                        return el.getValueAsDatetime()
                    except Exception:  # noqa: BLE001
                        return el.getValueAsString()
        return None


def detect_backends() -> dict[str, bool]:
    avail = {}
    for mod in ("xbbg", "blpapi"):
        try:
            __import__(mod)
            avail[mod] = True
        except Exception:  # noqa: BLE001 - ImportError, aber blpapi kann auch DLL-Fehler werfen
            avail[mod] = False
    return avail


def open_backend(host: str, port: int):
    avail = detect_backends()
    if avail["xbbg"]:
        try:
            b = XbbgBackend()
            log.info("Backend: xbbg")
            return b
        except Exception as exc:  # noqa: BLE001
            log.warning("xbbg importierbar, aber nicht nutzbar (%s); versuche blpapi", exc)
    if avail["blpapi"]:
        b = BlpapiBackend(host, port)
        log.info("Backend: blpapi (%s:%d, %s)", host, port, REFDATA)
        return b
    return None


# ----------------------------------------------------------------------------
# Pull
# ----------------------------------------------------------------------------
def safe_bdh(backend, tickers: list[str], start: date, end: date) -> dict[str, pd.Series]:
    """bdh mit Fehlerfang. Scheitert eine Sammelanfrage, wird einzeln nachgefragt."""
    try:
        return backend.bdh(tickers, start, end)
    except Exception as exc:  # noqa: BLE001
        if len(tickers) == 1:
            log.error("Anfrage %s fehlgeschlagen: %s", tickers[0], exc)
            return {}
        log.warning("Sammelanfrage (%d Ticker) fehlgeschlagen: %s -> einzeln", len(tickers), exc)
        out = {}
        for t in tickers:
            out.update(safe_bdh(backend, [t], start, end))
        return out


def check_fallback_contract(backend, leg: Leg) -> None:
    """Warnt, wenn die einstellige Form auf ein anderes Jahr zeigt als der Jahrgang."""
    try:
        val = backend.bdp(leg.fallback, EXPIRY_FIELD)
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: %s nicht abfragbar (%s), Jahrgang ungeprueft", leg.fallback, EXPIRY_FIELD, exc)
        return
    if val is None or (isinstance(val, float) and pd.isna(val)):
        log.warning("%s: %s leer, Jahrgang ungeprueft", leg.fallback, EXPIRY_FIELD)
        return
    try:
        ltd = pd.Timestamp(val).date()
    except Exception:  # noqa: BLE001
        log.warning("%s: %s=%r nicht interpretierbar", leg.fallback, EXPIRY_FIELD, val)
        return
    if ltd.year != leg.contract_year or ltd.month != EXPIRY_MONTH[leg.month_code]:
        leg.note = f"ACHTUNG {EXPIRY_FIELD}={ltd} passt nicht zu Jahrgang {leg.contract_year}"
        log.error("%s (fuer %s): %s", leg.fallback, leg.ticker, leg.note)
    else:
        log.info("%s: %s=%s passt zu %s", leg.fallback, EXPIRY_FIELD, ltd, leg.ticker)


def fetch_all(backend, plan: list[Leg], start: date, end: date) -> None:
    primaries = [l.ticker for l in plan]
    log.info("Anfrage 1: %d Ticker in zweistelliger Form, %s bis %s", len(primaries), start, end)
    got = safe_bdh(backend, primaries, start, end)
    for l in plan:
        s = got.get(l.ticker)
        if s is not None and not s.empty:
            l.series, l.used = s, l.ticker
    missing = [l for l in plan if l.series is None]
    log.info("Zweistellig geliefert: %d/%d", len(plan) - len(missing), len(plan))
    if not missing:
        return
    log.info("Anfrage 2: %d Ticker ohne Daten, einstellige Form: %s",
             len(missing), ", ".join(l.fallback for l in missing))
    for l in missing:
        s = safe_bdh(backend, [l.fallback], start, end).get(l.fallback)
        if s is None or s.empty:
            l.note = "keine Daten (zwei- und einstellig)"
            log.warning("%s / %s: keine Daten in beiden Formen", l.ticker, l.fallback)
            continue
        l.series, l.used = s, l.fallback
        log.info("%s: einstellige Form %s hat geliefert (%d Zeilen)", l.ticker, l.fallback, len(s))
        check_fallback_contract(backend, l)


def plausibility(plan: list[Leg], end: date) -> None:
    for l in plan:
        if l.series is None:
            continue
        first, last = l.series.index[0].date(), l.series.index[-1].date()
        lo, hi = float(l.series.min()), float(l.series.max())
        notes = []
        if l.expiry < end and not (last.year == l.expiry.year and last.month == l.expiry.month):
            notes.append(f"endet {last}, erwartet ca. {l.expiry}")
        if lo < PLAUSIBLE_CENTS[0] or hi > PLAUSIBLE_CENTS[1]:
            notes.append(f"Kurse {lo:.4g}..{hi:.4g} ausserhalb {PLAUSIBLE_CENTS} USd/gal - Einheit pruefen")
        if first < config.HISTORY_START:
            notes.append(f"beginnt vor HISTORY_START ({first})")
        if notes:
            l.note = "; ".join(filter(None, [l.note, *notes]))
            log.warning("%s: %s", l.ticker, "; ".join(notes))


def print_summary(plan: list[Leg]) -> None:
    hdr = f"{'Ticker':<13} {'geholt als':<13} {'n':>5}  {'erster Tag':<10}  {'letzter Tag':<11}  {'min':>8}  {'max':>8}  Hinweis"
    print()
    print("Zusammenfassung (px in US-Cent je Gallone, roh)")
    print(hdr)
    print("-" * len(hdr))
    for l in plan:
        if l.series is None:
            print(f"{l.ticker:<13} {'-':<13} {0:>5}  {'-':<10}  {'-':<11}  {'-':>8}  {'-':>8}  {l.note}")
            continue
        s = l.series
        print(f"{l.ticker:<13} {l.used:<13} {len(s):>5}  {s.index[0].date().isoformat():<10}  "
              f"{s.index[-1].date().isoformat():<11}  {s.min():>8.2f}  {s.max():>8.2f}  {l.note}")
    n_ok = sum(l.series is not None for l in plan)
    n_fb = sum(l.used is not None and l.used != l.ticker for l in plan)
    n_rows = sum(len(l.series) for l in plan if l.series is not None)
    print("-" * len(hdr))
    print(f"{n_ok}/{len(plan)} Ticker mit Daten, davon {n_fb} ueber die einstellige Form; {n_rows} Kurszeilen gesamt.")


# ----------------------------------------------------------------------------
# Ausgabe
# ----------------------------------------------------------------------------
def write_outputs(plan: list[Leg], out_dir: Path) -> tuple[Path, Path]:
    raw_path = out_dir / "data" / "raw" / "bloomberg_export.csv"
    long_path = out_dir / "data" / "raw" / "bloomberg_export_long.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    long_path.parent.mkdir(parents=True, exist_ok=True)

    # breit: alle 60 Spalten in config-Reihenfolge, Header = zweistellige Form
    cols = {l.ticker: (l.series if l.series is not None else pd.Series(dtype="float64")) for l in plan}
    wide = pd.DataFrame(cols).sort_index()
    wide.index.name = "date"
    wide = wide.reset_index()
    wide["date"] = pd.to_datetime(wide["date"]).dt.strftime("%Y-%m-%d")
    wide.to_csv(raw_path, index=False, encoding="utf-8", lineterminator="\n")

    # lang: exakt PRICES_LONG_COLUMNS
    parts = []
    for l in plan:
        if l.series is None:
            continue
        df = pd.DataFrame({"date": l.series.index, "px": l.series.to_numpy()})
        df["ticker"] = l.ticker
        df["product"] = l.product
        df["month_code"] = l.month_code
        df["contract_year"] = l.contract_year
        parts.append(df[config.PRICES_LONG_COLUMNS])
    if parts:
        long = pd.concat(parts, ignore_index=True).sort_values(["date", "ticker"], kind="stable")
    else:
        long = pd.DataFrame(columns=config.PRICES_LONG_COLUMNS)
    long["date"] = pd.to_datetime(long["date"]).dt.strftime("%Y-%m-%d")
    long.to_csv(long_path, index=False, encoding="utf-8", lineterminator="\n")
    return raw_path, long_path


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Bloomberg-API-Pull der 60 RB/HO-Ticker (PX_LAST, taeglich).")
    ap.add_argument("--start", type=date.fromisoformat, default=config.HISTORY_START,
                    help="Startdatum YYYY-MM-DD (Standard: config.HISTORY_START)")
    ap.add_argument("--end", type=date.fromisoformat, default=date.today(),
                    help="Enddatum YYYY-MM-DD (Standard: heute)")
    ap.add_argument("--out-dir", type=Path, default=config.PROJECT_ROOT,
                    help="Basisordner; darunter werden data/raw und data/processed beschrieben (Standard: Projektstamm)")
    ap.add_argument("--host", default=DEFAULT_HOST, help="blpapi-Host (Standard localhost)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="blpapi-Port (Standard 8194)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Nur den Tickerplan anzeigen, nichts abrufen, nichts schreiben (laeuft ohne Bloomberg)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Debug-Logging")
    args = ap.parse_args(argv)
    if args.start > args.end:
        ap.error(f"--start {args.start} liegt nach --end {args.end}")
    args.out_dir = args.out_dir if args.out_dir.is_absolute() else (Path.cwd() / args.out_dir).resolve()
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    plan = build_plan()
    avail = detect_backends()
    status = ", ".join(f"{k}: {'ja' if v else 'nein'}" for k, v in avail.items())

    if args.dry_run:
        print_plan(plan, args.start, args.end, args.out_dir, f"{status} (Dry-Run, kein Abruf)")
        return 0

    backend = open_backend(args.host, args.port)
    if backend is None:
        print(INSTALL_HINT, file=sys.stderr)
        return 1
    t0 = datetime.now()
    try:
        fetch_all(backend, plan, args.start, args.end)
    finally:
        backend.close()
    plausibility(plan, args.end)
    raw_path, long_path = write_outputs(plan, args.out_dir)
    print_summary(plan)
    print(f"\nGeschrieben:\n  {raw_path}\n  {long_path}")
    print(f"Dauer: {(datetime.now() - t0).total_seconds():.0f}s. "
          f"Naechster Schritt: data/raw/bloomberg_export.csv auf den Analyse-PC nach data/raw/ kopieren.")
    if any(l.series is None for l in plan):
        log.warning("%d Ticker ohne Daten, siehe Zusammenfassung.", sum(l.series is None for l in plan))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

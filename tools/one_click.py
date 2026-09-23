"""
Ein Klick: Umgebung pruefen -> Daten nachholen -> Analyse -> Bericht oeffnen.

    py tools/one_click.py                # alles
    py tools/one_click.py --no-pull      # nur rechnen, nichts von Bloomberg holen
    py tools/one_click.py --no-open      # Bericht nicht im Browser oeffnen
    py tools/one_click.py --check        # nur Umgebung pruefen, nichts installieren

Ablauf
------
1. tools/bootstrap.py: Python-Version, Rechenpakete, Bloomberg-API; fehlende
   Pakete werden nachinstalliert (Benutzerinstallation, keine Adminrechte).
2. Liegt ein Excel-Export in data/raw/, wandert er in den Cache.
3. Ist Bloomberg erreichbar, werden NUR die fehlenden Tage geholt
   (bloomberg/cache.py entscheidet je Kontrakt). Abgelaufene, vollstaendige
   Kontrakte werden nie wieder abgefragt - das haelt den Abzug unter dem
   Tageslimit.
4. run_analysis.py rechnet aus dem Cache und schreibt output/.
5. Laufzeitstempel nach output/last_run.txt, Bericht im Browser.

Ohne Bloomberg-Verbindung laeuft alles ausser Schritt 3; die Analyse nutzt dann
die zuletzt geholten Daten. Das Skript ist damit auf beiden Rechnern derselbe Klick.
"""
from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))          # run_analysis.py liegt im Projektstamm
sys.path.insert(0, str(PROJECT_ROOT / "tools"))
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT / "bloomberg"))

import bootstrap  # noqa: E402  - reine Standardbibliothek, muss zuerst laufen

RULE = "=" * 78


def head(text: str) -> None:
    print("\n" + RULE + "\n" + text + "\n" + RULE)


def _fmt_secs(s: float) -> str:
    return f"{s:.0f} s" if s < 90 else f"{s / 60:.1f} min"


# ----------------------------------------------------------------------------
# Schritt 2/3: Daten
# ----------------------------------------------------------------------------
def import_export_file(cache_mod, config) -> int:
    """Einen per Hand gebrachten Excel/CSV-Export in den Cache uebernehmen."""
    from spread import load as loader

    candidates = [p for p in sorted(config.DATA_RAW.glob("*"))
                  if p.suffix.lower() in {".xlsx", ".xlsm", ".csv"}
                  and not p.name.startswith("synthetic")
                  and p.name != cache_mod.CACHE_FILE.name]
    if not candidates:
        return 0
    n_new = 0
    for path in candidates:
        try:
            rows = loader.load_bloomberg_export(path)      # liefert bereits $/gal
        except Exception as exc:  # noqa: BLE001
            print(f"  {path.name}: nicht lesbar ({exc})")
            continue
        if rows.empty:
            print(f"  {path.name}: keine Kurse gefunden")
            continue
        old = cache_mod.load()
        merged = cache_mod.merge(old, rows)
        cache_mod.save(merged)
        added = len(merged) - len(old)
        n_new += added
        print(f"  {path.name}: {len(rows)} Zeilen eingelesen, {added} neu im Cache")
    return n_new


def _rows(pd, series, leg, config):
    df = pd.DataFrame({"date": series.index, "px": series.to_numpy()})
    df["ticker"] = leg.ticker
    df["product"] = leg.product
    df["month_code"] = leg.month_code
    df["contract_year"] = leg.contract_year
    return df[config.PRICES_LONG_COLUMNS]


def pull_missing(cache_mod, config, today: date, host: str, port: int) -> str:
    """Nur die fehlenden Tage holen. Gibt eine Statuszeile zurueck."""
    import socket

    import pandas as pd
    import pull_bloomberg_api as api

    # Schneller Vorabtest: ohne laufendes Terminal horcht niemand auf 8194.
    # Ohne ihn laeuft blpapi auf einem gewoehnlichen PC rund eine Minute in den Timeout.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        if probe.connect_ex((host, port)) != 0:
            return (f"kein Bloomberg-Terminal erreichbar ({host}:{port} antwortet nicht) - "
                    "es wird mit den vorhandenen Daten gerechnet")

    try:
        backend = api.open_backend(host, port)
    except Exception as exc:  # noqa: BLE001 - kein Terminal, keine Lizenz, Port belegt ...
        return (f"keine Verbindung zum Bloomberg-Terminal ({exc}) - "
                "es wird mit den vorhandenen Daten gerechnet")
    if backend is None:
        return ("keine Bloomberg-API auf diesem Rechner (blpapi/xbbg fehlen) - "
                "es wird mit den vorhandenen Daten gerechnet")

    plan = api.build_plan()
    legs = {l.ticker: l for l in plan}
    cached = cache_mod.load()
    needs = cache_mod.needs([(l.ticker, l.contract_year, l.month_code) for l in plan], cached, today)

    todo = [n for n in needs if n.start is not None]
    print(f"  {len(needs) - len(todo)} Kontrakte vollstaendig im Cache (werden nicht mehr abgefragt)")
    if not todo:
        return f"nichts nachzuholen, Cache aktuell ({cache_mod.summary(cached)})"

    groups: dict[date, list[str]] = {}
    for n in todo:
        groups.setdefault(n.start, []).append(n.ticker)
    est = sum(len(t) * max((today - s).days, 1) * 0.7 for s, t in groups.items())
    print(f"  {len(todo)} Kontrakte nachzuholen in {len(groups)} Anfrage(n), grob {est:,.0f} Datenpunkte")

    parts: list = []
    empty: list = []
    for start, tickers in sorted(groups.items()):
        print(f"  Anfrage: {len(tickers)} Ticker ab {start}")
        got = api.safe_bdh(backend, tickers, start, today)
        for t in tickers:
            s = got.get(t)
            if s is None or s.empty:
                empty.append((t, start))
                continue
            parts.append(_rows(pd, s, legs[t], config))

    # Noch laufende Kontrakte loesen manchmal nur in der einstelligen Form auf.
    for item in list(empty):
        t, start = item
        leg = legs[t]
        if leg.expiry < today:
            continue
        s = api.safe_bdh(backend, [leg.fallback], start, today).get(leg.fallback)
        if s is not None and not s.empty:
            print(f"    {t}: ueber die einstellige Form {leg.fallback} geholt ({len(s)} Zeilen)")
            parts.append(_rows(pd, s, leg, config))
            empty.remove(item)

    if not parts:
        return ("Bloomberg hat auf keine Anfrage Daten geliefert. Haeufigste Ursache: "
                "Tageslimit erschoepft (#N/A Daily Capacity). Es wird mit den vorhandenen Daten gerechnet")

    new = cache_mod.to_usd_per_gal(pd.concat(parts, ignore_index=True))
    merged = cache_mod.merge(cached, new)
    cache_mod.save(merged)
    msg = f"{len(merged) - len(cached)} neue Kurszeilen geholt; Cache: {cache_mod.summary(merged)}"
    if empty:
        msg += "; ohne Daten: " + ", ".join(t for t, _ in empty)
    return msg


def choose_input(cache_mod, config) -> Path | None:
    if not cache_mod.load().empty:
        return cache_mod.CACHE_FILE
    for name in ("bloomberg_export.xlsx", "bloomberg_export.csv", "bloomberg_export.xlsm"):
        p = config.DATA_RAW / name
        if p.exists():
            return p
    return None


# ----------------------------------------------------------------------------
# Ablauf
# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ein Klick: holen, rechnen, berichten")
    ap.add_argument("--no-pull", action="store_true", help="nichts von Bloomberg holen")
    ap.add_argument("--no-open", action="store_true", help="Bericht nicht oeffnen")
    ap.add_argument("--no-install", action="store_true", help="fehlende Pakete nicht nachinstallieren")
    ap.add_argument("--check", action="store_true", help="nur Umgebung pruefen und beenden")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8194)
    args = ap.parse_args(argv)

    t0 = time.time()
    started = datetime.now()
    head("RB/HO Jun-vs-Dec Spread - Lauf vom " + started.strftime("%d.%m.%Y um %H:%M:%S"))

    head("1/4  Umgebung")
    core_ok, bbg_ok = bootstrap.run(install=not (args.no_install or args.check),
                                    bloomberg=not args.no_pull)
    if args.check:
        return 0 if core_ok else 1
    if not core_ok:
        print("Abbruch: ohne die Rechenpakete kann nicht gerechnet werden.")
        print("Pruefen Sie die Internetverbindung oder installieren Sie von Hand:")
        print("  py -m pip install --user pandas numpy openpyxl matplotlib plotly")
        return 1

    from spread import config
    import cache as cache_mod

    head("2/4  Daten")
    print("Exportdateien in data/raw/:")
    if import_export_file(cache_mod, config) == 0:
        print("  keine neuen Exportdateien")
    if args.no_pull:
        status = "uebersprungen (--no-pull)"
    elif not bbg_ok:
        status = "keine Bloomberg-API auf diesem Rechner - es wird mit den vorhandenen Daten gerechnet"
    else:
        print("Bloomberg-Abzug (nur fehlende Tage):")
        try:
            status = pull_missing(cache_mod, config, date.today(), args.host, args.port)
        except Exception as exc:  # noqa: BLE001 - ein gescheiterter Abzug darf den Lauf nie beenden
            status = f"Abzug fehlgeschlagen ({exc}) - es wird mit den vorhandenen Daten gerechnet"
    print("Ergebnis: " + status)

    src = choose_input(cache_mod, config)
    if src is None:
        print("\nAbbruch: keine Daten. Es liegt weder ein Cache noch ein Bloomberg-Export vor.")
        print(f"Legen Sie einen Export nach {config.DATA_RAW} oder starten Sie dies auf dem Bloomberg-PC.")
        return 1

    head("3/4  Analyse")
    import run_analysis
    rc = run_analysis.main(["--input", str(src)])
    if rc != 0:
        print("Analyse mit Fehler beendet.")
        return rc

    head("4/4  Ergebnis")
    finished = datetime.now()
    stamp = (f"Lauf gestartet : {started.strftime('%d.%m.%Y %H:%M:%S')}\n"
             f"Lauf beendet   : {finished.strftime('%d.%m.%Y %H:%M:%S')}\n"
             f"Dauer          : {_fmt_secs(time.time() - t0)}\n"
             f"Datenquelle    : {src}\n"
             f"Bloomberg      : {status}\n")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (config.OUTPUT_DIR / "last_run.txt").write_text(stamp, encoding="utf-8")
    print(stamp)

    report = config.OUTPUT_DIR / "report.html"
    print(f"Bericht   : {report}")
    print(f"Dashboard : {config.OUTPUT_DIR / 'dashboard.html'}")
    if not args.no_open and report.exists():
        webbrowser.open(report.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

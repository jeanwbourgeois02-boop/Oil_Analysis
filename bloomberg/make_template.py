"""
Erzeugt bloomberg_pull_template.xlsx fuer den Daten-Pull per Bloomberg-Excel-Add-in.

Aufruf (vom Projektstamm oder aus bloomberg/ heraus, Pfade werden ueber config aufgeloest):
    py bloomberg/make_template.py
    py bloomberg/make_template.py --out anderer_name.xlsx --end 20271231

Aufbau der erzeugten Datei
--------------------------
Blatt "Daten" (Hauptweg, Excel-Add-in):
    Ticker i (0-basiert, Reihenfolge = config.all_tickers()) steht in Zeile 1 der
    Spalte 2*i+1, die BDH-Formel dazu in Zeile 2 derselben Spalte, z.B. fuer Spalte A:
        =BDH(A1,"PX_LAST","20120601","20271231","Days=T","Dts=S","Sort=A","Dir=V")
    BDH fuellt Datum + Wert nach unten in ZWEI Spalten (Dts=S, Dir=V), deshalb bleibt
    Spalte 2*i+2 leer. 60 Ticker -> 120 Spalten. Zeile 1 enthaelt sonst nichts, der
    Loader sucht dort nach Ticker-Mustern und liest darunter die (Datum, Wert)-Paare.
    Datumsangaben als YYYYMMDD-Strings (unabhaengig vom Excel-Gebietsschema),
    "Days=T" = nur Handelstage, kein Auffuellen.

Blatt "Tickerliste": A = Ticker (zweistellige Jahresform), B = einstellige Ersatzform
    fuer noch laufende Kontrakte, C = Beschreibung, D = erwarteter letzter Handelstag,
    E = Status. Fuer den Bloomberg Spreadsheet Builder als Alternativweg.

Blatt "Anleitung": Kurzanleitung auf Deutsch.

EINHEIT: Bloomberg liefert XB und HO in US-Cent je Gallone (Werte wie 245.30, nicht
2.4530). Die Vorlage rechnet NICHT um; das macht der Loader (src/spread/load.py).

Nach dem Schreiben wird die Datei mit openpyxl erneut geoeffnet und geprueft
(60 Tickerzellen, 60 Formeln, exakter Formeltext, Tickerliste). Exit-Code 1 bei Fehlern.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
from spread import config  # noqa: E402

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.workbook.properties import CalcProperties  # noqa: E402

FIELD = "PX_LAST"
# Optionale BDH-Argumente: nur Handelstage, Datumsspalte anzeigen, aufsteigend, vertikal.
BDH_OPTIONS = ("Days=T", "Dts=S", "Sort=A", "Dir=V")
TEMPLATE_NAME = "bloomberg_pull_template.xlsx"

PRODUCT_NAMES = {"RB": "RBOB Gasoline (NYMEX, Root XB)", "HO": "NY Harbor ULSD (NYMEX, Root HO)"}
LEG_NAMES = {"M": "Jun", "Z": "Dez"}
EXPIRY_MONTH = {"M": 5, "Z": 11}      # Jun-Kontrakt endet Ende Mai, Dez-Kontrakt Ende Nov


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
def default_end() -> str:
    """Jahresende des letzten konfigurierten Jahrgangs; deckt auch den Dez-Kontrakt ab.
    Ein Enddatum in der Zukunft ist fuer BDH unkritisch (liefert bis zum letzten Kurs)."""
    return f"{config.LAST_VINTAGE}1231"


def bdh_formula(ticker_cell: str, start: str, end: str) -> str:
    opts = ",".join(f'"{o}"' for o in BDH_OPTIONS)
    return f'=BDH({ticker_cell},"{FIELD}","{start}","{end}",{opts})'


def nominal_last_trade_date(month_code: str, contract_year: int) -> date:
    """Letzter Werktag des Vormonats des Kontraktmonats (CME: last business day of the
    month prior to the contract month). Memorial Day (letzter Montag im Mai) wird
    beruecksichtigt, sonstige Feiertage nicht -> 'ca.'."""
    m = EXPIRY_MONTH[month_code]
    d = date(contract_year, m + 1, 1) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    if m == 5 and d.weekday() == 0:      # 31. Mai ist ein Montag = Memorial Day
        d -= timedelta(days=3)
    return d


def ticker_plan() -> list[dict]:
    """Ein Eintrag je Ticker in der Reihenfolge von config.all_tickers()."""
    rows = []
    for v in config.VINTAGES:
        for product, mc, leg in config.LEGS:
            rows.append({
                "ticker": config.bbg_ticker(product, mc, v),
                "fallback": config.bbg_ticker(product, mc, v, two_digit=False),
                "product": product,
                "month_code": mc,
                "leg": leg,
                "vintage": v,
                "expiry": nominal_last_trade_date(mc, v),
            })
    assert [r["ticker"] for r in rows] == config.all_tickers(), "Reihenfolge weicht von config.all_tickers() ab"
    return rows


# ----------------------------------------------------------------------------
# Blaetter
# ----------------------------------------------------------------------------
def build_daten_sheet(ws, plan: list[dict], start: str, end: str) -> None:
    ws.title = "Daten"
    bold = Font(bold=True)
    for i, row in enumerate(plan):
        col = 2 * i + 1
        letter = get_column_letter(col)
        c = ws.cell(row=1, column=col, value=row["ticker"])
        c.font = bold
        c.alignment = Alignment(horizontal="left")
        ws.cell(row=2, column=col, value=bdh_formula(f"{letter}1", start, end))
        ws.column_dimensions[letter].width = 14                          # Datumsspalte
        ws.column_dimensions[get_column_letter(col + 1)].width = 10      # Wertspalte (bleibt leer, BDH fuellt)
    ws.freeze_panes = "A2"


def build_tickerliste_sheet(ws, plan: list[dict], today: date) -> None:
    ws.title = "Tickerliste"
    header = ["Ticker (zweistellig)", "Ersatzform (einstellig)", "Beschreibung",
              "Erwarteter letzter Handelstag (ca.)", f"Status (Stand {today.isoformat()})"]
    for j, h in enumerate(header, start=1):
        c = ws.cell(row=1, column=j, value=h)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="DDEBF7")
    for r, row in enumerate(plan, start=2):
        leg_name = LEG_NAMES[row["month_code"]]
        desc = (f"{PRODUCT_NAMES[row['product']]} | {leg_name}-Bein | "
                f"Jahrgang {row['vintage']} | Kontrakt {leg_name} {row['vintage']}")
        status = "abgelaufen" if row["expiry"] < today else "laufend (ggf. einstellige Form noetig)"
        ws.cell(row=r, column=1, value=row["ticker"])
        ws.cell(row=r, column=2, value=row["fallback"])
        ws.cell(row=r, column=3, value=desc)
        d = ws.cell(row=r, column=4, value=row["expiry"])
        d.number_format = "yyyy-mm-dd"
        ws.cell(row=r, column=5, value=status)
    for letter, w in zip("ABCDE", (20, 22, 70, 34, 40)):
        ws.column_dimensions[letter].width = w
    ws.freeze_panes = "A2"


ANLEITUNG_ROWS = [
    ("Bloomberg-Pull RB/HO Jun-vs-Dec Spread - Kurzanleitung (ausfuehrlich: bloomberg/ANLEITUNG_BLOOMBERG.md)", "title"),
    ("", None),
    ("Ziel: Am Ende muss auf dem Analyse-PC die Datei data/raw/bloomberg_export.xlsx (oder .csv) liegen, mit "
     "Tageskursen (PX_LAST) fuer alle 60 Ticker ab 01.06.2012.", None),
    ("", None),
    ("Weg A - Blatt 'Daten' mit dem Excel-Add-in (empfohlen)", "head"),
    ("1. Diese Datei auf den Bloomberg-PC kopieren und in Excel oeffnen. Das Bloomberg-Add-in muss geladen sein "
     "(Menueband-Reiter 'Bloomberg' sichtbar, Terminal angemeldet).", None),
    ("2. Zeigen die Zellen in Zeile 2 '#NAME?' oder bleiben leer: Strg+Alt+F9 (vollstaendige Neuberechnung) "
     "druecken und warten, bis die Bloomberg-Anzeige in der Excel-Statusleiste nichts mehr laedt.", None),
    ("3. Pruefen: Jede Tickerspalte hat mehrere hundert Zeilen (abgelaufene Kontrakte ca. 600-750 Zeilen, "
     "Jahrgang 2013 weniger, weil der Pull erst am 01.06.2012 beginnt).", None),
    ("4. Liefert ein noch laufender Kontrakt (Dez 2026, Jun 2027, Dez 2027) nur '#N/A' oder 'Invalid security': "
     "den Tickertext in Zeile 1 durch die einstellige Form aus Blatt 'Tickerliste' ersetzen "
     "(z.B. 'XBZ26 Comdty' -> 'XBZ6 Comdty'), dann erneut Strg+Alt+F9.", None),
    ("5. WICHTIG - Formeln in Werte umwandeln: Strg+A, Strg+C, Rechtsklick -> Inhalte einfuegen -> Werte. "
     "Ohne diesen Schritt ist die Datei auf einem PC ohne Bloomberg leer.", None),
    ("6. Speichern unter: bloomberg_export.xlsx (Dateityp Excel-Arbeitsmappe .xlsx).", None),
    ("7. Datei auf den Analyse-PC in den Projektordner data/raw/ kopieren.", None),
    ("", None),
    ("Weg C - Bloomberg Spreadsheet Builder (Ersatzweg)", "head"),
    ("Bloomberg-Reiter -> Spreadsheet Builder -> Historical Data Table. Ticker aus Blatt 'Tickerliste', "
     "Bereich A2:A61 (bei laufenden Kontrakten ggf. Spalte B). Feld PX_LAST, Start 01.06.2012, taeglich, "
     "keine Nicht-Handelstage. Beide Layouts (eine Datumsspalte oder Datum je Ticker) werden vom Loader akzeptiert. "
     "Danach ebenfalls in Werte umwandeln und als bloomberg_export.xlsx speichern.", None),
    ("", None),
    ("Plausibilitaet vor dem Kopieren", "head"),
    ("- Abgelaufene Jun-Kontrakte enden am letzten Geschaeftstag im Mai, Dez-Kontrakte am letzten Geschaeftstag im November "
     "(siehe Spalte D in 'Tickerliste').", None),
    ("- Kurse sind US-Cent je Gallone: Werte grob zwischen 40 und 450 (z.B. 245.30). NICHT umrechnen, das macht der Loader.", None),
    ("- 60 Ticker: XB (RBOB) und HO (ULSD), jeweils Jun und Dez, Jahrgaenge 2013 bis 2027.", None),
    ("", None),
    ("Warum zwei Tickerformen? Bloomberg fuehrt abgelaufene Kontrakte nur noch zweistellig (XBM13). Die einstellige Form "
     "(XBM3) zeigt immer auf den naechsten Kontrakt mit dieser Endziffer, bei alten Jahrgaengen also auf das falsche Jahrzehnt. "
     "Bei laufenden Kontrakten loest dagegen manchmal nur die einstellige Form auf.", None),
]


def build_anleitung_sheet(ws) -> None:
    ws.title = "Anleitung"
    ws.column_dimensions["A"].width = 130
    for r, (text, style) in enumerate(ANLEITUNG_ROWS, start=1):
        c = ws.cell(row=r, column=1, value=text)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if style == "title":
            c.font = Font(bold=True, size=13)
        elif style == "head":
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDEBF7")


# ----------------------------------------------------------------------------
# Erzeugen und Pruefen
# ----------------------------------------------------------------------------
def make_template(out_path: Path, start: str, end: str, today: date) -> None:
    plan = ticker_plan()
    wb = Workbook()
    build_daten_sheet(wb.active, plan, start, end)
    build_tickerliste_sheet(wb.create_sheet(), plan, today)
    build_anleitung_sheet(wb.create_sheet())
    # Excel soll beim Oeffnen alles neu berechnen (die Datei enthaelt keine Zwischenwerte).
    wb.calculation = CalcProperties(fullCalcOnLoad=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def verify_template(path: Path, start: str, end: str) -> bool:
    tickers = config.all_tickers()
    wb = load_workbook(path)          # ohne data_only -> Formeln als Text
    problems: list[str] = []

    if set(wb.sheetnames) != {"Daten", "Tickerliste", "Anleitung"}:
        problems.append(f"Blattnamen unerwartet: {wb.sheetnames}")
    ws = wb["Daten"]
    n_ticker_ok = n_formula_ok = n_gap_ok = 0
    for i, t in enumerate(tickers):
        col = 2 * i + 1
        letter = get_column_letter(col)
        if ws.cell(row=1, column=col).value == t:
            n_ticker_ok += 1
        else:
            problems.append(f"Daten!{letter}1: erwartet {t!r}, gefunden {ws.cell(row=1, column=col).value!r}")
        expected = bdh_formula(f"{letter}1", start, end)
        got = ws.cell(row=2, column=col).value
        if got == expected:
            n_formula_ok += 1
        else:
            problems.append(f"Daten!{letter}2: Formel weicht ab: {got!r}")
        if ws.cell(row=1, column=col + 1).value is None and ws.cell(row=2, column=col + 1).value is None:
            n_gap_ok += 1
        else:
            problems.append(f"Daten!{get_column_letter(col + 1)}1/2 muss leer sein")
    if ws.max_column != 2 * len(tickers):
        problems.append(f"Daten: {ws.max_column} Spalten, erwartet {2 * len(tickers)}")
    extra_row1 = [c.coordinate for c in ws[1] if c.value is not None and c.value not in tickers]
    if extra_row1:
        problems.append(f"Daten Zeile 1 enthaelt Fremdes: {extra_row1}")
    if ws.freeze_panes != "A2":
        problems.append(f"Daten: Fixierung ist {ws.freeze_panes!r}, erwartet 'A2'")

    wl = wb["Tickerliste"]
    listed = [wl.cell(row=r, column=1).value for r in range(2, 2 + len(tickers))]
    fallbacks = [wl.cell(row=r, column=2).value for r in range(2, 2 + len(tickers))]
    exp_fallbacks = [config.bbg_ticker(p, mc, v, two_digit=False)
                     for v in config.VINTAGES for p, mc, _ in config.LEGS]
    if listed != tickers:
        problems.append("Tickerliste Spalte A stimmt nicht mit config.all_tickers() ueberein")
    if fallbacks != exp_fallbacks:
        problems.append("Tickerliste Spalte B (einstellige Form) stimmt nicht")
    if wl.cell(row=2 + len(tickers), column=1).value is not None:
        problems.append("Tickerliste enthaelt mehr als 60 Datenzeilen")

    print("Pruefung der Vorlage")
    print(f"  Datei                 : {path}")
    print(f"  Blaetter              : {wb.sheetnames}")
    print(f"  Tickerzellen Zeile 1  : {n_ticker_ok}/{len(tickers)} korrekt")
    print(f"  BDH-Formeln Zeile 2   : {n_formula_ok}/{len(tickers)} exakt wie erwartet")
    print(f"  Leere Nachbarspalten  : {n_gap_ok}/{len(tickers)}")
    print(f"  Spalten gesamt        : {ws.max_column}")
    print(f"  Tickerliste           : {sum(x is not None for x in listed)} Ticker, Ersatzformen {'ok' if fallbacks == exp_fallbacks else 'FEHLER'}")
    print(f"  Beispiel A1/A2        : {ws['A1'].value!r}")
    print(f"                          {ws['A2'].value}")
    last = get_column_letter(2 * len(tickers) - 1)
    print(f"  Beispiel {last}1/{last}2    : {ws[f'{last}1'].value!r}")
    print(f"                          {ws[f'{last}2'].value}")
    if problems:
        print(f"  PROBLEME ({len(problems)}):")
        for p in problems:
            print(f"    - {p}")
        return False
    print("  Ergebnis              : OK")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Erzeugt die Bloomberg-Excel-Vorlage (BDH-Formeln) fuer die 60 RB/HO-Ticker.")
    ap.add_argument("--out", type=Path, default=config.BLOOMBERG_DIR / TEMPLATE_NAME,
                    help=f"Zielpfad der Vorlage (Standard: bloomberg/{TEMPLATE_NAME})")
    ap.add_argument("--start", default=config.HISTORY_START.strftime("%Y%m%d"),
                    help="Startdatum YYYYMMDD (Standard: config.HISTORY_START)")
    ap.add_argument("--end", default=default_end(),
                    help="Enddatum YYYYMMDD (Standard: 31.12. des letzten Jahrgangs; Zukunft ist unkritisch)")
    args = ap.parse_args(argv)

    for label, val in (("start", args.start), ("end", args.end)):
        if len(val) != 8 or not val.isdigit():
            ap.error(f"--{label} muss YYYYMMDD sein, nicht {val!r}")

    out = args.out if args.out.is_absolute() else (Path.cwd() / args.out).resolve()
    make_template(out, args.start, args.end, date.today())
    print(f"Vorlage geschrieben: {out}")
    print(f"  Zeitraum in BDH: {args.start} bis {args.end}, Feld {FIELD}, Optionen {', '.join(BDH_OPTIONS)}")
    return 0 if verify_template(out, args.start, args.end) else 1


if __name__ == "__main__":
    sys.exit(main())

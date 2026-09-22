# bloomberg/ – Daten-Abzug-Kit

Werkzeuge, um die 60 RB/HO-Terminkontrakte (PX_LAST, täglich, ab 01.06.2012) von einem Bloomberg-PC zu holen. Ticker, Zeitraum und Spaltenschema kommen aus `src/spread/config.py`.

| Datei | Zweck |
|---|---|
| `ANLEITUNG_BLOOMBERG.md` | Schritt-für-Schritt-Anleitung (deutsch) für den Bloomberg-PC: Weg A Excel-Vorlage, Weg B API-Skript, Weg C Spreadsheet Builder, Checks, Fehler. **Hier anfangen.** |
| `make_template.py` | Erzeugt `bloomberg_pull_template.xlsx` (openpyxl) und prüft sie: `py bloomberg/make_template.py` |
| `bloomberg_pull_template.xlsx` | Excel-Vorlage: Blatt *Daten* (60 `BDH`-Formeln), *Tickerliste* (zwei- und einstellige Form), *Anleitung*. Auf dem Bloomberg-PC öffnen, berechnen, in Werte umwandeln, als `data/raw/bloomberg_export.xlsx` speichern. |
| `pull_bloomberg_api.py` | Alternative per Desktop-API (xbbg oder blpapi): schreibt `data/raw/bloomberg_export.csv` und `data/raw/bloomberg_export_long.csv`. Test ohne Bloomberg: `py bloomberg/pull_bloomberg_api.py --dry-run` |

Einheit der Rohdaten: US-Cent je Gallone (z. B. `245.30`). Das Kit rechnet nicht um; der Loader normalisiert.

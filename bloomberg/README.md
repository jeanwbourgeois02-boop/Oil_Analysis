# bloomberg/ – Daten-Abzug-Kit

Werkzeuge, um die 60 RB/HO-Terminkontrakte (PX_LAST, täglich, ab 01.06.2012) von einem Bloomberg-PC zu holen. Ticker, Zeitraum und Spaltenschema kommen aus `src/spread/config.py`.

| Datei | Zweck |
|---|---|
| `ANLEITUNG_BLOOMBERG.md` | Schritt-für-Schritt-Anleitung (deutsch) für den Bloomberg-PC: Weg A Excel-Vorlage, Weg B API-Skript, Weg C Spreadsheet Builder, Checks, Fehler. **Hier anfangen.** |
| `make_template.py` | Erzeugt `bloomberg_pull_template.xlsx` (openpyxl) und prüft sie: `py bloomberg/make_template.py` |
| `bloomberg_pull_template.xlsx` | Excel-Vorlage: Blatt *Daten* (60 `BDH`-Formeln), *Tickerliste* (zwei- und einstellige Form), *Anleitung*. Auf dem Bloomberg-PC öffnen, berechnen, in Werte umwandeln, als `data/raw/bloomberg_export.xlsx` speichern. |
| `cache.py` | Inkrementeller Preis-Cache (`data/raw/price_cache.csv`, $/gal). Entscheidet je Kontrakt, was noch fehlt; abgelaufene, vollstaendige Kontrakte werden nie wieder abgefragt. Haelt den Abzug unter dem Tageslimit. |
| `make_batches.py` | Teil-Vorlagen je Jahrgangsblock, falls das Tageslimit keinen Abzug aller 60 Ticker erlaubt: `py bloomberg/make_batches.py` |
| `pull_bloomberg_api.py` | Alternative per Desktop-API (xbbg oder blpapi): schreibt `data/raw/bloomberg_export.csv` und `data/raw/bloomberg_export_long.csv`. Test ohne Bloomberg: `py bloomberg/pull_bloomberg_api.py --dry-run` |

Der bequemste Weg ist `ANALYSE_STARTEN.bat` im Projektstamm: prueft die Umgebung, holt nur die
fehlenden Tage, rechnet und oeffnet den Bericht. Diese Bausteine sind fuer den Fall, dass das
nicht geht.

Einheit der Rohdaten: US-Cent je Gallone (z. B. `245.30`). Das Kit rechnet nicht um; der Loader normalisiert.

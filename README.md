# RBOB/HO Jun-vs-Dec Crack-Spread, Jahrgänge 2013–2027

Tägliche Analyse des Spreads

    spread_bbl = [(RBOB Jun − HO Jun) − (RBOB Dec − HO Dec)] × 42     in $/bbl

pro Jahrgang Y: Fenster 1. Juni (Y−1) bis letzter Handelstag des Jun-Kontrakts
(letzter NYMEX-Geschäftstag im Mai Y). Erster Jahrgang 2013 (ab dort ist HO = ULSD),
letzter abgeschlossener Jahrgang 2026, Jahrgang 2027 läuft seit 1. Juni 2026.

## Ablauf in drei Schritten

1. **Daten am Bloomberg-Rechner ziehen** → siehe [bloomberg/ANLEITUNG_BLOOMBERG.md](bloomberg/ANLEITUNG_BLOOMBERG.md).
   Ergebnis: eine Datei `bloomberg_export.xlsx` (oder `.csv`).
2. **Datei hierher kopieren** nach `data/raw/bloomberg_export.xlsx`.
3. **Analyse laufen lassen**:

       py run_analysis.py

   Testlauf ohne echte Daten (synthetische Preise, nur zum Prüfen der Pipeline; Ergebnisse landen getrennt in `output/synthetic/`):

       py run_analysis.py --synthetic

## Was herauskommt (`output/`)

| Datei | Inhalt |
|---|---|
| `spread_daily.csv` | Durchgehende tägliche Kette 2012‑06 bis heute: alle vier Beine, Cracks, Spread, Jahrgang, Tage bis Verfall |
| `vintage_stats.csv` | Kennzahlen pro Jahrgang: Start, Ende, Min/Max mit Datum, Tage negativ, längste Negativstrecke, Drawdown, Werte bei T‑120/60/20 |
| `seasonal_by_tdays_to_expiry.csv` | Saison-Matrix: Zeilen = Handelstage bis Verfall, Spalten = Jahrgänge + Mittel/Median/Min/Max |
| `seasonal_by_tday_index.csv` | Gleiche Matrix, Zeilen = Handelstage seit Fensterbeginn (für den laufenden Jahrgang) |
| `summary.json` | Gesamtüberblick: absolutes Minimum, welche Jahrgänge negativ wurden usw. |
| `chart_chain.png` | Durchgehende Kette mit Jahrgangsbändern und Rollpunkten Ende Mai |
| `chart_seasonal.png` | Saisonaler Overlay: eine Linie pro Jahrgang, x = Tage bis Verfall, Median-Band, 2027 hervorgehoben |
| `chart_vintage_range.png` | Pro Jahrgang: Spanne Min–Max, Startwert, Endwert |
| `chart_negative_days.png` | Anzahl negativer Tage pro Jahrgang |
| `chart_heatmap.png` | Jahrgang × Monat, mittlerer Spread |
| `dashboard.html` | Interaktive Version (Plotly, offline lauffähig) |
| `report.md` / `report.html` | Bericht mit Kernaussagen, Methodik, Tabelle, Charts |
| `data_validation.csv`, `expiry_check.csv` | Datenqualität: fehlende Ticker, Verfallsdaten vs. Erwartung |

## Projektstruktur

    bloomberg/          Excel-Vorlage mit BDH-Formeln, API-Pull-Skript, Anleitung
    data/raw/           Bloomberg-Export hier ablegen
    data/processed/     normalisierte Preise (long format)
    src/spread/         config (Datenvertrag), load, vintages, stats, charts, report
    tests/              synthetische Daten + Pipeline-Tests (py tests/test_pipeline.py)
    output/             Ergebnisse
    run_analysis.py     ein Befehl für alles

## Konventionen und Fallstricke

- **Einheiten:** Bloomberg liefert XB und HO in US‑Cent/Gallone. Der Loader erkennt das und rechnet in $/gal um, der Spread wird mit 42 in $/bbl ausgedrückt.
- **Fensterende:** wird aus den Daten genommen (letzter Tag mit Preis für beide Jun-Beine), nicht aus einem Kalender. `expiry_check.csv` vergleicht mit dem erwarteten letzten Mai-Geschäftstag.
- **Nur vollständige Tage:** Ein Tag geht nur in die Kette ein, wenn alle vier Beine einen Preis haben.
- **Weit entfernte Kontrakte:** Am Fensterbeginn ist Dec 18 Monate entfernt und dünn gehandelt. Settlements dort sind weniger aussagekräftig als ab etwa Januar.
- **Strukturbruch:** Vor dem Mai‑2013‑Kontrakt war HO Heizöl mit bis zu 2.000 ppm Schwefel. Die Analyse beginnt deshalb mit Jahrgang 2013.

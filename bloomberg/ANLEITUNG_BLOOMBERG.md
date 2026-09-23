# Bloomberg-Datenabzug für die RB/HO Jun-vs-Dec Spread-Analyse

## 1. Ziel

Am Ende muss auf dem Analyse-PC **eine** dieser Dateien liegen:

- `data/raw/bloomberg_export.xlsx` (Weg A oder C) **oder**
- `data/raw/bloomberg_export.csv` (Weg B)

Inhalt: Tagesschlusskurse (`PX_LAST`) für **60 Ticker** vom **01.06.2012 bis heute**:
NYMEX RBOB Gasoline (Bloomberg-Root `XB`) und NY Harbor ULSD (Root `HO`), jeweils die
Juni- und Dezember-Kontrakte der Jahrgänge 2013 bis 2027 (`XBM13 … XBM27`, `XBZ13 … XBZ27`,
`HOM13 … HOM27`, `HOZ13 … HOZ27`, jeweils mit Suffix ` Comdty`). Die vollständige Liste
steht im Blatt **Tickerliste** der Vorlage.

**Einheit:** Bloomberg notiert XB und HO in **US-Cent je Gallone**. Die Werte sehen also aus
wie `245.30`, nicht `2.4530`. Das ist richtig so. **Nicht umrechnen**, der Loader normalisiert.

**Zwei Tickerformen:** Abgelaufene Kontrakte gibt es nur in der zweistelligen Jahresform
(`XBM13 Comdty`). Noch laufende Kontrakte (Dez 2026, Jun 2027, Dez 2027) lösen manchmal nur
in der einstelligen Form auf (`XBZ6 Comdty`, `XBM7 Comdty`, `XBZ7 Comdty`). Immer zuerst
zweistellig versuchen, nur bei `#N/A` / „Invalid security“ auf die einstellige Form
ausweichen (Spalte B der Tickerliste). Die einstellige Form zeigt immer auf den *nächsten*
Kontrakt mit dieser Endziffer, für alte Jahrgänge also auf das falsche Jahrzehnt.

## 2. Weg 0 (am einfachsten): ein Klick

Wenn auf dem Bloomberg-PC Python laufen darf, ist das der kuerzeste Weg:

1. Das Projekt auf den Bloomberg-PC bringen (`git clone`, oder auf GitHub
   **Code → Download ZIP**).
2. **`ANALYSE_STARTEN.bat` doppelklicken.**

Das Skript prueft Python und die noetigen Pakete und installiert Fehlendes nach
(Benutzerinstallation, keine Adminrechte). Danach holt es ueber die
Desktop-API **nur die Tage, die noch fehlen**, rechnet die Analyse und oeffnet
den Bericht. Dauer beim ersten Mal ein paar Minuten, danach Sekunden.

Der Zeitstempel jedes Laufs steht in `output/last_run.txt` und in der Kopfzeile
von Bericht und Dashboard.

Geht das nicht - keine Installationsrechte, kein Python, API gesperrt -, dann
Weg A. Das Skript sagt es selbst und nennt die Alternative.

### Tageslimit beachten

Bloomberg zaehlt jeden Datenpunkt gegen ein Tageslimit. Der erste vollstaendige
Abzug sind rund 40.000 Punkte; danach reichen wenige Dutzend pro Lauf, weil
abgelaufene Kontrakte aus dem Cache kommen und nicht erneut abgefragt werden.

Wichtig bei Weg A: **jedes Strg+Alt+F9 fragt alle Ticker des Blattes erneut ab.**
Mehrfaches Neuberechnen beim Herumprobieren ist die haeufigste Ursache fuer
`#N/A Daily Capacity`. Deshalb: einmal rechnen lassen, warten, dann sofort in
Werte umwandeln.

Ist das Limit erschoepft, hilft nur warten (es laeuft taeglich neu an) oder der
Help Desk (zweimal **F1**). Fuer einen einmaligen historischen Abzug heben sie
das Limit oft an. In der Zwischenzeit mit Teil-Vorlagen arbeiten:
`py bloomberg/make_batches.py` erzeugt sie in `bloomberg/batches/`.

## 3. Weg A: Excel-Vorlage mit dem Bloomberg-Add-in

1. `bloomberg/bloomberg_pull_template.xlsx` auf den Bloomberg-PC kopieren (USB, Mail, Laufwerk).
2. Am Terminal anmelden. Die Datei in Excel öffnen; der Menüband-Reiter **Bloomberg** muss
   sichtbar sein (sonst Add-in laden: Datei → Optionen → Add-Ins → COM-Add-Ins → Bloomberg Excel Add-in).
3. Blatt **Daten**: In Zeile 1 stehen die 60 Ticker (jede zweite Spalte), in Zeile 2 die
   `BDH`-Formeln. Zeigen die Zellen `#NAME?` oder bleiben leer: **Strg+Alt+F9** (vollständige
   Neuberechnung) drücken und warten, bis die Bloomberg-Anzeige in der Excel-Statusleiste
   („Requesting data …“) nichts mehr lädt. Das kann ein bis zwei Minuten dauern.
4. Ergebnis prüfen: Jede Tickerspalte läuft mehrere hundert Zeilen nach unten
   (Datum links, Kurs rechts daneben). Abgelaufene Kontrakte haben ca. 600–750 Zeilen
   (sie handeln etwa drei Jahre); die 2013er Kontrakte weniger, weil der Abzug erst am
   01.06.2012 beginnt.
5. Liefert ein noch laufender Kontrakt nur `#N/A` oder „Invalid security“: den Tickertext in
   **Zeile 1** durch die einstellige Form aus Blatt **Tickerliste** (Spalte B) ersetzen, z. B.
   `XBZ26 Comdty` → `XBZ6 Comdty`. Danach erneut Strg+Alt+F9. Betroffen sind höchstens
   die sechs Ticker `XBZ26`, `HOZ26`, `XBM27`, `HOM27`, `XBZ27`, `HOZ27`.
6. **Formeln in Werte umwandeln (Pflicht):** Blatt Daten, **Strg+A**, **Strg+C**, Rechtsklick →
   **Inhalte einfügen → Werte** (bzw. Einfügen-Symbol „123“). Ohne diesen Schritt enthält die
   Datei auf einem PC ohne Bloomberg nur leere Zellen oder `#NAME?`.
7. **Speichern unter** `bloomberg_export.xlsx` (Dateityp „Excel-Arbeitsmappe (.xlsx)“, nicht .xlsm/.xls).
8. Datei auf den Analyse-PC in den Projektordner **`data/raw/`** kopieren. Fertig.

## 4. Weg B: Python-Skript über die Desktop-API

Voraussetzung: Auf dem Bloomberg-PC ist Python installiert und das Terminal läuft angemeldet.

1. Den **gesamten Projektordner** auf den Bloomberg-PC kopieren (das Skript liest
   `src/spread/config.py`).
2. Einmalig die API-Pakete installieren:
   ```
   py -m pip install blpapi --index-url https://blpapi.bloomberg.com/repository/releases/python/simple/
   py -m pip install xbbg
   ```
   (`xbbg` ist optional; ohne `xbbg` nutzt das Skript `blpapi` direkt.)
3. Im Projektordner ausführen:
   ```
   py bloomberg/pull_bloomberg_api.py
   ```
   Optionen: `--start 2012-06-01`, `--end 2026-09-22`, `--out-dir <Ordner>`, `--dry-run`
   (zeigt nur den Tickerplan, braucht kein Bloomberg).
4. Das Skript versucht jeden Ticker zuerst zweistellig, dann einstellig, prüft bei der
   einstelligen Form das Verfallsdatum (`LAST_TRADEABLE_DT`) und druckt am Ende eine
   Tabelle je Ticker (Zeilen, erster/letzter Tag, min/max).
5. Ergebnis: `data/raw/bloomberg_export.csv` (breit, eine Datumsspalte) und
   `data/raw/bloomberg_export_long.csv` (lang). Beide in **US-Cent je Gallone**, unverändert.
6. `data/raw/bloomberg_export.csv` auf den Analyse-PC nach `data/raw/` kopieren.

## 5. Weg C (Ersatz): Bloomberg Spreadsheet Builder

1. Vorlage öffnen, Reiter **Bloomberg → Spreadsheet Builder → Historical Data Table**.
2. Wertpapiere: „From spreadsheet“ → Bereich `Tickerliste!A2:A61`. Für die noch laufenden
   Kontrakte bei Bedarf die einstellige Form aus Spalte B nehmen.
3. Feld: `PX_LAST`. Zeitraum: **01.06.2012** bis heute, Periodizität **Daily**,
   Nicht-Handelstage **nicht** auffüllen (Option „Exclude non-trading days“ bzw. Fill: none).
4. Layout ist egal: sowohl „eine gemeinsame Datumsspalte“ als auch „Datum je Wertpapier“
   wird vom Loader akzeptiert.
5. Danach wie in Weg A: Strg+A, Strg+C, Inhalte einfügen → Werte; speichern als
   `bloomberg_export.xlsx`; nach `data/raw/` auf den Analyse-PC kopieren.

## 6. Plausibilitäts-Checks vor dem Kopieren

- **60 Ticker** vorhanden (Zeile 1 im Blatt Daten bzw. Spaltenköpfe).
- Abgelaufene **Jun-Kontrakte enden am letzten Geschäftstag im Mai** des Jahrgangs,
  abgelaufene **Dez-Kontrakte am letzten Geschäftstag im November** (Spalte D der Tickerliste
  nennt das erwartete Datum). Endet eine Reihe deutlich früher, war es der falsche Kontrakt.
- Kurse liegen grob zwischen **40 und 450** (US-Cent/Gallone). Werte um 2–5 wären $/gal
  (ungewöhnlich, aber der Loader erkennt es); Werte um 100.000 wären falsch.
- Erste Zeile jeder Reihe: 01.06.2012 für die 2013/2014-Kontrakte, sonst der erste
  Handelstag des Kontrakts (etwa drei Jahre vor Verfall).
- Keine Formeln mehr in der Datei (Zelle A2 anklicken: in der Bearbeitungsleiste muss ein
  Datum stehen, kein `=BDH(...)`).

## 7. Häufige Fehler

| Symptom | Ursache / Lösung |
|---|---|
| Datei ist auf dem Analyse-PC leer oder voller `#NAME?` | Formeln wurden nicht in Werte umgewandelt (Schritt A6). Auf dem Bloomberg-PC wiederholen. |
| `#N/A Invalid security` bei Dez 2026 / 2027-Kontrakten | Einstellige Tickerform verwenden (Tickerliste, Spalte B). |
| Reihe endet Jahre zu früh oder beginnt in der Zukunft | Falsche Tickerform: einstellige Form für einen abgelaufenen Jahrgang zeigt auf das falsche Jahrzehnt. Zweistellig nehmen. |
| `#N/A Daily Capacity` | Tageslimit fuer Datenabrufe erschoepft - kein Datei- oder Formelfehler. Nicht weiter neu berechnen (das verbraucht weiter). Bereits gefuellte Spalten sofort in Werte umwandeln und sichern, am Folgetag blockweise nachholen (`py bloomberg/make_batches.py`). Help Desk: zweimal F1. |
| `#N/A Requesting Data…` bleibt stehen | Add-in lädt noch; warten, dann Strg+Alt+F9. Notfalls Excel neu starten. |
| Datumsfehler / falscher Zeitraum | In den Formeln stehen deshalb `YYYYMMDD`-Strings (`"20120601"`), die vom Excel-Gebietsschema unabhängig sind. Formeln nicht mit `01.06.2012` o. ä. überschreiben. |
| Ticker lautet `XBM13 COMB Comdty` statt `XBM13 Comdty` | Beides ist in Ordnung (`COMB` = kombinierte Sitzung), der Loader normalisiert. |
| Nur jede zweite Spalte gefüllt | Normal: BDH schreibt Datum in die Tickerspalte und den Kurs in die Spalte rechts daneben. |
| Werte wie `2.45` statt `245.30` | Terminal-Einstellung (`PDF`-Preisdefaults) auf $/gal. Kein Problem, der Loader erkennt die Einheit; bitte im Bericht erwähnen. |

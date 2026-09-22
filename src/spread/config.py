"""
Zentrale Konfiguration und Datenvertrag fuer die RB/HO Jun-vs-Dec Spread-Analyse.

Alle Module (load, vintages, stats, charts, report) importieren von hier.
Nichts hier drin darf von einem einzelnen Modul "umdefiniert" werden.

KONZEPT
-------
Spread eines Jahrgangs Y (in $/bbl):

    spread_bbl = [(RB_JunY - HO_JunY) - (RB_DecY - HO_DecY)] * 42

wobei RB = NYMEX RBOB Gasoline (Bloomberg-Root "XB"), HO = NYMEX NY Harbor ULSD
(Bloomberg-Root "HO"), beide in $/gal notiert, 42.000 gal = 1.000 bbl pro Kontrakt.

Fenster eines Jahrgangs Y: 1. Juni (Y-1) bis zum letzten Handelstag des Jun-Kontrakts
(= letzter Geschaeftstag im Mai Y, CME Rulebook Ch. 191 / 150). Danach existiert das
Jun-Bein nicht mehr, der Jahrgang ist beendet. Die Fenster ueberlappen nicht, die
Kette ist lueckenlos.

Erster Jahrgang: 2013 (ab dem Mai-2013-Kontrakt ist HO = ULSD < 15 ppm; Handel der
ULSD-Spezifikation begann am 29.04.2012, das Fenster ab 01.06.2012 ist also komplett ULSD).
Letzter abgeschlossener Jahrgang: 2026. Jahrgang 2027 laeuft (seit 01.06.2026).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# ----------------------------------------------------------------------------
# Pfade
# ----------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "output"
BLOOMBERG_DIR = PROJECT_ROOT / "bloomberg"

# Erwarteter Dateiname des Bloomberg-Exports (kann per CLI ueberschrieben werden)
DEFAULT_RAW_FILE = DATA_RAW / "bloomberg_export.xlsx"
PRICES_LONG_FILE = DATA_PROCESSED / "prices_long.csv"
SPREAD_DAILY_FILE = OUTPUT_DIR / "spread_daily.csv"
VINTAGE_STATS_FILE = OUTPUT_DIR / "vintage_stats.csv"
SEASONAL_MATRIX_FILE = OUTPUT_DIR / "seasonal_matrix.csv"

# ----------------------------------------------------------------------------
# Kontrakt-Konstanten
# ----------------------------------------------------------------------------
GALLONS_PER_BBL = 42.0

# Bloomberg-Root -> interner Produktcode
BBG_ROOT_TO_PRODUCT = {"XB": "RB", "HO": "HO"}
PRODUCT_TO_BBG_ROOT = {v: k for k, v in BBG_ROOT_TO_PRODUCT.items()}
PRODUCTS = ("RB", "HO")

# Futures-Monatscodes
MONTH_CODES = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
LEG_MONTHS = {"jun": "M", "dec": "Z"}          # die beiden Beine des Spreads
LEG_CODES = tuple(LEG_MONTHS.values())          # ("M", "Z")

# Die vier Beine in fester Reihenfolge; das ist auch die Spaltenreihenfolge in SPREAD_DAILY
LEGS = (
    ("RB", "M", "rb_jun"),
    ("HO", "M", "ho_jun"),
    ("RB", "Z", "rb_dec"),
    ("HO", "Z", "ho_dec"),
)

# ----------------------------------------------------------------------------
# Jahrgaenge und Fenster
# ----------------------------------------------------------------------------
FIRST_VINTAGE = 2013


def vintage_for_date(d: date) -> int:
    """Jahrgang, dessen Fenster das Datum d enthaelt: ab 1. Juni gehoert d zum Folgejahr."""
    return d.year + 1 if d.month >= 6 else d.year


def _as_of() -> date:
    """Stichtag: heute, oder Umgebungsvariable SPREAD_AS_OF=YYYY-MM-DD (fuer reproduzierbare Laeufe)."""
    import os
    v = os.environ.get("SPREAD_AS_OF")
    return date.fromisoformat(v) if v else date.today()


AS_OF = _as_of()
CURRENT_VINTAGE = vintage_for_date(AS_OF)        # laufender Jahrgang, z.B. 2027 fuer Stichtage 01.06.2026 .. 31.05.2027
LAST_VINTAGE = CURRENT_VINTAGE                   # letzter Jahrgang im Ticker-Universum
VINTAGES = tuple(range(FIRST_VINTAGE, LAST_VINTAGE + 1))

HISTORY_START = date(FIRST_VINTAGE - 1, 6, 1)    # 2012-06-01, Beginn des Bloomberg-Pulls


def window_start(vintage: int) -> date:
    """Nomineller Fensterbeginn: 1. Juni des Vorjahres."""
    return date(vintage - 1, 6, 1)


def window_nominal_end(vintage: int) -> date:
    """Nominelles Fensterende: 31. Mai des Jahrgangs.

    Der tatsaechliche letzte Handelstag ist der letzte NYMEX-Geschaeftstag im Mai
    (Achtung Memorial Day = letzter Montag im Mai). In der Pipeline wird das
    tatsaechliche Ende aus den Daten genommen: letzter Tag, an dem das Jun-Bein
    einen Preis hat. Diese Funktion dient nur als obere Schranke / Plausibilitaetscheck.
    """
    return date(vintage, 5, 31)


# ----------------------------------------------------------------------------
# Bloomberg-Ticker
# ----------------------------------------------------------------------------
def bbg_ticker(product: str, month_code: str, contract_year: int, two_digit: bool = True) -> str:
    """z.B. bbg_ticker("RB","M",2013) -> "XBM13 Comdty"."""
    root = PRODUCT_TO_BBG_ROOT[product]
    yy = f"{contract_year % 100:02d}" if two_digit else f"{contract_year % 10:d}"
    return f"{root}{month_code}{yy} Comdty"


def all_tickers() -> list[str]:
    """Die 60 Ticker (4 Beine x 15 Jahrgaenge) in stabiler Reihenfolge."""
    out = []
    for v in VINTAGES:
        for product, mc, _ in LEGS:
            out.append(bbg_ticker(product, mc, v))
    return out


# ----------------------------------------------------------------------------
# DATENVERTRAG: Spaltenschemas
# ----------------------------------------------------------------------------
# 1) prices_long (Output von load.py) - eine Zeile pro (Datum, Ticker)
PRICES_LONG_COLUMNS = [
    "date",            # datetime64[ns], Handelstag
    "ticker",          # str, normalisiert, z.B. "XBM13 Comdty"
    "product",         # str, "RB" | "HO"
    "month_code",      # str, "M" | "Z"
    "contract_year",   # int, z.B. 2013
    "px",              # float, $/gal (PX_LAST / Settlement)
]

# 2) spread_daily (Output von vintages.py) - durchgehende Kette, eine Zeile pro Handelstag
#    Nur Tage, an denen ALLE vier Beine einen Preis haben (inner join).
SPREAD_DAILY_COLUMNS = [
    "date",                    # datetime64[ns]
    "vintage",                 # int, Jahrgang Y
    "rb_jun", "ho_jun", "rb_dec", "ho_dec",   # float, $/gal
    "crack_jun_bbl",           # float, (rb_jun - ho_jun) * 42
    "crack_dec_bbl",           # float, (rb_dec - ho_dec) * 42
    "spread_bbl",              # float, crack_jun_bbl - crack_dec_bbl   <- DIE Zielgroesse
    "spread_gal",              # float, spread_bbl / 42  (in $/gal, zur Kontrolle)
    "expiry_date",             # datetime64[ns], letzter Handelstag des Jun-Beins in diesem Jahrgang
    "cal_days_to_expiry",      # int, Kalendertage bis expiry_date (0 am letzten Tag)
    "tdays_to_expiry",         # int, verbleibende Handelstage im Fenster (0 am letzten Tag)
    "tday_index",              # int, laufender Handelstag im Fenster (0 am ersten Tag)
    "is_current",              # bool, True nur fuer den laufenden Jahrgang (CURRENT_VINTAGE)
]

# 3) vintage_stats (Output von stats.py) - eine Zeile pro Jahrgang
VINTAGE_STATS_COLUMNS = [
    "vintage",
    "window_start",            # erster Tag mit allen 4 Beinen
    "window_end",              # = expiry_date (bzw. letzter verfuegbarer Tag beim laufenden Jahrgang)
    "is_complete",             # bool, False fuer den laufenden Jahrgang
    "n_days",                  # Anzahl Handelstage mit vollstaendigen Daten
    "first_value",             # spread_bbl am window_start
    "last_value",              # spread_bbl am window_end (bei Verfall)
    "mean", "median", "std",
    "min", "min_date", "tdays_to_expiry_at_min",
    "max", "max_date", "tdays_to_expiry_at_max",
    "n_negative_days", "pct_negative_days",
    "first_negative_date",     # NaT falls nie negativ
    "longest_negative_streak", # in Handelstagen
    "max_drawdown",            # groesster Rueckgang vom laufenden Hoch (>= 0)
    "value_t120", "value_t60", "value_t20",   # spread_bbl bei tdays_to_expiry == 120/60/20 (NaN falls nicht vorhanden)
    "value_jan1",              # spread_bbl am ersten Handelstag im Januar des Jahrgangs
]

# 4) seasonal_matrix (Output von stats.py) - Zeilen = tdays_to_expiry (0..N), Spalten = Jahrgaenge
#    plus Spalten "mean", "median", "min", "max" ueber alle ABGESCHLOSSENEN Jahrgaenge.
SEASONAL_INDEX_NAME = "tdays_to_expiry"
SEASONAL_AGG_COLUMNS = ["mean", "median", "min", "max"]

# ----------------------------------------------------------------------------
# Darstellung
# ----------------------------------------------------------------------------
UNIT_LABEL = "$/bbl"
SPREAD_LABEL = "RB/HO Jun - Dec Crack-Spread"

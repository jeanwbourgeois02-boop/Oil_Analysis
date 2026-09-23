"""
Inkrementeller Preis-Cache.

Zweck: Bloomberg zaehlt jeden abgerufenen Datenpunkt gegen das Tageslimit
("#N/A Daily Capacity"). Ein voller Abzug sind ~40.000 Punkte. Mit Cache holt
jeder weitere Lauf nur die Tage, die noch fehlen - typisch ein paar Dutzend
Punkte fuer die noch laufenden Kontrakte.

Regeln je Kontrakt:
  * abgelaufen und bis zum Verfall im Cache  -> nie wieder abfragen
  * im Cache, aber unvollstaendig            -> ab letztem Cache-Tag + 1
  * nicht im Cache                           -> ab config.HISTORY_START

Cache-Datei: data/raw/price_cache.csv, Spalten exakt config.PRICES_LONG_COLUMNS.

EINHEIT: im Cache stehen Preise in $/gal, also bereits normalisiert. Bloomberg
liefert US-Cent je Gallone; ``to_usd_per_gal`` rechnet beim Einspielen um. Das
ist verlustfrei wiederholbar, weil load.normalize_units nur dann durch 100
teilt, wenn der Median eines Tickers ueber 20 liegt - $/gal-Werte (1..4) bleiben
unberuehrt, Cent-Werte (40..450) werden umgerechnet. Dadurch kann der Cache
direkt als --input an run_analysis.py gehen und Excel-Exporte und API-Abzuege
lassen sich in derselben Datei mischen.

Die Datei liegt unter data/raw/ und ist damit von .gitignore erfasst - sie darf
nicht ins Repository.
"""
from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent / "src") not in sys.path:
    sys.path.insert(0, str(HERE.parent / "src"))

import pandas as pd  # noqa: E402

from spread import calendar_nymex, config  # noqa: E402

log = logging.getLogger("cache")

CACHE_FILE = config.DATA_RAW / "price_cache.csv"

# Ein abgelaufener Kontrakt gilt als vollstaendig, wenn der letzte Cache-Tag
# hoechstens so viele Handelstage vor dem erwarteten Verfall liegt. Ohne diese
# Toleranz wuerde ein fehlender Schlusstag jeden Lauf eine neue Anfrage ausloesen.
EXPIRY_TOLERANCE_TDAYS = 1


@dataclass
class Need:
    """Was fuer einen Ticker noch zu holen ist."""
    ticker: str
    start: date | None          # None = nichts zu tun
    reason: str                 # "vollstaendig" | "neu" | "fortschreiben"
    cached_rows: int
    cached_last: date | None


def load(path: Path | None = None) -> pd.DataFrame:
    """Cache lesen; leerer DataFrame mit korrektem Schema, wenn es ihn nicht gibt."""
    path = Path(path) if path is not None else CACHE_FILE
    if not path.exists():
        return pd.DataFrame({c: pd.Series(dtype=object) for c in config.PRICES_LONG_COLUMNS})
    df = pd.read_csv(path, dtype={"ticker": "str", "product": "str", "month_code": "str"})
    missing = [c for c in config.PRICES_LONG_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"Cache {path} fehlen Spalten: {', '.join(missing)}")
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
    df["px"] = pd.to_numeric(df["px"], errors="coerce").astype("float64")
    df["contract_year"] = pd.to_numeric(df["contract_year"], errors="coerce").astype("int64")
    df = df[df["px"].notna() & df["date"].notna()]
    return df[config.PRICES_LONG_COLUMNS].reset_index(drop=True)


def to_usd_per_gal(rows: pd.DataFrame) -> pd.DataFrame:
    """Rohzeilen auf die Cache-Einheit $/gal bringen (idempotent, siehe Modulkopf)."""
    from spread import load as _load
    if rows is None or rows.empty:
        return rows
    return _load.normalize_units(rows[config.PRICES_LONG_COLUMNS])


def last_dates(cache: pd.DataFrame) -> dict[str, date]:
    if cache.empty:
        return {}
    g = cache.groupby("ticker")["date"].max()
    return {str(t): d.date() for t, d in g.items()}


def row_counts(cache: pd.DataFrame) -> dict[str, int]:
    if cache.empty:
        return {}
    return {str(t): int(n) for t, n in cache.groupby("ticker").size().items()}


def _is_complete(cached_last: date | None, expiry: date, today: date) -> bool:
    if cached_last is None or expiry >= today:
        return False
    return calendar_nymex.trading_days_between(cached_last, expiry) <= EXPIRY_TOLERANCE_TDAYS


def needs(tickers: list[tuple[str, int, str]], cache: pd.DataFrame,
          today: date | None = None) -> list[Need]:
    """Bedarf je Ticker. ``tickers`` = [(ticker, contract_year, month_code), ...]."""
    today = today or date.today()
    last = last_dates(cache)
    counts = row_counts(cache)
    out = []
    for ticker, contract_year, month_code in tickers:
        cached_last = last.get(ticker)
        n = counts.get(ticker, 0)
        expiry = calendar_nymex.expected_expiry(contract_year, month_code)
        if _is_complete(cached_last, expiry, today):
            out.append(Need(ticker, None, "vollstaendig", n, cached_last))
        elif cached_last is None:
            out.append(Need(ticker, config.HISTORY_START, "neu", 0, None))
        else:
            out.append(Need(ticker, cached_last + timedelta(days=1), "fortschreiben", n, cached_last))
    return out


def merge(cache: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    """Neue Zeilen einspielen; bei gleichem (ticker, date) gewinnt die neue Zeile."""
    if new_rows is None or new_rows.empty:
        return cache
    new_rows = new_rows[config.PRICES_LONG_COLUMNS].copy()
    new_rows["date"] = pd.to_datetime(new_rows["date"]).astype("datetime64[ns]")
    both = pd.concat([cache, new_rows], ignore_index=True) if not cache.empty else new_rows
    both = both[both["px"].notna() & (both["px"] > 0)]
    before = len(both)
    both = both.drop_duplicates(["ticker", "date"], keep="last")
    log.info("Cache: %d neue Zeilen, %d Duplikate ersetzt, %d Zeilen gesamt",
             len(new_rows), before - len(both), len(both))
    return both.sort_values(["ticker", "date"], kind="stable").reset_index(drop=True)


def save(cache: pd.DataFrame, path: Path | None = None) -> Path:
    path = Path(path) if path is not None else CACHE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    out = cache.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out[config.PRICES_LONG_COLUMNS].to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def summary(cache: pd.DataFrame) -> str:
    if cache.empty:
        return "Cache leer"
    n_t = cache["ticker"].nunique()
    return (f"{len(cache)} Kurszeilen, {n_t}/{len(config.all_tickers())} Ticker, "
            f"{cache['date'].min().date()} bis {cache['date'].max().date()}")

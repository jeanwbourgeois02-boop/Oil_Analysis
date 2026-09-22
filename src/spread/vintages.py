"""
Build the daily vintage chain ``spread_daily`` from ``prices_long``.

For every vintage v in ``config.VINTAGES`` the four legs (RB Jun, HO Jun,
RB Dec, HO Dec of contract year v) are pivoted wide, cut to the nominal window
``config.window_start(v) .. config.window_nominal_end(v)`` and inner-joined
(only days on which all four legs have a price).

expiry_date
    Completed vintages: last date in the window on which BOTH June legs have a
    price. This is determined BEFORE the inner join, so a missing December
    quote on the last day cannot shift the expiry.
    Current vintage (``config.CURRENT_VINTAGE``): last available complete day,
    ``is_current=True``. Its ``tdays_to_expiry`` is therefore provisional
    (0 on the last available day) - use ``stats.seasonal_matrix`` (calendar
    aligned) or ``tday_index`` to overlay it on the completed vintages.

Output: exactly ``config.SPREAD_DAILY_COLUMNS``, one row per trading day, all
vintages concatenated in date order; dates are unique and vintage windows do
not overlap (both verified, raising AssertionError otherwise).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from spread import calendar_nymex, config
except ImportError:  # executed as a script: put src/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from spread import calendar_nymex, config

log = logging.getLogger(__name__)

LEG_COLUMNS = [col for _, _, col in config.LEGS]


def _build_vintage(px: pd.DataFrame, vintage: int, is_current: bool) -> pd.DataFrame | None:
    wide: pd.DataFrame | None = None
    for product, mc, col in config.LEGS:
        ticker = config.bbg_ticker(product, mc, vintage)
        leg = px.loc[px["ticker"] == ticker, ["date", "px"]].rename(columns={"px": col})
        if leg.empty:
            log.warning("vintage %d: leg %s (%s) entirely missing -> vintage skipped", vintage, col, ticker)
            return None
        wide = leg if wide is None else wide.merge(leg, on="date", how="outer")
    assert wide is not None
    start = pd.Timestamp(config.window_start(vintage))
    end = pd.Timestamp(config.window_nominal_end(vintage))
    wide = wide[(wide["date"] >= start) & (wide["date"] <= end)].sort_values("date").reset_index(drop=True)
    if wide.empty:
        log.warning("vintage %d: no prices inside the window %s..%s -> skipped", vintage, start.date(), end.date())
        return None
    jun_ok = wide["rb_jun"].notna() & wide["ho_jun"].notna()
    if not jun_ok.any():
        log.warning("vintage %d: June legs never quoted together inside the window -> skipped", vintage)
        return None
    complete = wide.dropna(subset=LEG_COLUMNS).reset_index(drop=True)
    if complete.empty:
        log.warning("vintage %d: no day with all four legs inside the window -> skipped", vintage)
        return None
    n_dropped = len(wide) - len(complete)

    if is_current:
        expiry = complete["date"].iloc[-1]
    else:
        expiry = wide.loc[jun_ok, "date"].max()
        if complete["date"].iloc[-1] != expiry:
            log.warning("vintage %d: last complete day %s precedes the June-leg expiry %s "
                        "(December quote missing on the expiry day)", vintage,
                        complete["date"].iloc[-1].date(), expiry.date())

    g = config.GALLONS_PER_BBL
    out = complete.copy()
    out["vintage"] = int(vintage)
    out["crack_jun_bbl"] = (out["rb_jun"] - out["ho_jun"]) * g
    out["crack_dec_bbl"] = (out["rb_dec"] - out["ho_dec"]) * g
    out["spread_bbl"] = out["crack_jun_bbl"] - out["crack_dec_bbl"]
    out["spread_gal"] = out["spread_bbl"] / g
    out["expiry_date"] = pd.Timestamp(expiry)
    n = len(out)
    out["tday_index"] = np.arange(n, dtype="int64")
    out["tdays_to_expiry"] = (n - 1 - out["tday_index"]).astype("int64")
    out["cal_days_to_expiry"] = (out["expiry_date"] - out["date"]).dt.days.astype("int64")
    out["is_current"] = bool(is_current)
    log.info("vintage %d%s: %d trading days %s .. %s, expiry %s, %d day(s) dropped by the inner join",
             vintage, " (current)" if is_current else "", n, out["date"].iloc[0].date(),
             out["date"].iloc[-1].date(), pd.Timestamp(expiry).date(), n_dropped)
    return out[config.SPREAD_DAILY_COLUMNS]


def _check_chain(df: pd.DataFrame) -> None:
    if not df["date"].is_unique:
        dups = df.loc[df["date"].duplicated(), "date"].dt.date.unique()[:5].tolist()
        raise AssertionError(f"spread_daily: duplicate dates across vintages, e.g. {dups}")
    span = df.groupby("vintage")["date"].agg(["min", "max"]).sort_index()
    prev_v, prev_max = None, None
    for v, (dmin, dmax) in span.iterrows():
        if prev_max is not None and dmin <= prev_max:
            raise AssertionError(f"spread_daily: vintage {v} starts {dmin.date()} before vintage {prev_v} ends {prev_max.date()}")
        prev_v, prev_max = v, dmax
    if not df["date"].is_monotonic_increasing:
        raise AssertionError("spread_daily: dates not monotonic after concatenation")


def build_spread_daily(prices_long: pd.DataFrame, vintages=config.VINTAGES,
                       current_vintage: int | None = config.CURRENT_VINTAGE) -> pd.DataFrame:
    """Concatenate all vintages into the daily chain (see module docstring)."""
    missing = {"date", "ticker", "px"} - set(prices_long.columns)
    if missing:
        raise ValueError(f"prices_long lacks columns {sorted(missing)}")
    px = prices_long[["date", "ticker", "px"]].copy()
    px["date"] = pd.to_datetime(px["date"]).astype("datetime64[ns]")
    px["px"] = px["px"].astype("float64")
    parts = []
    for v in vintages:
        part = _build_vintage(px, int(v), int(v) == current_vintage)
        if part is not None:
            parts.append(part)
    if not parts:
        raise ValueError("no vintage could be built from prices_long")
    out = pd.concat(parts, ignore_index=True)
    _check_chain(out)
    out = out.sort_values("date").reset_index(drop=True)
    out["date"] = out["date"].astype("datetime64[ns]")
    out["expiry_date"] = out["expiry_date"].astype("datetime64[ns]")
    out["vintage"] = out["vintage"].astype("int64")
    out["is_current"] = out["is_current"].astype(bool)
    log.info("spread_daily: %d rows, %d vintages, %s .. %s", len(out), out["vintage"].nunique(),
             out["date"].iloc[0].date(), out["date"].iloc[-1].date())
    return out[config.SPREAD_DAILY_COLUMNS]


def expected_expiry_check(spread_daily: pd.DataFrame) -> pd.DataFrame:
    """Compare each completed vintage's expiry_date with ``calendar_nymex.expected_expiry(v, "M")``.

    Columns: vintage, expiry_date, expected_expiry, matches, gap_bdays (signed
    trading days actual - expected).
    """
    rows = []
    done = spread_daily[~spread_daily["is_current"]]
    for v, g in done.groupby("vintage"):
        actual = pd.Timestamp(g["expiry_date"].iloc[0])
        exp = calendar_nymex.expected_expiry(int(v), config.LEG_MONTHS["jun"])
        rows.append({"vintage": int(v), "expiry_date": actual, "expected_expiry": pd.Timestamp(exp),
                     "matches": actual.date() == exp, "gap_bdays": calendar_nymex.trading_days_between(exp, actual)})
    rep = pd.DataFrame(rows, columns=["vintage", "expiry_date", "expected_expiry", "matches", "gap_bdays"])
    bad = rep[~rep["matches"]] if len(rep) else rep
    if len(bad):
        log.warning("expiry check: %d vintage(s) deviate from the CME rule: %s", len(bad),
                    ", ".join(f"{r.vintage}: {r.expiry_date.date()} vs {r.expected_expiry.date()}" for r in bad.itertuples()))
    return rep


def read_spread_daily(path: str | Path = config.SPREAD_DAILY_FILE) -> pd.DataFrame:
    """Read a spread_daily CSV back with the contract dtypes."""
    df = pd.read_csv(path)
    for c in ("date", "expiry_date"):
        df[c] = pd.to_datetime(df[c], format="ISO8601").astype("datetime64[ns]")
    for c in ("vintage", "cal_days_to_expiry", "tdays_to_expiry", "tday_index"):
        df[c] = df[c].astype("int64")
    df["is_current"] = df["is_current"].astype(str).str.lower().isin(["true", "1"])
    return df[config.SPREAD_DAILY_COLUMNS]


def main(argv: list[str] | None = None) -> int:
    """Usage: py src/spread/vintages.py [prices_long.csv] [spread_daily.csv]"""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from spread import load  # local import: keeps module import light
    args = sys.argv[1:] if argv is None else argv
    src = Path(args[0]) if args else config.PRICES_LONG_FILE
    dst = Path(args[1]) if len(args) > 1 else config.SPREAD_DAILY_FILE
    prices = load.read_prices_long(src)
    sd = build_spread_daily(prices)
    chk = expected_expiry_check(sd)
    print(chk.to_string(index=False))
    dst.parent.mkdir(parents=True, exist_ok=True)
    sd.to_csv(dst, index=False, date_format="%Y-%m-%d", lineterminator="\n")
    print(f"wrote {len(sd)} rows -> {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

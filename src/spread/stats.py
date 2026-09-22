"""
Statistics on the daily vintage chain ``spread_daily``.

* ``vintage_stats``          one row per vintage, exactly ``config.VINTAGE_STATS_COLUMNS``
* ``seasonal_matrix``        rows = tdays_to_expiry, columns = vintages (+ mean/median/min/max
                             over COMPLETE vintages)
* ``seasonal_matrix_by_tday_index``  same, rows = tday_index (days since window start)
* ``seasonal_tables``        both matrices in a dict
* ``overall_summary``        JSON-serialisable dict of headline numbers

Which x-axis for the running vintage?
-------------------------------------
For completed vintages ``tdays_to_expiry`` is exact. For the current vintage
(``config.CURRENT_VINTAGE``) ``spread_daily.tdays_to_expiry`` is only
provisional (0 on the last available day), because ``vintages.py`` sets its
expiry_date to the last available date. Two ways to overlay it:

1. ``seasonal_matrix(..., current_alignment="calendar")`` (default): the
   current vintage's column is positioned at the ESTIMATED remaining trading
   days, counted with the NYMEX calendar to ``calendar_nymex.expected_expiry
   (v, "M")``. Exact unless an unscheduled closure happens before expiry.
2. ``seasonal_matrix_by_tday_index``: everything indexed by days since the
   window start, which needs no expiry knowledge at all.

The aggregate columns always use completed vintages only.
"""
from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from spread import calendar_nymex, config
except ImportError:  # executed as a script: put src/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from spread import calendar_nymex, config

log = logging.getLogger(__name__)

DATE_STAT_COLUMNS = ("window_start", "window_end", "min_date", "max_date", "first_negative_date")
INT_STAT_COLUMNS = ("vintage", "n_days", "tdays_to_expiry_at_min", "tdays_to_expiry_at_max",
                    "n_negative_days", "longest_negative_streak")
CHECKPOINT_TDAYS = {"value_t120": 120, "value_t60": 60, "value_t20": 20}


# ----------------------------------------------------------------------------
# per-vintage statistics
# ----------------------------------------------------------------------------
def _longest_true_run(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        if cur > best:
            best = cur
    return int(best)


def _vintage_row(g: pd.DataFrame) -> dict:
    g = g.sort_values("date").reset_index(drop=True)
    v = int(g["vintage"].iloc[0])
    s = g["spread_bbl"].astype("float64")
    is_complete = not bool(g["is_current"].any())
    imin, imax = int(s.idxmin()), int(s.idxmax())
    neg = (s < 0).to_numpy()
    n_neg = int(neg.sum())

    def at_tdays(k: int) -> float:
        # provisional tdays_to_expiry of the running vintage are meaningless here -> NaN
        if not is_complete:
            return np.nan
        m = g["tdays_to_expiry"] == k
        return float(s[m].iloc[0]) if m.any() else np.nan

    jan = g[(g["date"].dt.year == v) & (g["date"].dt.month == 1)]
    row = {
        "vintage": v,
        "window_start": g["date"].iloc[0],
        "window_end": g["date"].iloc[-1],
        "is_complete": is_complete,
        "n_days": int(len(g)),
        "first_value": float(s.iloc[0]),
        "last_value": float(s.iloc[-1]),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "std": float(s.std()) if len(s) > 1 else np.nan,
        "min": float(s.iloc[imin]),
        "min_date": g["date"].iloc[imin],
        "tdays_to_expiry_at_min": int(g["tdays_to_expiry"].iloc[imin]),
        "max": float(s.iloc[imax]),
        "max_date": g["date"].iloc[imax],
        "tdays_to_expiry_at_max": int(g["tdays_to_expiry"].iloc[imax]),
        "n_negative_days": n_neg,
        "pct_negative_days": 100.0 * n_neg / len(g),
        "first_negative_date": g["date"].iloc[int(neg.argmax())] if n_neg else pd.NaT,
        "longest_negative_streak": _longest_true_run(neg),
        "max_drawdown": float((s.cummax() - s).max()),
        "value_jan1": float(jan["spread_bbl"].iloc[0]) if len(jan) else np.nan,
    }
    for col, k in CHECKPOINT_TDAYS.items():
        row[col] = at_tdays(k)
    return row


def vintage_stats(spread_daily: pd.DataFrame) -> pd.DataFrame:
    """One row per vintage present in ``spread_daily``; columns = ``config.VINTAGE_STATS_COLUMNS``.

    Definitions: first/last_value at window start/end; min/max with dates and
    the tdays_to_expiry at which they occurred; n_negative_days = count of
    spread_bbl < 0 and pct_negative_days in percent; first_negative_date NaT if
    never negative; longest_negative_streak in consecutive trading days;
    max_drawdown = max over t of (running max - value) >= 0; value_t120/60/20 =
    spread_bbl at tdays_to_expiry == 120/60/20 (NaN if absent, and NaN for the
    running vintage whose tdays_to_expiry is provisional); value_jan1 =
    spread_bbl on the first trading day in January of year v (NaN if absent);
    is_complete = not is_current.
    """
    rows = [_vintage_row(g) for _, g in spread_daily.groupby("vintage", sort=True)]
    out = pd.DataFrame(rows, columns=config.VINTAGE_STATS_COLUMNS)
    for c in DATE_STAT_COLUMNS:
        out[c] = pd.to_datetime(out[c]).astype("datetime64[ns]")
    for c in INT_STAT_COLUMNS:
        out[c] = out[c].astype("int64")
    out["is_complete"] = out["is_complete"].astype(bool)
    log.info("vintage_stats: %d vintages, %d complete, %d with negative days", len(out),
             int(out["is_complete"].sum()), int((out["n_negative_days"] > 0).sum()))
    return out[config.VINTAGE_STATS_COLUMNS]


# ----------------------------------------------------------------------------
# seasonal matrices
# ----------------------------------------------------------------------------
def _assemble(cols: dict[int, pd.Series], complete: list[int], index_name: str) -> pd.DataFrame:
    mat = pd.DataFrame(cols) if cols else pd.DataFrame()
    top = int(mat.index.max()) + 1 if len(mat) else 0
    mat = mat.reindex(pd.RangeIndex(0, top))
    mat = mat[sorted(mat.columns)]
    mat.index.name = index_name
    sub = mat[[v for v in complete if v in mat.columns]]
    for name in config.SEASONAL_AGG_COLUMNS:
        mat[name] = getattr(sub, name)(axis=1, skipna=True) if sub.shape[1] else np.nan
    return mat


def _series_by(g: pd.DataFrame, idx) -> pd.Series:
    ser = pd.Series(g["spread_bbl"].to_numpy(dtype="float64"), index=pd.Index(idx, dtype="int64"))
    return ser[~ser.index.duplicated(keep="last")].sort_index()


def estimated_tdays_to_expiry(dates: pd.Series, vintage: int) -> list[int]:
    """Trading days from each date to the CME-rule expiry of vintage's June contract (calendar based)."""
    exp = calendar_nymex.expected_expiry(int(vintage), config.LEG_MONTHS["jun"])
    dd = pd.to_datetime(dates).dt.date.tolist()
    days = calendar_nymex.trading_days(min(dd), max(max(dd), exp))
    return [calendar_nymex.tdays_to(d, exp, days) for d in dd]


def seasonal_matrix(spread_daily: pd.DataFrame, current_alignment: str = "calendar") -> pd.DataFrame:
    """Matrix rows = ``tdays_to_expiry`` (0..max ascending), columns = vintages (int labels)
    plus ``config.SEASONAL_AGG_COLUMNS`` over COMPLETE vintages (row-wise, NaN skipped).

    current_alignment (running vintage only):
      "calendar" (default) - place its values at the calendar-estimated remaining
                              trading days to ``expected_expiry(v, "M")``
      "raw"                 - use the provisional spread_daily.tdays_to_expiry (0 = last available day)
      "exclude"             - leave the running vintage out
    """
    if current_alignment not in {"calendar", "raw", "exclude"}:
        raise ValueError(f"unknown current_alignment {current_alignment!r}")
    cols: dict[int, pd.Series] = {}
    complete: list[int] = []
    for v, g in spread_daily.groupby("vintage", sort=True):
        v = int(v)
        if bool(g["is_current"].any()):
            if current_alignment == "exclude":
                continue
            idx = g["tdays_to_expiry"].tolist()
            if current_alignment == "calendar":
                est = estimated_tdays_to_expiry(g["date"], v)
                if min(est) < 0:
                    log.warning("seasonal_matrix: current vintage %d has data after its expected expiry; "
                                "falling back to raw tdays_to_expiry", v)
                else:
                    idx = est
        else:
            complete.append(v)
            idx = g["tdays_to_expiry"].tolist()
        cols[v] = _series_by(g, idx)
    return _assemble(cols, complete, config.SEASONAL_INDEX_NAME)


def seasonal_matrix_by_tday_index(spread_daily: pd.DataFrame) -> pd.DataFrame:
    """Same layout as ``seasonal_matrix`` but rows = ``tday_index`` (trading days since the
    window start, 0 = first complete day). This axis needs no expiry knowledge and is the
    natural one to overlay the running vintage on the completed ones. Aggregates use
    completed vintages only.
    """
    cols: dict[int, pd.Series] = {}
    complete: list[int] = []
    for v, g in spread_daily.groupby("vintage", sort=True):
        v = int(v)
        if not bool(g["is_current"].any()):
            complete.append(v)
        cols[v] = _series_by(g, g["tday_index"].tolist())
    return _assemble(cols, complete, "tday_index")


def seasonal_tables(spread_daily: pd.DataFrame, current_alignment: str = "calendar") -> dict[str, pd.DataFrame]:
    """{"by_tdays_to_expiry": seasonal_matrix(...), "by_tday_index": seasonal_matrix_by_tday_index(...)}"""
    return {
        "by_tdays_to_expiry": seasonal_matrix(spread_daily, current_alignment=current_alignment),
        "by_tday_index": seasonal_matrix_by_tday_index(spread_daily),
    }


# ----------------------------------------------------------------------------
# overall summary
# ----------------------------------------------------------------------------
def _py(x):
    """Recursively convert pandas/numpy scalars to plain JSON-serialisable Python values."""
    if isinstance(x, dict):
        return {str(k): _py(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, np.ndarray, pd.Index, pd.Series)):
        return [_py(v) for v in list(x)]
    if x is None or x is pd.NaT or x is pd.NA:
        return None
    if isinstance(x, (pd.Timestamp, datetime, date)):
        return None if pd.isna(x) else pd.Timestamp(x).date().isoformat()
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (int, np.integer)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if np.isnan(x) else float(x)
    return x


def _extreme(spread_daily: pd.DataFrame, which: str) -> dict:
    i = spread_daily["spread_bbl"].idxmin() if which == "min" else spread_daily["spread_bbl"].idxmax()
    r = spread_daily.loc[i]
    return {"value": r["spread_bbl"], "date": r["date"], "vintage": r["vintage"],
            "tdays_to_expiry": r["tdays_to_expiry"], "tday_index": r["tday_index"]}


def overall_summary(spread_daily: pd.DataFrame, stats: pd.DataFrame) -> dict:
    """Headline numbers across all vintages as plain Python types (json.dumps-able)."""
    st = stats.sort_values("vintage")
    comp = st[st["is_complete"]]
    cur = st[~st["is_complete"]]
    neg_any = st[st["n_negative_days"] > 0]
    neg_exp = comp[comp["last_value"] < 0]
    out: dict = {
        "spread_label": config.SPREAD_LABEL,
        "unit": config.UNIT_LABEL,
        "data_first_date": spread_daily["date"].min(),
        "data_last_date": spread_daily["date"].max(),
        "n_days_total": len(spread_daily),
        "n_vintages": len(st),
        "n_vintages_complete": len(comp),
        "vintages": st["vintage"].tolist(),
        "current_vintage": int(cur["vintage"].iloc[0]) if len(cur) else None,
        "overall_min": _extreme(spread_daily, "min"),
        "overall_max": _extreme(spread_daily, "max"),
        "n_vintages_negative_any_day": len(neg_any),
        "vintages_negative": neg_any["vintage"].tolist(),
        "share_vintages_negative_any_day_pct": 100.0 * len(neg_any) / len(st) if len(st) else None,
        "n_vintages_negative_at_expiry": len(neg_exp),
        "vintages_negative_at_expiry": neg_exp["vintage"].tolist(),
        "mean_first_value_complete": comp["first_value"].mean() if len(comp) else None,
        "mean_last_value_complete": comp["last_value"].mean() if len(comp) else None,
        "median_last_value_complete": comp["last_value"].median() if len(comp) else None,
        "mean_min_complete": comp["min"].mean() if len(comp) else None,
        "mean_max_complete": comp["max"].mean() if len(comp) else None,
        "mean_of_means_complete": comp["mean"].mean() if len(comp) else None,
        "mean_pct_negative_days_complete": comp["pct_negative_days"].mean() if len(comp) else None,
        "mean_max_drawdown_complete": comp["max_drawdown"].mean() if len(comp) else None,
        "mean_value_t120_complete": comp["value_t120"].mean() if len(comp) else None,
        "mean_value_t60_complete": comp["value_t60"].mean() if len(comp) else None,
        "mean_value_t20_complete": comp["value_t20"].mean() if len(comp) else None,
        "lowest_last_value_complete": None,
        "highest_last_value_complete": None,
        "current": None,
    }
    if len(comp):
        lo, hi = comp.loc[comp["last_value"].idxmin()], comp.loc[comp["last_value"].idxmax()]
        out["lowest_last_value_complete"] = {"vintage": lo["vintage"], "value": lo["last_value"]}
        out["highest_last_value_complete"] = {"vintage": hi["vintage"], "value": hi["last_value"]}
    if len(cur):
        c = cur.iloc[0]
        rank_pool = comp["last_value"].to_numpy() if len(comp) else np.array([])
        out["current"] = {
            "vintage": c["vintage"], "first_date": c["window_start"], "last_date": c["window_end"],
            "n_days": c["n_days"], "first_value": c["first_value"], "last_value": c["last_value"],
            "min": c["min"], "min_date": c["min_date"], "max": c["max"], "max_date": c["max_date"],
            "n_negative_days": c["n_negative_days"],
            "est_tdays_to_expiry": int(estimated_tdays_to_expiry(pd.Series([c["window_end"]]), int(c["vintage"]))[0]),
            "expected_expiry": calendar_nymex.expected_expiry(int(c["vintage"]), config.LEG_MONTHS["jun"]),
            "pct_complete_last_values_below_current": float(100.0 * (rank_pool < c["last_value"]).mean()) if len(rank_pool) else None,
        }
    return _py(out)


def main(argv: list[str] | None = None) -> int:
    """Usage: py src/spread/stats.py [spread_daily.csv]  - prints stats and the summary."""
    import json
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from spread import vintages
    args = sys.argv[1:] if argv is None else argv
    sd = vintages.read_spread_daily(Path(args[0]) if args else config.SPREAD_DAILY_FILE)
    st = vintage_stats(sd)
    with pd.option_context("display.max_rows", 100, "display.width", 250, "display.max_columns", 40):
        print(st.to_string(index=False))
    print(json.dumps(overall_summary(sd, st), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

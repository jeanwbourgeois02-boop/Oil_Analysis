"""
Fixture-Generator fuer die Entwicklung von charts.py und report.py.

Erzeugt vertragskonforme (config.py) Fake-Daten:
    spread_daily, vintage_stats, seasonal = make_fixture_frames(seed=0)

Die Statistiken werden hier nur so weit nachgebaut, dass Spalten und dtypes dem
Datenvertrag entsprechen. Die fachlich massgebliche Implementierung liegt in
stats.py / vintages.py (anderer Agent).

Annahmen, die hier getroffen werden (und die charts/report robust behandeln):
  * Handelstage = Mo-Fr (keine NYMEX-Feiertage).
  * Verfall eines abgeschlossenen Jahrgangs = letzter Handelstag im Mai.
  * Fuer den LAUFENDEN Jahrgang ist der Verfall unbekannt: expiry_date = NaT,
    cal_days_to_expiry / tdays_to_expiry = <NA> (nullable Int64). Die Spalten
    tdays_to_expiry_at_min/_at_max in vintage_stats sind dort ebenfalls <NA>.
  * pct_negative_days ist in Prozent (0..100).

Aufruf:  py tests/fixtures_charts.py   -> schreibt CSVs nach output/_dev/
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from spread import config  # noqa: E402

DEFAULT_END = "2026-09-22"
NEGATIVE_VINTAGES = (2016, 2020)      # diese Jahrgaenge tauchen fuer einige Wochen unter null
NOMINAL_WINDOW_TDAYS = 261            # ~ Handelstage eines vollen Fensters (Mo-Fr)


# ----------------------------------------------------------------------------
# Hilfsfunktionen
# ----------------------------------------------------------------------------
def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float, x0: float = 0.0) -> np.ndarray:
    out = np.empty(n)
    x = x0
    eps = rng.normal(0.0, sigma, n)
    for i in range(n):
        x = phi * x + eps[i]
        out[i] = x
    return out


def _spread_path(rng: np.random.Generator, n: int, vintage: int) -> np.ndarray:
    """Plausibler, mean-revertierender Pfad: Start ~ +8 $/bbl, Konvergenz Richtung ~ +3."""
    t = np.arange(n)
    frac = np.clip(t / (NOMINAL_WINDOW_TDAYS - 1), 0.0, 1.0)
    start = 8.0 + rng.normal(0.0, 1.2)
    end = 3.0 + rng.normal(0.0, 0.9)
    level = start + (end - start) * frac ** 1.2
    level += 1.2 * rng.uniform(0.3, 1.0) * np.sin(2 * np.pi * frac)       # Winterschwaeche
    noise = _ar1(rng, n, phi=0.93, sigma=0.32, x0=rng.normal(0.0, 0.8))
    path = level + noise
    if vintage in NEGATIVE_VINTAGES:
        t0 = int(NOMINAL_WINDOW_TDAYS * rng.uniform(0.45, 0.70))
        width = rng.uniform(9.0, 14.0)
        depth = path[min(t0, n - 1)] + rng.uniform(1.0, 2.5)
        path = path - depth * np.exp(-((t - t0) / width) ** 2)
    return path


def _longest_true_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return int(best)


# ----------------------------------------------------------------------------
# spread_daily
# ----------------------------------------------------------------------------
def make_spread_daily(seed: int = 0, end_date: str = DEFAULT_END) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(pd.Timestamp(config.HISTORY_START), pd.Timestamp(end_date))
    df = pd.DataFrame({"date": dates})
    df["vintage"] = (df["date"].dt.year + (df["date"].dt.month >= 6).astype(int)).astype("int64")

    v_last = int(df["vintage"].max())
    current = v_last if pd.Timestamp(end_date) < pd.Timestamp(config.window_nominal_end(v_last)) else None

    # Ziel-Spread je Jahrgang
    target = np.empty(len(df))
    for v, idx in df.groupby("vintage").indices.items():
        target[idx] = _spread_path(rng, len(idx), int(v))

    # Beine grob konsistent ableiten ($/gal)
    n = len(df)
    ho_jun = np.clip(2.9 + np.cumsum(rng.normal(0.0, 0.012, n)), 1.2, 4.2)
    ho_dec = ho_jun + 0.03 + _ar1(rng, n, 0.9, 0.004)
    crack_dec_bbl = 9.0 + _ar1(rng, n, 0.95, 0.30)
    rb_dec = ho_dec + crack_dec_bbl / config.GALLONS_PER_BBL
    rb_jun = ho_jun + (crack_dec_bbl + target) / config.GALLONS_PER_BBL

    df["rb_jun"] = np.round(rb_jun, 4)
    df["ho_jun"] = np.round(ho_jun, 4)
    df["rb_dec"] = np.round(rb_dec, 4)
    df["ho_dec"] = np.round(ho_dec, 4)

    # Abgeleitete Spalten exakt nach Vertrag
    g = config.GALLONS_PER_BBL
    df["crack_jun_bbl"] = (df["rb_jun"] - df["ho_jun"]) * g
    df["crack_dec_bbl"] = (df["rb_dec"] - df["ho_dec"]) * g
    df["spread_bbl"] = df["crack_jun_bbl"] - df["crack_dec_bbl"]
    df["spread_gal"] = df["spread_bbl"] / g

    is_current = df["vintage"] == current if current is not None else pd.Series(False, index=df.index)
    last_in_vintage = df.groupby("vintage")["date"].transform("max")
    df["expiry_date"] = last_in_vintage.where(~is_current, pd.NaT)
    df["cal_days_to_expiry"] = (df["expiry_date"] - df["date"]).dt.days.astype("Int64")
    df["tday_index"] = df.groupby("vintage").cumcount().astype("int64")
    n_in_vintage = df.groupby("vintage")["date"].transform("size")
    df["tdays_to_expiry"] = (n_in_vintage - 1 - df["tday_index"]).where(~is_current).astype("Int64")
    df["is_current"] = is_current.astype(bool)

    return df[config.SPREAD_DAILY_COLUMNS].reset_index(drop=True)


# ----------------------------------------------------------------------------
# vintage_stats
# ----------------------------------------------------------------------------
def make_vintage_stats(spread_daily: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for v, g in spread_daily.groupby("vintage", sort=True):
        g = g.sort_values("date")
        s = g["spread_bbl"].to_numpy(dtype=float)
        d = g["date"].reset_index(drop=True)
        td = g["tdays_to_expiry"].reset_index(drop=True)
        complete = not bool(g["is_current"].iloc[0])
        imin = int(np.nanargmin(s))
        imax = int(np.nanargmax(s))
        neg = s < 0
        cummax = np.maximum.accumulate(s)
        max_dd = float(np.nanmax(cummax - s)) if len(s) else float("nan")

        def at_td(k: int) -> float:
            if not complete:
                return float("nan")
            m = (td == k).fillna(False).to_numpy()
            return float(s[m][0]) if m.any() else float("nan")

        jan = g[(g["date"].dt.year == int(v)) & (g["date"].dt.month == 1)]
        value_jan1 = float(jan["spread_bbl"].iloc[0]) if len(jan) else float("nan")

        rows.append({
            "vintage": int(v),
            "window_start": d.iloc[0],
            "window_end": d.iloc[-1],
            "is_complete": complete,
            "n_days": int(len(g)),
            "first_value": float(s[0]),
            "last_value": float(s[-1]),
            "mean": float(np.nanmean(s)),
            "median": float(np.nanmedian(s)),
            "std": float(np.nanstd(s, ddof=1)) if len(s) > 1 else float("nan"),
            "min": float(s[imin]),
            "min_date": d.iloc[imin],
            "tdays_to_expiry_at_min": (int(td.iloc[imin]) if complete else pd.NA),
            "max": float(s[imax]),
            "max_date": d.iloc[imax],
            "tdays_to_expiry_at_max": (int(td.iloc[imax]) if complete else pd.NA),
            "n_negative_days": int(neg.sum()),
            "pct_negative_days": float(100.0 * neg.mean()),
            "first_negative_date": (d[neg].iloc[0] if neg.any() else pd.NaT),
            "longest_negative_streak": _longest_true_run(neg),
            "max_drawdown": max_dd,
            "value_t120": at_td(120),
            "value_t60": at_td(60),
            "value_t20": at_td(20),
            "value_jan1": value_jan1,
        })
    vs = pd.DataFrame(rows, columns=config.VINTAGE_STATS_COLUMNS)
    vs["tdays_to_expiry_at_min"] = vs["tdays_to_expiry_at_min"].astype("Int64")
    vs["tdays_to_expiry_at_max"] = vs["tdays_to_expiry_at_max"].astype("Int64")
    vs["first_negative_date"] = pd.to_datetime(vs["first_negative_date"])
    vs["is_complete"] = vs["is_complete"].astype(bool)
    return vs


# ----------------------------------------------------------------------------
# seasonal tables
# ----------------------------------------------------------------------------
def _add_agg(table: pd.DataFrame, agg_over: list[int]) -> pd.DataFrame:
    cols = [c for c in agg_over if c in table.columns]
    block = table[cols].astype(float) if cols else pd.DataFrame(index=table.index)
    for name in config.SEASONAL_AGG_COLUMNS:
        table[name] = getattr(block, name)(axis=1) if cols else np.nan
    return table


def make_seasonal_tables(spread_daily: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sd = spread_daily
    complete_mask = (~sd["is_current"]) & sd["expiry_date"].notna()
    comp = sd[complete_mask]
    complete_vintages = sorted(int(v) for v in comp["vintage"].unique())

    by_td = comp.assign(tdays_to_expiry=comp["tdays_to_expiry"].astype("int64")).pivot(
        index="tdays_to_expiry", columns="vintage", values="spread_bbl").sort_index()
    by_td.columns = [int(c) for c in by_td.columns]
    by_td.index = by_td.index.astype("int64")
    by_td.index.name = config.SEASONAL_INDEX_NAME
    by_td.columns.name = None
    by_td = _add_agg(by_td, complete_vintages)

    by_ti = sd.pivot(index="tday_index", columns="vintage", values="spread_bbl").sort_index()
    by_ti.columns = [int(c) for c in by_ti.columns]
    by_ti.index = by_ti.index.astype("int64")
    by_ti.index.name = "tday_index"
    by_ti.columns.name = None
    by_ti = _add_agg(by_ti, complete_vintages)

    return {"by_tdays_to_expiry": by_td, "by_tday_index": by_ti}


# ----------------------------------------------------------------------------
# overall dict (wie ihn die Pipeline liefern koennte)
# ----------------------------------------------------------------------------
def make_overall(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame) -> dict:
    sd = spread_daily
    vs = vintage_stats
    imin = sd["spread_bbl"].idxmin()
    imax = sd["spread_bbl"].idxmax()
    comp = vs[vs["is_complete"]]
    neg = vs[vs["n_negative_days"] > 0]
    return {
        "overall_min": {"value": float(sd.loc[imin, "spread_bbl"]), "date": sd.loc[imin, "date"],
                        "vintage": int(sd.loc[imin, "vintage"])},
        "overall_max": {"value": float(sd.loc[imax, "spread_bbl"]), "date": sd.loc[imax, "date"],
                        "vintage": int(sd.loc[imax, "vintage"])},
        "n_vintages": int(len(vs)),
        "n_vintages_complete": int(comp.shape[0]),
        "n_vintages_negative_any_day": int(neg.shape[0]),
        "vintages_negative": [int(v) for v in neg["vintage"]],
        "n_vintages_negative_at_expiry": int((comp["last_value"] < 0).sum()),
        "mean_last_value_complete": float(comp["last_value"].mean()) if len(comp) else float("nan"),
        "mean_min_complete": float(comp["min"].mean()) if len(comp) else float("nan"),
        "first_date": sd["date"].min(),
        "last_date": sd["date"].max(),
    }


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------
def make_fixture_frames(seed: int = 0, end_date: str = DEFAULT_END):
    """-> (spread_daily, vintage_stats, seasonal_dict)"""
    sd = make_spread_daily(seed=seed, end_date=end_date)
    vs = make_vintage_stats(sd)
    seasonal = make_seasonal_tables(sd)
    return sd, vs, seasonal


def check_contract(spread_daily: pd.DataFrame, vintage_stats: pd.DataFrame, seasonal: dict) -> None:
    assert list(spread_daily.columns) == config.SPREAD_DAILY_COLUMNS, "spread_daily columns"
    assert list(vintage_stats.columns) == config.VINTAGE_STATS_COLUMNS, "vintage_stats columns"
    assert spread_daily["date"].is_monotonic_increasing and spread_daily["date"].is_unique
    assert str(spread_daily["date"].dtype).startswith("datetime64")
    assert spread_daily["vintage"].dtype.kind == "i"
    assert spread_daily["is_current"].dtype == bool
    for key in ("by_tdays_to_expiry", "by_tday_index"):
        t = seasonal[key]
        assert all(c in t.columns for c in config.SEASONAL_AGG_COLUMNS), key
    assert seasonal["by_tdays_to_expiry"].index.name == config.SEASONAL_INDEX_NAME


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    out = config.OUTPUT_DIR / "_dev"
    out.mkdir(parents=True, exist_ok=True)
    sd, vs, seasonal = make_fixture_frames()
    check_contract(sd, vs, seasonal)
    sd.to_csv(out / "fixture_spread_daily.csv", index=False)
    vs.to_csv(out / "fixture_vintage_stats.csv", index=False)
    seasonal["by_tdays_to_expiry"].to_csv(out / "fixture_seasonal_by_tdays_to_expiry.csv")
    seasonal["by_tday_index"].to_csv(out / "fixture_seasonal_by_tday_index.csv")
    print("spread_daily :", sd.shape, "|", sd["date"].min().date(), "->", sd["date"].max().date())
    print(sd.dtypes.to_string())
    print("vintage_stats:", vs.shape)
    print(vs[["vintage", "is_complete", "n_days", "first_value", "last_value", "min", "min_date",
              "n_negative_days", "pct_negative_days", "longest_negative_streak", "max_drawdown"]]
          .to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    for k, t in seasonal.items():
        print(k, t.shape, "index", t.index.min(), "..", t.index.max(), "cols", list(t.columns))
    print("written to", out)

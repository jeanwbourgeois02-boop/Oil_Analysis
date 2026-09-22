"""
End-to-end tests for the core data pipeline (calendar -> load -> vintages -> stats)
on the synthetic Bloomberg exports from ``make_synthetic.py``.

Run as a plain script (no pytest needed):   py tests/test_pipeline.py
or with pytest:                             py -m pytest tests -q

The synthetic files are (re)generated deterministically into data/raw at the
first access. FutureWarnings are turned into errors: the pipeline must be clean
on pandas 3.
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import time
import traceback
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import make_synthetic  # noqa: E402
from spread import calendar_nymex, config, load, stats, vintages  # noqa: E402

warnings.simplefilter("error", FutureWarning)

AS_OF = make_synthetic.AS_OF_DEFAULT
JUN = config.LEG_MONTHS["jun"]
_CACHE: dict = {}


def data() -> dict:
    """Generate the synthetic exports once and run the pipeline on them (cached)."""
    if not _CACHE:
        t0 = time.perf_counter()
        res = make_synthetic.generate(config.DATA_RAW, seed=make_synthetic.DEFAULT_SEED, as_of=AS_OF)
        _CACHE["gen_seconds"] = time.perf_counter() - t0
        _CACHE["paths"] = res
        _CACHE["truth"] = pd.read_csv(res["truth"], parse_dates=["date"])
        _CACHE["truth"]["date"] = _CACHE["truth"]["date"].astype("datetime64[ns]")
        t0 = time.perf_counter()
        _CACHE["long_xlsx"] = load.load_bloomberg_export(res["xlsx"], reference_date=AS_OF)
        _CACHE["load_xlsx_seconds"] = time.perf_counter() - t0
        t0 = time.perf_counter()
        _CACHE["long_csv"] = load.load_bloomberg_export(res["wide_csv"], reference_date=AS_OF)
        _CACHE["load_csv_seconds"] = time.perf_counter() - t0
        _CACHE["spread_daily"] = vintages.build_spread_daily(_CACHE["long_xlsx"])
        _CACHE["stats"] = stats.vintage_stats(_CACHE["spread_daily"])
    return _CACHE


# ----------------------------------------------------------------------------
# calendar
# ----------------------------------------------------------------------------
def test_calendar_rules():
    assert calendar_nymex.easter_sunday(2024) == date(2024, 3, 31)
    assert calendar_nymex.easter_sunday(2025) == date(2025, 4, 20)
    assert calendar_nymex.easter_sunday(2026) == date(2026, 4, 5)
    assert not calendar_nymex.is_trading_day(date(2024, 3, 29))       # Good Friday
    assert not calendar_nymex.is_trading_day(date(2026, 5, 25))       # Memorial Day
    assert calendar_nymex.expected_expiry(2026, "M") == date(2026, 5, 29)
    assert calendar_nymex.expected_expiry(2021, "M") == date(2021, 5, 28)   # 31 May 2021 = Memorial Day
    assert calendar_nymex.expected_expiry(2015, "M") == date(2015, 5, 29)
    assert calendar_nymex.expected_expiry(2020, "M") == date(2020, 5, 29)
    assert calendar_nymex.expected_expiry(2025, "Z") == date(2025, 11, 28)  # 28 Nov 2025 Fri
    assert calendar_nymex.expected_expiry(2024, "Z") == date(2024, 11, 29)  # Thanksgiving 28 Nov
    assert calendar_nymex.expected_expiry(2024, "F") == date(2023, 12, 29)  # January -> prior December
    assert not calendar_nymex.is_trading_day(date(2023, 6, 19))       # Juneteenth (Mon)
    assert not calendar_nymex.is_trading_day(date(2022, 6, 20))       # Juneteenth observed (Sun -> Mon)
    assert calendar_nymex.is_trading_day(date(2021, 6, 18))           # not yet observed in 2021
    assert not calendar_nymex.is_trading_day(date(2020, 7, 3))        # 4 Jul Sat -> Fri
    assert not calendar_nymex.is_trading_day(date(2021, 12, 24))      # Christmas Sat -> Fri
    assert not calendar_nymex.is_trading_day(date(2022, 12, 26))      # Christmas Sun -> Mon
    assert calendar_nymex.is_trading_day(date(2021, 12, 31))          # New Year Sat: NOT observed on Fri
    assert not calendar_nymex.is_trading_day(date(2023, 1, 2))        # New Year Sun -> Mon
    assert not calendar_nymex.is_trading_day(date(2025, 11, 27))      # Thanksgiving
    assert calendar_nymex.is_trading_day(date(2025, 11, 28))
    assert calendar_nymex.last_trading_day_of_month(2026, 5) == date(2026, 5, 29)
    assert calendar_nymex.trading_days_between(date(2026, 5, 29), date(2026, 5, 29)) == 0
    assert calendar_nymex.trading_days_between(date(2026, 5, 22), date(2026, 5, 29)) == 4   # Memorial Day inside
    assert calendar_nymex.trading_days_between(date(2026, 5, 29), date(2026, 5, 22)) == -4
    days = calendar_nymex.trading_days(date(2024, 1, 1), date(2024, 12, 31))
    assert len(days) == 252 and days[0] == date(2024, 1, 2)   # 262 weekdays - 10 weekday holidays
    assert len(calendar_nymex.nymex_holidays(2024)) == 10


# ----------------------------------------------------------------------------
# loader
# ----------------------------------------------------------------------------
def test_coercion_helpers():
    d = load.coerce_dates([pd.Timestamp("2020-01-02"), date(2020, 1, 3), 43834, "44197", "2020-01-06",
                           "07.01.2020", "01/08/2020", "20200109", "2020-01-10 00:00:00", "Date", None, 245.3])
    exp = ["2020-01-02", "2020-01-03", "2020-01-04", "2021-01-01", "2020-01-06", "2020-01-07", "2020-01-08",
           "2020-01-09", "2020-01-10", None, None, None]
    assert str(d.dtype) == "datetime64[ns]"
    assert [None if pd.isna(x) else x.strftime("%Y-%m-%d") for x in d] == exp
    n = load.coerce_numbers([245.3, "246,1", "1.234,5", "1,234.5", " 2.45 ", "#N/A N/A", None, "", True, pd.Timestamp("2020-01-01")])
    got = [None if np.isnan(x) else x for x in n]
    assert got == [245.3, 246.1, 1234.5, 1234.5, 2.45, None, None, None, None, None]
    assert load.match_ticker(" xbm13 comdty ") == ("XB", "M", "13")
    assert load.match_ticker("HOZ6 COMB Comdty") == ("HO", "Z", "6")
    assert load.match_ticker("HOZ6") == ("HO", "Z", "6")
    assert load.match_ticker("HOME DEPOT") is None and load.match_ticker("XBM131 Comdty") is None
    assert load.match_ticker("CLZ26 Comdty") is None


def test_one_digit_resolution():
    assert load.resolve_contract_year("6", "Z", date(2026, 9, 22)) == 2026
    assert load.resolve_contract_year("7", "M", date(2026, 9, 22)) == 2027
    assert load.resolve_contract_year("7", "Z", date(2026, 9, 22)) == 2027
    assert load.resolve_contract_year("6", "M", date(2026, 5, 29)) == 2026     # observed through its expiry
    assert load.resolve_contract_year("6", "M", date(2026, 6, 1)) == 2036      # past delivery month start
    assert load.resolve_contract_year("3", "M", date(2013, 5, 31)) == 2013
    assert load.resolve_contract_year("13", "M", None, date(2026, 9, 22)) == 2013
    assert load.resolve_contract_year("9", "Z", None, date(2026, 9, 22)) == 2029
    px = data()["long_xlsx"]
    tickers = set(px["ticker"])
    for t in ("XBZ26 Comdty", "HOZ26 Comdty", "XBM27 Comdty", "HOM27 Comdty", "XBZ27 Comdty", "HOZ27 Comdty"):
        assert t in tickers, t
    assert not any(load.match_ticker(t)[2].__len__() == 1 for t in tickers), "one-digit forms leaked"
    sub = px[px["ticker"].isin(["XBZ26 Comdty", "HOZ26 Comdty"])]
    assert set(sub["contract_year"]) == {2026}
    sub = px[px["ticker"].isin(["XBM27 Comdty", "HOM27 Comdty", "XBZ27 Comdty", "HOZ27 Comdty"])]
    assert set(sub["contract_year"]) == {2027}
    assert px.loc[px["ticker"] == "XBZ26 Comdty", "date"].max().date() == AS_OF


def test_layouts_agree_and_schema():
    a, b = data()["long_xlsx"], data()["long_csv"]
    for df in (a, b):
        assert list(df.columns) == config.PRICES_LONG_COLUMNS
        assert str(df["date"].dtype) == "datetime64[ns]"
        assert df["px"].dtype == "float64" and df["contract_year"].dtype == "int64"
        assert df.duplicated(["ticker", "date"]).sum() == 0
        assert df.equals(df.sort_values(["ticker", "date"]).reset_index(drop=True)), "not sorted by (ticker, date)"
        assert set(df["ticker"]) == set(config.all_tickers()), "not all 60 tickers present"
        assert df["ticker"].nunique() == 60
        assert set(df["month_code"]) == set(config.LEG_CODES) and set(df["product"]) == set(config.PRODUCTS)
        assert df["px"].between(0.3, 6.0).all(), "prices not in $/gal after unit normalisation"
        assert (df["px"] > 0).all()
    assert len(a) == len(b)
    m = a.merge(b, on=["ticker", "date"], how="outer", suffixes=("_a", "_b"), indicator=True)
    assert (m["_merge"] == "both").all()
    assert np.allclose(m["px_a"], m["px_b"], atol=1e-9, rtol=0)
    assert (m["contract_year_a"] == m["contract_year_b"]).all()
    # the xlsx was in cents: raw medians > threshold, output medians well below
    assert a.groupby("ticker")["px"].median().max() < load.CENTS_THRESHOLD


def test_validation_report():
    px = data()["long_xlsx"]
    rep = load.validate_prices_long(px, reference_date=AS_OF)
    assert list(rep.columns) == ["ticker", "n", "first_date", "last_date", "px_min", "px_max",
                                 "expected_expiry", "actual_last_date", "expiry_gap_bdays", "status"]
    assert len(rep) == 60 and rep["ticker"].tolist() == config.all_tickers()
    assert (rep["status"] == "ok").all(), rep[rep["status"] != "ok"]
    done = rep[rep["expected_expiry"] < pd.Timestamp(AS_OF)]
    assert (done["expiry_gap_bdays"] == 0).all() and (done["actual_last_date"] == done["expected_expiry"]).all()
    active = rep[rep["expected_expiry"] >= pd.Timestamp(AS_OF)]
    assert len(active) == 6 and active["expiry_gap_bdays"].isna().all()
    # a missing ticker and a truncated one are flagged
    broken = px[px["ticker"] != "XBM15 Comdty"]
    cut = broken["ticker"] == "HOM16 Comdty"
    broken = pd.concat([broken[~cut], broken[cut & (broken["date"] < "2016-05-20")]])
    rep2 = load.validate_prices_long(broken, reference_date=AS_OF).set_index("ticker")
    assert rep2.loc["XBM15 Comdty", "status"] == "missing" and rep2.loc["XBM15 Comdty", "n"] == 0
    assert rep2.loc["HOM16 Comdty", "status"] == "expiry_mismatch" and rep2.loc["HOM16 Comdty", "expiry_gap_bdays"] < 0
    late = px[~((px["ticker"] == "XBZ17 Comdty") & (px["date"] < "2016-07-01"))]
    rep3 = load.validate_prices_long(late, reference_date=AS_OF).set_index("ticker")
    assert rep3.loc["XBZ17 Comdty", "status"] == "short_history"


def test_long_layout_and_german_csv():
    px = data()["long_xlsx"]
    sample = px[px["contract_year"].isin([2016, 2026, 2027])]
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # layout C, plain
        p1 = tmp / "long.csv"
        sample.rename(columns={"px": "PX_LAST"})[["date", "ticker", "PX_LAST"]].to_csv(p1, index=False, date_format="%Y-%m-%d")
        r1 = load.load_bloomberg_export(p1, reference_date=AS_OF)
        assert r1.shape == sample.shape and np.allclose(r1["px"], sample["px"].to_numpy())
        assert set(r1["ticker"]) == set(sample["ticker"])
        # layout C, German Excel-CSV: ';' separator, dd.mm.yyyy, decimal comma, cents, one-digit ticker, extra month code
        p2 = tmp / "long_de.csv"
        g = sample.copy()
        g["Datum"] = g["date"].dt.strftime("%d.%m.%Y")
        g["Wert"] = (g["px"] * 100).map(lambda x: f"{x:.2f}".replace(".", ","))
        g["Security"] = g["ticker"].str.replace("XBZ26 Comdty", "XBZ6 Comdty").str.replace("HOM27 Comdty", "hom7 comdty")
        extra = pd.DataFrame({"Datum": ["02.01.2026"], "Security": ["HOF26 Comdty"], "Wert": ["250,00"]})
        pd.concat([g[["Datum", "Security", "Wert"]], extra]).to_csv(p2, index=False, sep=";")
        r2 = load.load_bloomberg_export(p2, reference_date=AS_OF)
        assert r2.shape == sample.shape, (r2.shape, sample.shape)
        assert set(r2["ticker"]) == set(sample["ticker"])
        assert np.allclose(r2["px"], sample["px"].to_numpy(), atol=1e-9)
        # layout B as xlsx with a field row and a "Date" header
        p3 = tmp / "wide.xlsx"
        wide = sample.pivot(index="date", columns="ticker", values="px")
        with pd.ExcelWriter(p3, engine="openpyxl") as xw:
            hdr = pd.DataFrame([["Date"] + list(wide.columns), [""] + ["PX_LAST"] * wide.shape[1]])
            hdr.to_excel(xw, sheet_name="Sheet1", header=False, index=False)
            body = wide.reset_index()
            body.to_excel(xw, sheet_name="Sheet1", header=False, index=False, startrow=2)
        r3 = load.load_bloomberg_export(p3, reference_date=AS_OF)
        assert r3.shape == sample.shape and np.allclose(r3["px"], sample["px"].to_numpy(), atol=1e-9)


# ----------------------------------------------------------------------------
# vintages
# ----------------------------------------------------------------------------
def test_spread_matches_truth():
    sd, truth = data()["spread_daily"], data()["truth"]
    assert list(sd.columns) == config.SPREAD_DAILY_COLUMNS
    m = sd.merge(truth, on=["vintage", "date"], how="outer", suffixes=("", "_t"), indicator=True)
    assert (m["_merge"] == "both").all(), m[m["_merge"] != "both"][["vintage", "date", "_merge"]].head()
    assert np.allclose(m["spread_bbl"], m["spread_bbl_t"], atol=1e-6, rtol=0)
    for leg in ("rb_jun", "ho_jun", "rb_dec", "ho_dec"):
        assert np.allclose(m[leg], m[f"{leg}_t"], atol=1e-9, rtol=0)
    g = config.GALLONS_PER_BBL
    assert np.allclose(sd["crack_jun_bbl"], (sd["rb_jun"] - sd["ho_jun"]) * g)
    assert np.allclose(sd["crack_dec_bbl"], (sd["rb_dec"] - sd["ho_dec"]) * g)
    assert np.allclose(sd["spread_bbl"], sd["crack_jun_bbl"] - sd["crack_dec_bbl"])
    assert np.allclose(sd["spread_gal"] * g, sd["spread_bbl"])
    # the pipeline result is identical from both layouts
    sd2 = vintages.build_spread_daily(data()["long_csv"])
    assert sd2.shape == sd.shape and np.allclose(sd2["spread_bbl"], sd["spread_bbl"], atol=1e-9)


def test_vintage_chain():
    sd = data()["spread_daily"]
    assert sd["date"].is_unique and sd["date"].is_monotonic_increasing
    assert sd["vintage"].tolist() == sorted(sd["vintage"].tolist())
    assert sd["vintage"].drop_duplicates().tolist() == list(config.VINTAGES)
    span = sd.groupby("vintage")["date"].agg(["min", "max"])
    for prev, nxt in zip(config.VINTAGES[:-1], config.VINTAGES[1:]):
        assert span.loc[prev, "max"] < span.loc[nxt, "min"], (prev, nxt)
        # the next vintage starts on the first trading day on/after 1 June
        assert span.loc[nxt, "min"].date() == calendar_nymex.next_trading_day(config.window_start(nxt), include=True)
    assert span.loc[config.FIRST_VINTAGE, "min"].date() == config.HISTORY_START
    assert span.loc[config.CURRENT_VINTAGE, "max"].date() == AS_OF
    dates = set(sd["date"].dt.date)
    assert all(calendar_nymex.is_trading_day(d) for d in dates)
    all_days = set(calendar_nymex.trading_days(sd["date"].min().date(), sd["date"].max().date()))
    gaps = all_days - dates
    removed = {m["date"] for m in data()["paths"]["missing"]}
    assert gaps == removed, f"unexpected gaps: {sorted(gaps ^ removed)[:10]}"
    assert len(gaps) == len(make_synthetic.MISSING_DEC)
    counts = sd.groupby("vintage").size()
    assert counts.loc[list(config.VINTAGES[:-1])].between(240, 256).all(), counts
    assert (sd["tday_index"].groupby(sd["vintage"]).apply(lambda s: (s.to_numpy() == np.arange(len(s))).all())).all()


def test_expiry_dates():
    sd = data()["spread_daily"]
    chk = vintages.expected_expiry_check(sd)
    assert chk["vintage"].tolist() == [v for v in config.VINTAGES if v != config.CURRENT_VINTAGE]
    assert chk["matches"].all() and (chk["gap_bdays"] == 0).all(), chk[~chk["matches"]]
    for v, g in sd.groupby("vintage"):
        exp = g["expiry_date"].iloc[0]
        assert (g["expiry_date"] == exp).all()
        assert g["tdays_to_expiry"].iloc[-1] == 0 and g["tday_index"].iloc[0] == 0
        assert ((g["tdays_to_expiry"] + g["tday_index"]) == len(g) - 1).all()
        assert g["cal_days_to_expiry"].iloc[-1] == (exp - g["date"].iloc[-1]).days
        if v == config.CURRENT_VINTAGE:
            assert g["is_current"].all() and exp == g["date"].iloc[-1] and g["cal_days_to_expiry"].iloc[-1] == 0
        else:
            assert (~g["is_current"]).all()
            assert exp.date() == calendar_nymex.expected_expiry(int(v), JUN)
            if v == 2018:   # HO Dec quote removed on the expiry day: last complete row is the day before
                assert g["date"].iloc[-1].date() == date(2018, 5, 30) and g["cal_days_to_expiry"].iloc[-1] == 1
            else:
                assert g["date"].iloc[-1] == exp and g["cal_days_to_expiry"].iloc[-1] == 0
    # a vintage with a missing leg is skipped with a warning, not an error
    px = data()["long_xlsx"]
    sd2 = vintages.build_spread_daily(px[px["ticker"] != "HOZ19 Comdty"])
    assert 2019 not in set(sd2["vintage"]) and sd2["vintage"].nunique() == len(config.VINTAGES) - 1


# ----------------------------------------------------------------------------
# stats
# ----------------------------------------------------------------------------
def test_vintage_stats():
    sd, st = data()["spread_daily"], data()["stats"]
    assert list(st.columns) == config.VINTAGE_STATS_COLUMNS
    assert st["vintage"].tolist() == list(config.VINTAGES)
    assert st.loc[~st["is_complete"], "vintage"].tolist() == [config.CURRENT_VINTAGE]
    comp = st[st["is_complete"]]
    assert (st["min"] <= st["first_value"]).all() and (st["first_value"] <= st["max"]).all()
    assert (st["max_drawdown"] >= 0).all() and (st["longest_negative_streak"] <= st["n_negative_days"]).all()
    assert np.allclose(st["pct_negative_days"], 100 * st["n_negative_days"] / st["n_days"])
    neg = set(st.loc[st["n_negative_days"] > 0, "vintage"])
    assert {2015, 2020} <= neg, neg
    assert st.set_index("vintage").loc[2020, "last_value"] < 0 < st.set_index("vintage").loc[2015, "last_value"]
    assert st.set_index("vintage").loc[2020, "longest_negative_streak"] >= 20
    assert st.loc[st["n_negative_days"] == 0, "first_negative_date"].isna().all()
    assert st.loc[st["n_negative_days"] > 0, "first_negative_date"].notna().all()
    assert comp[["value_t120", "value_t60", "value_t20", "value_jan1"]].notna().all().all()
    cur = st[~st["is_complete"]].iloc[0]
    assert np.isnan(cur["value_t120"]) and np.isnan(cur["value_jan1"]) and cur["window_end"].date() == AS_OF
    # manual recomputation for one vintage
    v = 2016
    g = sd[sd["vintage"] == v].reset_index(drop=True)
    r = st.set_index("vintage").loc[v]
    s = g["spread_bbl"]
    assert r["n_days"] == len(g) and r["window_start"] == g["date"].iloc[0] and r["window_end"] == g["expiry_date"].iloc[0]
    assert np.isclose(r["mean"], s.mean()) and np.isclose(r["std"], s.std()) and np.isclose(r["median"], s.median())
    assert np.isclose(r["min"], s.min()) and r["min_date"] == g.loc[s.idxmin(), "date"]
    assert r["tdays_to_expiry_at_min"] == g.loc[s.idxmin(), "tdays_to_expiry"]
    assert np.isclose(r["max_drawdown"], (s.cummax() - s).max())
    assert np.isclose(r["value_t60"], g.loc[g["tdays_to_expiry"] == 60, "spread_bbl"].iloc[0])
    jan = g[(g["date"].dt.year == v) & (g["date"].dt.month == 1)]
    assert np.isclose(r["value_jan1"], jan["spread_bbl"].iloc[0]) and jan["date"].iloc[0].date() == date(2016, 1, 4)
    # streak check on a synthetic series
    fake = sd[sd["vintage"] == 2013].copy()
    fake["spread_bbl"] = 1.0
    fake.loc[fake.index[10:15], "spread_bbl"] = -1.0
    fake.loc[fake.index[30:33], "spread_bbl"] = -1.0
    fr = stats.vintage_stats(fake).iloc[0]
    assert fr["n_negative_days"] == 8 and fr["longest_negative_streak"] == 5
    assert fr["first_negative_date"] == fake["date"].iloc[10] and np.isclose(fr["max_drawdown"], 2.0)
    for c in ("window_start", "window_end", "min_date", "max_date", "first_negative_date"):
        assert str(st[c].dtype) == "datetime64[ns]", c


def test_seasonal_matrices():
    sd, st = data()["spread_daily"], data()["stats"]
    tabs = stats.seasonal_tables(sd)
    assert set(tabs) == {"by_tdays_to_expiry", "by_tday_index"}
    m = tabs["by_tdays_to_expiry"]
    assert m.index.name == config.SEASONAL_INDEX_NAME
    assert m.index[0] == 0 and (np.diff(m.index.to_numpy()) == 1).all()
    vint_cols = [c for c in m.columns if isinstance(c, (int, np.integer))]
    assert vint_cols == list(config.VINTAGES) and all(type(c) is int for c in vint_cols)
    assert list(m.columns[-4:]) == config.SEASONAL_AGG_COLUMNS
    comp = [v for v in config.VINTAGES if v != config.CURRENT_VINTAGE]
    n_max = int(st.loc[st["is_complete"], "n_days"].max())
    assert m.index.max() >= n_max - 1
    for v in comp:
        assert np.isclose(m.loc[0, v], st.set_index("vintage").loc[v, "last_value"])
    # aggregates over complete vintages only
    assert np.allclose(m["mean"], m[comp].mean(axis=1), equal_nan=True)
    assert np.allclose(m["median"], m[comp].median(axis=1), equal_nan=True)
    assert np.allclose(m["min"], m[comp].min(axis=1), equal_nan=True)
    assert np.allclose(m["max"], m[comp].max(axis=1), equal_nan=True)
    # the current vintage is placed at calendar-estimated remaining trading days
    cur = m[config.CURRENT_VINTAGE].dropna()
    est_last = calendar_nymex.trading_days_between(AS_OF, calendar_nymex.expected_expiry(config.CURRENT_VINTAGE, JUN))
    assert cur.index.min() == est_last and est_last > 100, (cur.index.min(), est_last)
    assert np.isclose(cur.loc[est_last], sd.loc[sd["is_current"], "spread_bbl"].iloc[-1])
    assert len(cur) == int((sd["is_current"]).sum())
    raw = stats.seasonal_matrix(sd, current_alignment="raw")
    assert raw[config.CURRENT_VINTAGE].first_valid_index() == 0
    assert config.CURRENT_VINTAGE not in stats.seasonal_matrix(sd, current_alignment="exclude").columns
    # by tday_index: everything starts at 0 and equals first_value
    m2 = tabs["by_tday_index"]
    assert m2.index.name == "tday_index" and m2.index[0] == 0
    for v in config.VINTAGES:
        assert np.isclose(m2.loc[0, v], st.set_index("vintage").loc[v, "first_value"])
    assert np.allclose(m2["mean"], m2[comp].mean(axis=1), equal_nan=True)
    assert m2[config.CURRENT_VINTAGE].notna().sum() == int(sd["is_current"].sum())


def test_overall_summary_json():
    sd, st = data()["spread_daily"], data()["stats"]
    s = stats.overall_summary(sd, st)
    txt = json.dumps(s)
    assert json.loads(txt) == s
    assert s["n_vintages"] == len(config.VINTAGES) and s["n_vintages_complete"] == len(config.VINTAGES) - 1
    assert s["current_vintage"] == config.CURRENT_VINTAGE
    assert np.isclose(s["overall_min"]["value"], sd["spread_bbl"].min())
    assert s["overall_min"]["vintage"] == 2020 and isinstance(s["overall_min"]["date"], str)
    assert np.isclose(s["overall_max"]["value"], sd["spread_bbl"].max())
    assert set(s["vintages_negative"]) >= {2015, 2020} and s["n_vintages_negative_any_day"] == len(s["vintages_negative"])
    assert 2020 in s["vintages_negative_at_expiry"]
    assert np.isclose(s["mean_last_value_complete"], st.loc[st["is_complete"], "last_value"].mean())
    assert np.isclose(s["mean_min_complete"], st.loc[st["is_complete"], "min"].mean())
    assert s["current"]["vintage"] == config.CURRENT_VINTAGE and s["current"]["last_date"] == AS_OF.isoformat()
    assert s["current"]["expected_expiry"] == "2027-05-28"


# ----------------------------------------------------------------------------
def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and inspect.isfunction(f)]
    t_all = time.perf_counter()
    failed = 0
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.simplefilter("error", FutureWarning)
        for name, fn in tests:
            t0 = time.perf_counter()
            try:
                fn()
                print(f"PASS  {name:38s} {time.perf_counter() - t0:6.2f}s")
            except Exception:
                failed += 1
                print(f"FAIL  {name:38s} {time.perf_counter() - t0:6.2f}s")
                traceback.print_exc()
    other = [w for w in caught if not issubclass(w.category, FutureWarning)]
    d = _CACHE
    sd, st = d.get("spread_daily"), d.get("stats")
    print("-" * 72)
    print(f"{len(tests) - failed}/{len(tests)} tests passed in {time.perf_counter() - t_all:.1f}s "
          f"(synthetic {d.get('gen_seconds', 0):.1f}s, load xlsx {d.get('load_xlsx_seconds', 0):.1f}s, "
          f"load csv {d.get('load_csv_seconds', 0):.1f}s)")
    if sd is not None:
        neg = st.loc[st["n_negative_days"] > 0, "vintage"].tolist()
        print(f"prices_long: {len(d['long_xlsx'])} rows / {d['long_xlsx']['ticker'].nunique()} tickers; "
              f"spread_daily: {len(sd)} rows, {sd['vintage'].nunique()} vintages "
              f"{sd['date'].min().date()} .. {sd['date'].max().date()}; negative vintages: {neg}; "
              f"last values: " + ", ".join(f"{int(v)}:{x:+.1f}" for v, x in zip(st['vintage'], st['last_value'])))
    if other:
        print(f"{len(other)} non-Future warning(s):")
        for w in other[:10]:
            print(f"  {w.category.__name__}: {w.message} ({w.filename}:{w.lineno})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

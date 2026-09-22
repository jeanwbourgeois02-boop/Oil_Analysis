"""
Loader for the Bloomberg PX_LAST export of the RB/HO futures -> ``prices_long``.

Output contract: ``config.PRICES_LONG_COLUMNS`` (date, ticker, product,
month_code, contract_year, px), sorted by (ticker, date), ``date`` as
datetime64[ns], ``px`` as float in **$/gal** (unit-normalised), one row per
(ticker, date).

Supported input layouts (auto-detected per sheet)
-------------------------------------------------
A "paired"  (Excel BDH template): a ticker string somewhere in the top rows in
            column k, below it a (date, value) column pair in columns k and
            k+1 (optionally with a "Date" / "PX_LAST" header row in between).
B "wide"    (Bloomberg wizard / API script, xlsx or csv): first column = date,
            a header row with >= 2 tickers, optionally a second header row with
            the field name ("PX_LAST") that is skipped. If a ticker spans
            several field columns, the PX_LAST column is chosen.
C "long"    (csv/xlsx): columns date, ticker, px (aliases: value, PX_LAST,
            price, ...), optional field column.

Sheets whose name looks like documentation ("Anleitung", "Tickerliste",
"README", ...) are skipped.

Tickers: regex ``^(XB|HO)([FGHJKMNQUVXZ])(\\d{1,2})(?:\\s|$)`` (case-insensitive,
suffixes like " Comdty" / " COMB Comdty" ignored). Only month codes M and Z are
kept. One-digit years (Bloomberg's form for active contracts, e.g. "XBZ6") are
resolved from the last observed date of that ticker: the smallest candidate
year Y in {2010+d, 2020+d, 2030+d, ...} whose delivery month start
date(Y, delivery_month, 1) is after the last observation.

Unit trap: Bloomberg quotes XB and HO in US **cents**/gal, CME in $/gal. Per
ticker: median px > ``CENTS_THRESHOLD`` (20) -> cents -> divided by 100 (logged).
"""
from __future__ import annotations

import csv
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from spread import calendar_nymex, config
except ImportError:  # executed as a script: put src/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from spread import calendar_nymex, config

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# constants
# ----------------------------------------------------------------------------
CENTS_THRESHOLD = 20.0                   # median px > 20 -> quoted in cents/gal -> /100
PLAUSIBLE_PX_MIN = 0.2                   # $/gal sanity band after normalisation
PLAUSIBLE_PX_MAX = 10.0
TICKER_RE = re.compile(r"^(XB|HO)([FGHJKMNQUVXZ])(\d{1,2})(?:\s|$)", re.IGNORECASE)
SKIP_SHEET_RE = re.compile(r"anleitung|tickerliste|readme|hinweis|instruction|notes|legende", re.IGNORECASE)
HEADER_SCAN_ROWS = 6                     # top rows scanned for ticker cells / header rows
PROBE_ROWS = 40                          # rows probed below a header cell to classify the layout
EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX = 20000, 80000   # 1954-09-.. 2119-01, Excel serial day numbers
EXCEL_EPOCH = pd.Timestamp("1899-12-30")
DATE_FORMATS = (
    "%Y-%m-%d", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y", "%Y%m%d", "%Y/%m/%d",
    "%d-%m-%Y", "%d.%m.%y", "%m/%d/%y", "%d.%m.%Y %H:%M:%S", "%m/%d/%Y %H:%M:%S",
    "%d.%m.%Y %H:%M", "%m/%d/%Y %H:%M",
)
PREFERRED_FIELDS = ("PX_LAST", "PX_SETTLE", "LAST_PRICE", "SETTLE", "PX_CLOSE", "CLOSE", "LAST", "VALUE", "PX")
LONG_DATE_NAMES = ("date", "datum", "dates", "trade_date", "as_of", "asof", "as_of_date", "tag", "day")
LONG_TICKER_NAMES = ("ticker", "security", "sec", "instrument", "symbol", "contract", "bbg_ticker", "id")
LONG_PX_NAMES = ("px", "px_last", "value", "price", "close", "last", "settle", "px_settle", "last_price",
                 "settlement", "preis", "kurs", "wert")
LONG_FIELD_NAMES = ("field", "feld", "fld")
SHORT_HISTORY_TOLERANCE_BDAYS = 5        # data may start at most this many trading days after window_start
STALE_TOLERANCE_BDAYS = 5                # active contract: warn if last date older than this
RAW_COLUMNS = ["root", "month_code", "year_str", "date", "px"]


# ----------------------------------------------------------------------------
# scalar / vector coercion helpers
# ----------------------------------------------------------------------------
def _is_missing(x) -> bool:
    if x is None or x is pd.NaT or x is pd.NA:
        return True
    if isinstance(x, str):
        return x.strip() == ""
    if isinstance(x, (float, np.floating)):
        return bool(np.isnan(x))
    return False


def _kind(x) -> str:
    """Classify a raw cell: 'na' | 'dt' | 'num' | 'str' | 'other'."""
    if _is_missing(x):
        return "na"
    if isinstance(x, bool):
        return "other"
    if isinstance(x, (pd.Timestamp, datetime, date, np.datetime64)):
        return "dt"
    if isinstance(x, (int, float, np.integer, np.floating)):
        return "num"
    if isinstance(x, str):
        return "str"
    return "other"


def _dt_scalar(x) -> pd.Timestamp:
    try:
        ts = pd.Timestamp(x)
    except (TypeError, ValueError):
        return pd.NaT
    if ts is pd.NaT:
        return pd.NaT
    if ts.tzinfo is not None:
        ts = ts.tz_convert(None)
    return ts.normalize()


def _as_object_series(values) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype(object)
    return pd.Series(list(values), dtype=object)


def coerce_dates(values) -> pd.Series:
    """Coerce a column of raw cells to datetime64[ns] (midnight), NaT where not a date.

    Accepts datetime/date/Timestamp cells, Excel serial numbers, and strings in
    ISO 8601, dd.mm.yyyy, mm/dd/yyyy (preferred over dd/mm/yyyy), yyyymmdd, ...
    """
    s = _as_object_series(values)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if s.empty:
        return out
    kinds = s.map(_kind)

    m = kinds == "dt"
    if m.any():
        out.loc[m] = pd.to_datetime(s[m].map(_dt_scalar)).astype("datetime64[ns]")

    m = kinds == "num"
    if m.any():
        nums = pd.to_numeric(s[m], errors="coerce")
        ok = nums.between(EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX)
        if ok.any():
            days = nums[ok].round().astype("int64")
            out.loc[days.index] = (EXCEL_EPOCH + pd.to_timedelta(days, unit="D")).astype("datetime64[ns]")

    m = kinds == "str"
    if m.any():
        strs = s[m].map(lambda x: x.strip())
        # numeric strings that are Excel serials
        serial = pd.to_numeric(strs, errors="coerce")
        ok = serial.between(EXCEL_SERIAL_MIN, EXCEL_SERIAL_MAX)
        if ok.any():
            days = serial[ok].round().astype("int64")
            out.loc[days.index] = (EXCEL_EPOCH + pd.to_timedelta(days, unit="D")).astype("datetime64[ns]")
        remaining = strs[~ok]
        for fmt in ("ISO8601",) + DATE_FORMATS:
            if remaining.empty:
                break
            parsed = pd.to_datetime(remaining, format=fmt, errors="coerce")
            hit = parsed.notna()
            if hit.any():
                out.loc[parsed[hit].index] = parsed[hit].astype("datetime64[ns]")
                remaining = remaining[~hit]
    return out.dt.normalize().astype("datetime64[ns]")


def _fix_decimal(t: str) -> str:
    t = t.replace(" ", "").replace(" ", "")
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):          # German 1.234,56
            return t.replace(".", "").replace(",", ".")
        return t.replace(",", "")                # US 1,234.56
    if "," in t:                                 # German 2,4530
        return t.replace(",", ".")
    return t


def coerce_numbers(values) -> pd.Series:
    """Coerce raw cells to float64; accepts German decimal comma; NaN otherwise."""
    s = _as_object_series(values)
    out = pd.Series(np.nan, index=s.index, dtype="float64")
    if s.empty:
        return out
    kinds = s.map(_kind)
    m = kinds.isin(["num", "str"])
    if m.any():
        vals = pd.to_numeric(s[m].map(lambda x: x.strip() if isinstance(x, str) else x), errors="coerce")
        out.loc[m] = vals.astype("float64").to_numpy()
    m2 = out.isna() & (kinds == "str")
    if m2.any():
        fixed = s[m2].map(lambda x: _fix_decimal(x.strip()))
        out.loc[m2] = pd.to_numeric(fixed, errors="coerce").astype("float64").to_numpy()
    return out


def _norm_name(x) -> str:
    return re.sub(r"\s+", "_", str(x).strip().lower()) if isinstance(x, str) else ""


def _norm_field(x) -> str:
    return str(x).strip().upper() if isinstance(x, str) else ""


def match_ticker(cell) -> tuple[str, str, str] | None:
    """(root, month_code, year_str) for a cell holding an XB/HO ticker, else None."""
    if not isinstance(cell, str):
        return None
    m = TICKER_RE.match(cell.strip())
    if not m:
        return None
    return m.group(1).upper(), m.group(2).upper(), m.group(3)


def resolve_contract_year(year_str: str, month_code: str, last_date: date | None,
                          reference_date: date | None = None) -> int:
    """Contract year from a 1- or 2-digit ticker year.

    Two digits -> 2000 + yy. One digit d -> the smallest Y in {2010+d, 2020+d, ...}
    such that date(Y, delivery_month, 1) > last observed date of the ticker
    (the contract had not reached its delivery month when last observed).
    `reference_date` (default today) is used when there is no observation.
    """
    if len(year_str) == 2:
        return 2000 + int(year_str)
    d = int(year_str)
    anchor = last_date if last_date is not None else (reference_date or date.today())
    delivery_month = config.MONTH_CODES[month_code.upper()]
    for decade in range(2010, 2100, 10):
        y = decade + d
        if date(y, delivery_month, 1) > anchor:
            return y
    raise ValueError(f"cannot resolve one-digit year {year_str!r} for anchor {anchor}")


# ----------------------------------------------------------------------------
# reading raw grids
# ----------------------------------------------------------------------------
def _sniff_sep(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        first = next((ln for ln in sample.splitlines() if ln.strip()), "")
        counts = {d: first.count(d) for d in (",", ";", "\t", "|")}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else ","


def _read_csv_grid(path: Path) -> pd.DataFrame:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sep = _sniff_sep(text[:65536])
    rows = [r for r in csv.reader(text.splitlines(), delimiter=sep)]
    if not rows:
        return pd.DataFrame()
    width = max(len(r) for r in rows)
    rows = [r + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=object)


def _read_grids(path: Path) -> dict[str, pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xlsm", ".xltx", ".xltm"}:
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine="openpyxl")
    elif suffix == ".xls":
        sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    elif suffix in {".csv", ".txt", ".tsv"}:
        sheets = {path.stem: _read_csv_grid(path)}
    else:
        raise ValueError(f"unsupported file type: {path}")
    grids: dict[str, pd.DataFrame] = {}
    for name, df in sheets.items():
        if SKIP_SHEET_RE.search(str(name)):
            log.info("sheet %r skipped (documentation sheet)", name)
            continue
        if df.empty:
            continue
        df = df.reset_index(drop=True)
        df.columns = range(df.shape[1])
        grids[str(name)] = df
    return grids


# ----------------------------------------------------------------------------
# layout detection and parsing (each parser returns RAW_COLUMNS)
# ----------------------------------------------------------------------------
def _empty_raw() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=object) for c in RAW_COLUMNS})


def _find_ticker_cells(grid: pd.DataFrame, max_rows: int = HEADER_SCAN_ROWS) -> list[tuple[int, int, str, str, str]]:
    hits = []
    for r in range(min(max_rows, len(grid))):
        row = grid.iloc[r]
        for c, cell in enumerate(row.tolist()):
            m = match_ticker(cell)
            if m:
                hits.append((r, c, *m))
    return hits


def _find_long_header(grid: pd.DataFrame) -> dict | None:
    for r in range(min(HEADER_SCAN_ROWS, len(grid))):
        names = [_norm_name(x) for x in grid.iloc[r].tolist()]
        def find(cands):
            for cand in cands:
                if cand in names:
                    return names.index(cand)
            return None
        dc, tc, pc = find(LONG_DATE_NAMES), find(LONG_TICKER_NAMES), find(LONG_PX_NAMES)
        if dc is not None and tc is not None and pc is not None:
            return {"row": r, "date": dc, "ticker": tc, "px": pc, "field": find(LONG_FIELD_NAMES)}
    return None


def _parse_long(grid: pd.DataFrame, hdr: dict, sheet: str) -> pd.DataFrame:
    body = grid.iloc[hdr["row"] + 1:]
    dates = coerce_dates(body[hdr["date"]])
    px = coerce_numbers(body[hdr["px"]])
    matches = body[hdr["ticker"]].map(match_ticker)
    keep = dates.notna() & matches.notna()
    if hdr["field"] is not None:
        fields = body[hdr["field"]].map(_norm_field)
        present = [f for f in PREFERRED_FIELDS if (fields == f).any()]
        if present:
            keep &= fields == present[0]
            log.info("sheet %r: long layout with field column, using field %s", sheet, present[0])
    n_bad = int((dates.notna() & matches.isna()).sum())
    if n_bad:
        log.warning("sheet %r: %d rows with unrecognised ticker strings dropped", sheet, n_bad)
    m = matches[keep]
    out = pd.DataFrame({
        "root": [t[0] for t in m], "month_code": [t[1] for t in m], "year_str": [t[2] for t in m],
        "date": dates[keep].to_numpy(), "px": px[keep].to_numpy(),
    })
    log.info("sheet %r: long layout, %d rows, %d tickers", sheet, len(out), out[["root", "month_code", "year_str"]].drop_duplicates().shape[0])
    return out


def _column_is_dates(grid: pd.DataFrame, r: int, c: int) -> bool:
    probe = grid.iloc[r + 1:r + 1 + PROBE_ROWS, c]
    n_dates = int(coerce_dates(probe).notna().sum())
    n_nums = int(coerce_numbers(probe).notna().sum())   # serials count as both -> tie means dates
    return n_dates > 0 and n_dates >= n_nums


def _classify(grid: pd.DataFrame, hits: list) -> str:
    votes_paired = sum(_column_is_dates(grid, r, c) for r, c, *_ in hits)
    return "paired" if votes_paired * 2 >= len(hits) else "wide"


def _parse_paired(grid: pd.DataFrame, hits: list, sheet: str) -> pd.DataFrame:
    frames = []
    seen: set[tuple[str, int]] = set()
    n_cols = grid.shape[1]
    for r, c, root, mc, ys in hits:
        raw = f"{root}{mc}{ys}"
        if (raw, c) in seen:
            continue
        seen.add((raw, c))
        if c + 1 >= n_cols:
            log.warning("sheet %r: ticker %s in last column, no value column -> skipped", sheet, raw)
            continue
        col_d = coerce_dates(grid.iloc[r + 1:, c])
        col_v = coerce_numbers(grid.iloc[r + 1:, c + 1])
        both = col_d.notna() & col_v.notna()
        if not both.any():
            log.warning("sheet %r: no (date, value) rows below ticker %s at row %d col %d", sheet, raw, r + 1, c + 1)
            continue
        start = int(both.idxmax())
        empty = grid.iloc[start:, c].map(_is_missing) & grid.iloc[start:, c + 1].map(_is_missing)
        end = int(empty.idxmax()) if empty.any() else len(grid)
        d = col_d.loc[start:end - 1]
        v = col_v.loc[start:end - 1]
        ok = d.notna()
        frames.append(pd.DataFrame({
            "root": root, "month_code": mc, "year_str": ys,
            "date": d[ok].to_numpy(), "px": v[ok].to_numpy(),
        }))
    if not frames:
        return _empty_raw()
    out = pd.concat(frames, ignore_index=True)
    log.info("sheet %r: paired layout, %d ticker blocks, %d rows", sheet, len(frames), len(out))
    return out


def _parse_wide(grid: pd.DataFrame, hits: list, sheet: str) -> pd.DataFrame:
    rows = pd.Series([h[0] for h in hits])
    header_row = int(rows.mode().iloc[0])
    row_hits = sorted([h for h in hits if h[0] == header_row], key=lambda h: h[1])
    ticker_cols = {h[1] for h in row_hits}
    date_col = None
    for c in range(grid.shape[1]):
        if c in ticker_cols:
            continue
        if _column_is_dates(grid, header_row, c):
            date_col = c
            break
    if date_col is None:
        log.warning("sheet %r: wide layout but no date column found -> skipped", sheet)
        return _empty_raw()
    body = grid.iloc[header_row + 1:]
    dates = coerce_dates(body[date_col])
    data_mask = dates.notna()
    field_row = None
    if header_row + 1 < len(grid):
        fields = [_norm_field(x) for x in grid.iloc[header_row + 1].tolist()]
        if any(f in PREFERRED_FIELDS for f in fields):
            field_row = header_row + 1
    frames = []
    for i, (r, c, root, mc, ys) in enumerate(row_hits):
        nxt = row_hits[i + 1][1] if i + 1 < len(row_hits) else grid.shape[1]
        col = c
        if field_row is not None:
            span = {_norm_field(grid.iat[field_row, j]): j for j in range(c, nxt) if j != date_col}
            for pf in PREFERRED_FIELDS:
                if pf in span:
                    col = span[pf]
                    break
        px = coerce_numbers(body[col])
        frames.append(pd.DataFrame({
            "root": root, "month_code": mc, "year_str": ys,
            "date": dates[data_mask].to_numpy(), "px": px[data_mask].to_numpy(),
        }))
    out = pd.concat(frames, ignore_index=True) if frames else _empty_raw()
    log.info("sheet %r: wide layout (date col %d, header row %d%s), %d tickers, %d dates",
             sheet, date_col + 1, header_row + 1, ", field row skipped" if field_row is not None else "",
             len(frames), int(data_mask.sum()))
    return out


def _parse_grid(grid: pd.DataFrame, sheet: str) -> pd.DataFrame:
    hdr = _find_long_header(grid)
    if hdr is not None:
        return _parse_long(grid, hdr, sheet)
    hits = _find_ticker_cells(grid)
    if not hits:
        log.warning("sheet %r: no ticker cells in the first %d rows and no long header -> skipped", sheet, HEADER_SCAN_ROWS)
        return _empty_raw()
    layout = _classify(grid, hits)
    if layout == "paired":
        return _parse_paired(grid, hits, sheet)
    return _parse_wide(grid, hits, sheet)


# ----------------------------------------------------------------------------
# normalisation
# ----------------------------------------------------------------------------
def normalize_units(df: pd.DataFrame) -> pd.DataFrame:
    """Per ticker: median px > CENTS_THRESHOLD -> cents/gal -> divide by 100."""
    df = df.copy()
    med = df.groupby("ticker")["px"].median()
    cents = med.index[med > CENTS_THRESHOLD].tolist()
    if cents:
        mask = df["ticker"].isin(cents)
        df.loc[mask, "px"] = df.loc[mask, "px"] / 100.0
        log.info("unit normalisation: %d of %d tickers quoted in cents/gal (median > %g) -> divided by 100",
                 len(cents), len(med), CENTS_THRESHOLD)
        log.debug("cents tickers: %s", ", ".join(cents))
    else:
        log.info("unit normalisation: all %d tickers already in $/gal (median <= %g)", len(med), CENTS_THRESHOLD)
    band = (df["px"] < PLAUSIBLE_PX_MIN) | (df["px"] > PLAUSIBLE_PX_MAX)
    if band.any():
        log.warning("%d prices outside the plausible band %.1f..%.1f $/gal (tickers: %s)",
                    int(band.sum()), PLAUSIBLE_PX_MIN, PLAUSIBLE_PX_MAX, ", ".join(df.loc[band, "ticker"].unique()[:10]))
    return df


def _finalize(raw: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    raw = raw.copy()
    raw["date"] = pd.to_datetime(raw["date"]).astype("datetime64[ns]")
    raw["px"] = pd.to_numeric(raw["px"], errors="coerce").astype("float64")
    raw = raw[raw["date"].notna()]

    bad_mc = ~raw["month_code"].isin(config.LEG_CODES)
    if bad_mc.any():
        dropped = sorted({f"{r}{m}{y}" for r, m, y in raw.loc[bad_mc, ["root", "month_code", "year_str"]].itertuples(index=False)})
        log.info("dropping %d rows of %d tickers with month codes other than %s: %s",
                 int(bad_mc.sum()), len(dropped), "/".join(config.LEG_CODES), ", ".join(dropped))
        raw = raw[~bad_mc]
    if raw.empty:
        raise ValueError("no Jun/Dec RB/HO price rows found in the export")

    key = ["root", "month_code", "year_str"]
    last = raw.groupby(key)["date"].max()
    year_map: dict[tuple[str, str, str], int] = {}
    for (root, mc, ys), last_date in last.items():
        y = resolve_contract_year(ys, mc, last_date.date(), reference_date)
        year_map[(root, mc, ys)] = y
        if len(ys) == 1:
            log.info("one-digit ticker %s%s%s (last observation %s) resolved to contract year %d",
                     root, mc, ys, last_date.date(), y)
    ticker_map = {k: config.bbg_ticker(config.BBG_ROOT_TO_PRODUCT[k[0]], k[1], y) for k, y in year_map.items()}
    keys = list(zip(raw["root"], raw["month_code"], raw["year_str"]))
    raw["contract_year"] = [year_map[k] for k in keys]
    raw["ticker"] = [ticker_map[k] for k in keys]
    raw["product"] = raw["root"].map(config.BBG_ROOT_TO_PRODUCT)

    n_nan = int(raw["px"].isna().sum())
    raw = raw[raw["px"].notna()]
    n_nonpos = int((raw["px"] <= 0).sum())
    raw = raw[raw["px"] > 0]
    n_dup = int(raw.duplicated(["ticker", "date"], keep="last").sum())
    raw = raw.drop_duplicates(["ticker", "date"], keep="last")
    log.info("cleaning: dropped %d rows without price, %d non-positive, %d duplicate (ticker, date) rows",
             n_nan, n_nonpos, n_dup)

    df = normalize_units(raw[config.PRICES_LONG_COLUMNS])
    df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
    df["date"] = df["date"].astype("datetime64[ns]")
    df["contract_year"] = df["contract_year"].astype("int64")
    df["px"] = df["px"].astype("float64")
    for c in ("ticker", "product", "month_code"):
        df[c] = df[c].astype("str")
    tick = df["ticker"].unique()
    missing = [t for t in config.all_tickers() if t not in set(tick)]
    log.info("prices_long: %d rows, %d tickers, %s .. %s%s", len(df), len(tick),
             df["date"].min().date(), df["date"].max().date(),
             f"; {len(missing)} expected tickers missing: {', '.join(missing)}" if missing else "")
    return df[config.PRICES_LONG_COLUMNS]


# ----------------------------------------------------------------------------
# public API
# ----------------------------------------------------------------------------
def load_bloomberg_export(path: str | Path, reference_date: date | None = None) -> pd.DataFrame:
    """Load a Bloomberg PX_LAST export (layouts A/B/C, xlsx or csv) into ``prices_long``."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    ref = calendar_nymex.to_date(reference_date) if reference_date is not None else date.today()
    log.info("loading %s", path)
    grids = _read_grids(path)
    frames = []
    for name, grid in grids.items():
        part = _parse_grid(grid, name)
        if not part.empty:
            frames.append(part)
    if not frames:
        raise ValueError(f"{path}: no recognisable price data (layouts A paired / B wide / C long)")
    raw = pd.concat(frames, ignore_index=True)
    return _finalize(raw, ref)


def read_prices_long(path: str | Path = config.PRICES_LONG_FILE) -> pd.DataFrame:
    """Read a prices_long CSV written by this module back with the contract dtypes."""
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], format="ISO8601").astype("datetime64[ns]")
    df["contract_year"] = df["contract_year"].astype("int64")
    df["px"] = df["px"].astype("float64")
    return df[config.PRICES_LONG_COLUMNS].sort_values(["ticker", "date"]).reset_index(drop=True)


def validate_prices_long(df: pd.DataFrame, reference_date: date | None = None) -> pd.DataFrame:
    """Per-ticker validation report.

    Columns: ticker, n, first_date, last_date, px_min, px_max, expected_expiry,
    actual_last_date, expiry_gap_bdays, status. Rows for every ticker in
    ``config.all_tickers()`` (status "missing" if absent) plus any extra ticker
    present in ``df``. ``expiry_gap_bdays`` is the signed trading-day gap
    actual - expected (0 = data ends exactly on the expected expiry); it is
    only computed for contracts whose expected expiry is before ``reference_date``.
    Statuses: ok | missing | expiry_mismatch | short_history (data starts more
    than SHORT_HISTORY_TOLERANCE_BDAYS trading days after the vintage window start).
    """
    ref = calendar_nymex.to_date(reference_date) if reference_date is not None else date.today()
    expected = config.all_tickers()
    present = list(pd.unique(df["ticker"])) if len(df) else []
    tickers = expected + [t for t in present if t not in set(expected)]
    g = df.groupby("ticker")
    agg = pd.concat([g["px"].agg(n="size", px_min="min", px_max="max"),
                     g["date"].agg(first_date="min", last_date="max")], axis=1) if len(df) else None
    rows = []
    for t in tickers:
        m = match_ticker(t)
        if m is None:
            log.warning("validate: ticker %r does not match the RB/HO pattern", t)
            continue
        root, mc, ys = m
        cy = resolve_contract_year(ys, mc, None, ref) if len(ys) == 1 else 2000 + int(ys)
        exp = calendar_nymex.expected_expiry(cy, mc)
        row = {"ticker": t, "n": 0, "first_date": pd.NaT, "last_date": pd.NaT, "px_min": np.nan, "px_max": np.nan,
               "expected_expiry": pd.Timestamp(exp), "actual_last_date": pd.NaT, "expiry_gap_bdays": pd.NA, "status": "missing"}
        if agg is not None and t in agg.index:
            a = agg.loc[t]
            first, last = a["first_date"].date(), a["last_date"].date()
            row.update(n=int(a["n"]), first_date=a["first_date"], last_date=a["last_date"],
                       px_min=float(a["px_min"]), px_max=float(a["px_max"]), actual_last_date=a["last_date"])
            status = "ok"
            if exp < ref:
                gap = calendar_nymex.trading_days_between(exp, last)
                row["expiry_gap_bdays"] = gap
                if gap != 0:
                    status = "expiry_mismatch"
            else:
                stale = calendar_nymex.trading_days_between(last, ref)
                if stale > STALE_TOLERANCE_BDAYS:
                    log.warning("validate: active contract %s last quoted %s, %d trading days before %s", t, last, stale, ref)
            ws = config.window_start(cy)
            if status == "ok" and first > ws and calendar_nymex.trading_days_between(ws, first) > SHORT_HISTORY_TOLERANCE_BDAYS:
                status = "short_history"
            row["status"] = status
        rows.append(row)
    rep = pd.DataFrame(rows, columns=["ticker", "n", "first_date", "last_date", "px_min", "px_max",
                                      "expected_expiry", "actual_last_date", "expiry_gap_bdays", "status"])
    for c in ("first_date", "last_date", "expected_expiry", "actual_last_date"):
        rep[c] = pd.to_datetime(rep[c]).astype("datetime64[ns]")
    rep["n"] = rep["n"].astype("int64")
    rep["expiry_gap_bdays"] = rep["expiry_gap_bdays"].astype("Int64")
    counts = rep["status"].value_counts().to_dict()
    log.info("validation: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return rep


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else config.DEFAULT_RAW_FILE
    df = load_bloomberg_export(path)
    report = validate_prices_long(df)
    with pd.option_context("display.max_rows", 500, "display.width", 250, "display.max_columns", 20):
        print(report.to_string(index=False))
    config.PRICES_LONG_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.PRICES_LONG_FILE, index=False, date_format="%Y-%m-%d", lineterminator="\n")
    bad = report[report["status"] != "ok"]
    print(f"\nwrote {len(df)} rows / {df['ticker'].nunique()} tickers -> {config.PRICES_LONG_FILE}")
    print(f"validation: {len(report) - len(bad)} ok, {len(bad)} with issues"
          + (": " + ", ".join(f"{r.ticker}={r.status}" for r in bad.itertuples()) if len(bad) else ""))
    return 0 if bad.empty else 1


if __name__ == "__main__":
    sys.exit(main())

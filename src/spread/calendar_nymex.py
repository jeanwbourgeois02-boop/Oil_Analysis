"""
NYMEX (CME Group) trading calendar for the RB/HO spread pipeline.

Pure Python (stdlib only), no external calendar library. Used for

* the expected last trading day of a contract (CME Rulebook Ch. 150 / 191:
  RB and HO trading terminates on the last business day of the month PRIOR to
  the delivery month),
* validation of the Bloomberg export (does the data end on the expected day?),
* gap checks along the vintage chain,
* the synthetic test data.

Holiday set (exchange closed, no settlement)
-------------------------------------------
New Year's Day, Martin Luther King Day (3rd Mon Jan), Presidents' Day (3rd Mon
Feb), Good Friday (Easter - 2 days, Easter via the Anonymous Gregorian
algorithm), Memorial Day (last Mon May), Juneteenth (19 Jun, observed by CME
since 2022), Independence Day (4 Jul), Labor Day (1st Mon Sep), Thanksgiving
(4th Thu Nov), Christmas (25 Dec).

Observed rules: Saturday -> preceding Friday, Sunday -> following Monday.
Exception (deliberate, matches actual CME/NYSE practice): New Year's Day on a
Saturday is NOT observed on the preceding Friday (31 Dec of the previous year
is a regular trading day, e.g. 2016-12-30, 2021-12-31).

Not modelled: ad-hoc closures (national days of mourning) and early closes.
"""
from __future__ import annotations

import sys
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

try:
    from spread import config
except ImportError:  # executed as a script: put src/ on the path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from spread import config

JUNETEENTH_FROM = 2022          # first year CME closed for Juneteenth
MONDAY, THURSDAY, SATURDAY, SUNDAY = 0, 3, 5, 6
DateLike = date | datetime | str


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def to_date(d: DateLike) -> date:
    """Coerce date / datetime / pandas Timestamp / ISO string to datetime.date."""
    if isinstance(d, datetime):          # datetime and pd.Timestamp (subclass)
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return date.fromisoformat(d[:10])
    if hasattr(d, "to_pydatetime"):      # numpy datetime64 wrapped in Timestamp etc.
        return d.to_pydatetime().date()
    raise TypeError(f"cannot interpret {d!r} as a date")


def easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) given weekday (Mon=0) of a month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def last_weekday(year: int, month: int, weekday: int) -> date:
    """Last given weekday (Mon=0) of a month."""
    last = _last_day_of_month(year, month)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _last_day_of_month(year: int, month: int) -> date:
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def observed(d: date) -> date:
    """Fixed-date holiday observance: Sat -> Fri, Sun -> Mon."""
    if d.weekday() == SATURDAY:
        return d - timedelta(days=1)
    if d.weekday() == SUNDAY:
        return d + timedelta(days=1)
    return d


# ----------------------------------------------------------------------------
# holidays
# ----------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _holidays_cached(year: int) -> frozenset[date]:
    hol: set[date] = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == SUNDAY:
        hol.add(new_year + timedelta(days=1))
    elif new_year.weekday() != SATURDAY:      # Saturday: not observed (see module doc)
        hol.add(new_year)
    hol.add(nth_weekday(year, 1, MONDAY, 3))              # MLK Day
    hol.add(nth_weekday(year, 2, MONDAY, 3))              # Presidents' Day
    hol.add(easter_sunday(year) - timedelta(days=2))      # Good Friday
    hol.add(last_weekday(year, 5, MONDAY))                # Memorial Day
    if year >= JUNETEENTH_FROM:
        hol.add(observed(date(year, 6, 19)))              # Juneteenth
    hol.add(observed(date(year, 7, 4)))                   # Independence Day
    hol.add(nth_weekday(year, 9, MONDAY, 1))              # Labor Day
    hol.add(nth_weekday(year, 11, THURSDAY, 4))           # Thanksgiving
    hol.add(observed(date(year, 12, 25)))                 # Christmas
    return frozenset(hol)


def nymex_holidays(year: int) -> set[date]:
    """Exchange holidays (weekday closures) of NYMEX in a calendar year."""
    return set(_holidays_cached(year))


# ----------------------------------------------------------------------------
# trading days
# ----------------------------------------------------------------------------
def is_trading_day(d: DateLike) -> bool:
    dd = to_date(d)
    return dd.weekday() < SATURDAY and dd not in _holidays_cached(dd.year)


def trading_days(start: DateLike, end: DateLike) -> list[date]:
    """All NYMEX trading days in [start, end] (inclusive), ascending."""
    s, e = to_date(start), to_date(end)
    out: list[date] = []
    d = s
    while d <= e:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


@lru_cache(maxsize=None)
def _trading_days_of_year(year: int) -> tuple[date, ...]:
    return tuple(trading_days(date(year, 1, 1), date(year, 12, 31)))


def trading_days_between(a: DateLike, b: DateLike) -> int:
    """Signed number of trading days strictly after `a` up to and including `b`.

    0 if a == b, positive if b is after a, negative if b is before a
    (then it is minus the number of trading days strictly after b up to and including a).
    """
    da, db = to_date(a), to_date(b)
    if da == db:
        return 0
    if db > da:
        return len(trading_days(da + timedelta(days=1), db))
    return -len(trading_days(db + timedelta(days=1), da))


def next_trading_day(d: DateLike, include: bool = False) -> date:
    dd = to_date(d)
    if not include:
        dd += timedelta(days=1)
    while not is_trading_day(dd):
        dd += timedelta(days=1)
    return dd


def previous_trading_day(d: DateLike, include: bool = False) -> date:
    dd = to_date(d)
    if not include:
        dd -= timedelta(days=1)
    while not is_trading_day(dd):
        dd -= timedelta(days=1)
    return dd


def last_trading_day_of_month(year: int, month: int) -> date:
    return previous_trading_day(_last_day_of_month(year, month), include=True)


def first_trading_day_of_month(year: int, month: int) -> date:
    return next_trading_day(date(year, month, 1), include=True)


def expected_expiry(contract_year: int, month_code: str) -> date:
    """Expected last trading day of an RB/HO contract (CME rule).

    Trading terminates on the last business day of the month preceding the
    delivery month: M (June) -> last trading day of May, Z (December) -> last
    trading day of November, F (January) -> last trading day of December of
    the previous year.
    """
    delivery_month = config.MONTH_CODES[month_code.upper()]
    year, month = contract_year, delivery_month - 1
    if month == 0:
        year, month = contract_year - 1, 12
    return last_trading_day_of_month(year, month)


def tdays_to(d: DateLike, target: DateLike, days: list[date] | None = None) -> int:
    """Trading days remaining from `d` (exclusive) to `target` (inclusive).

    Optional precomputed ascending list `days` covering [d, target] avoids
    recomputation in loops.
    """
    dd, tt = to_date(d), to_date(target)
    if days is None:
        return trading_days_between(dd, tt)
    return bisect_right(days, tt) - bisect_right(days, dd)


def _selftest() -> None:  # pragma: no cover - quick manual check
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2025) == date(2025, 4, 20)
    assert easter_sunday(2026) == date(2026, 4, 5)
    assert expected_expiry(2026, "M") == date(2026, 5, 29)   # Memorial Day 2026-05-25
    assert expected_expiry(2021, "M") == date(2021, 5, 28)   # Memorial Day 2021-05-31
    assert expected_expiry(2025, "Z") == date(2025, 11, 28)
    assert not is_trading_day(date(2023, 6, 19))
    assert is_trading_day(date(2021, 12, 31))
    assert not is_trading_day(date(2021, 12, 24))
    assert not is_trading_day(date(2020, 7, 3))
    print("calendar_nymex selftest ok")
    for v in config.VINTAGES:
        print(v, "Jun expiry", expected_expiry(v, "M"), " Dec expiry", expected_expiry(v, "Z"))


if __name__ == "__main__":
    _selftest()

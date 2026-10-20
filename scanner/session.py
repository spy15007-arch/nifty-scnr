"""
NSE market-session helpers (all clock times are IST).

WHY THIS EXISTS: during market hours the broker's daily candle for "today"
is still being built, so its volume is PARTIAL. The old scan compared that
partial volume with a full-day average, so a stock that was genuinely
running at 2x normal volume at 2:15 PM looked like it was only at ~1.5x and
could be rejected. `volume_fraction()` says what share of a normal day's
volume has typically traded by a given clock time; dividing today's partial
volume by it projects the full-day volume, so the volume test compares like
with like.

The profile below is an ESTIMATE of the usual NSE cash-market shape (busy
open, quiet middle of the day, busy last half hour). It is intentionally
simple and is only used for the volume-confirmation test - it does not
change prices or levels. It can be tuned in one place if live data shows it
is off.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

# (minutes after midnight IST, cumulative share of the day's volume traded by then)
_PROFILE = (
    (9 * 60 + 15, 0.00),
    (9 * 60 + 30, 0.11),
    (10 * 60 + 0, 0.19),
    (10 * 60 + 30, 0.26),
    (11 * 60 + 30, 0.38),
    (12 * 60 + 30, 0.48),
    (13 * 60 + 30, 0.58),
    (14 * 60 + 0, 0.64),
    (14 * 60 + 30, 0.72),
    (15 * 60 + 0, 0.81),
    (15 * 60 + 30, 1.00),
)
_MIN_FRACTION = 0.05   # never divide by less than this (guards the first minutes of the session)


def now_ist() -> datetime:
    return datetime.now(IST)


def _as_ist(now: datetime | None) -> datetime:
    if now is None:
        return now_ist()
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)   # naive values are treated as IST wall-clock time
    return now.astimezone(IST)


def session_state(now: datetime | None = None) -> str:
    """'pre_open' (before 9:15), 'open' (9:15-15:30 on a weekday) or 'closed'."""
    t = _as_ist(now)
    if t.weekday() >= 5:
        return "closed"
    clock = t.time()
    if clock < MARKET_OPEN:
        return "pre_open"
    if clock < MARKET_CLOSE:
        return "open"
    return "closed"


def volume_fraction(now: datetime | None = None) -> float:
    """
    Share (0.05-1.0) of a normal session's volume expected to have traded by
    `now`. 1.0 outside market hours. Only meaningful when the last daily bar
    really is today's in-progress bar - see `bar_is_partial`.
    """
    t = _as_ist(now)
    if session_state(t) != "open":
        return 1.0
    minute = t.hour * 60 + t.minute + t.second / 60.0
    prev_m, prev_f = _PROFILE[0]
    for m, f in _PROFILE[1:]:
        if minute <= m:
            frac = prev_f + (f - prev_f) * (minute - prev_m) / (m - prev_m)
            return max(_MIN_FRACTION, min(1.0, frac))
        prev_m, prev_f = m, f
    return 1.0


def bar_is_partial(last_bar_date, now: datetime | None = None) -> bool:
    """True when the newest daily bar is today's and the session is still running."""
    t = _as_ist(now)
    if session_state(t) != "open":
        return False
    if isinstance(last_bar_date, datetime):
        last_bar_date = last_bar_date.date()
    return last_bar_date == t.date()


def effective_volume_fraction(last_bar_date, now: datetime | None = None) -> float:
    """volume_fraction(now) when the last bar is the in-progress one, else 1.0."""
    return volume_fraction(now) if bar_is_partial(last_bar_date, now) else 1.0


def minutes_since_open(now: datetime | None = None) -> float:
    t = _as_ist(now)
    return (t.hour * 60 + t.minute + t.second / 60.0) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)


def ist_date(now: datetime | None = None) -> date:
    return _as_ist(now).date()

"""
One trade-simulation state machine, shared by the live scoreboard (tracker)
and the backtest, so the two can never disagree about how a trade is run.

THE PLAN (the one validated on 7 years of real NSE data, see reports/evidence/)
  * Entry
      "close": you buy now, at the price on the signal (BTST, market still open)
      "stop" : you leave a buy-stop order at `trigger`. It is live for
               `valid_sessions` sessions. If the stock opens above the trigger
               you pay the open; if it gaps more than `max_chase` beyond the
               trigger the trade is skipped (you would be chasing).
  * Stop     fill x (1 - stop_mult x ATR%)                  (default 2.5 ATR)
  * Trail    highest close since entry x (1 - trail_mult x ATR%), never lowered
             (default 5 ATR). The stop in force is the higher of the two.
  * Exit     stop/trail touched (a gap below it fills at the open), or after
             `max_hold` sessions at the close.
  * Costs    `cost` (default 0.35% round trip: STT, stamp duty, exchange and
             DP charges, brokerage and slippage) is deducted from every
             closed trade's return.
  * R        net return divided by the initial risk (stop_mult x ATR%), so
             "+2R" means a profit twice the amount risked.

Order of events inside one session is deliberately PESSIMISTIC: when the same
daily bar could have hit both the stop and a better price, the stop is assumed
to have been hit first; and a trade that fills and is stopped out on the same
day is booked as a loss at the stop.

Why a wide trailing stop and no fixed profit target: in the history, small
fixed targets and partial profit-taking both reduced the average result. The
gains came from the minority of trades that ran for weeks.

The step function takes one COMPLETED daily bar (open, high, low, close). It
must never be fed today's still-forming bar.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import Iterable

import config

PENDING, OPEN, CLOSED, UNFILLED = "PENDING", "OPEN", "CLOSED", "UNFILLED"
_NAN = float("nan")


@dataclass
class TradePlan:
    entry_mode: str                     # "close" or "stop"
    trigger: float                      # close-mode: the price paid; stop-mode: the buy-stop price
    atr_pct: float                      # ATR(14) / close on the signal bar, as a fraction (0.03 = 3%)
    stop_mult: float = config.STOP_ATR_MULT
    trail_mult: float = config.TRAIL_ATR_MULT
    valid_sessions: int = config.ENTRY_VALID_SESSIONS
    max_hold: int = config.MAX_HOLD_SESSIONS
    max_chase: float = config.MAX_CHASE_PCT
    cost: float = config.ROUND_TRIP_COST

    @property
    def risk_pct(self) -> float:
        """Initial risk as a fraction of the entry price."""
        return self.stop_mult * self.atr_pct


@dataclass
class TradeState:
    status: str = PENDING
    sessions: int = 0                   # completed sessions processed since the signal (1 = the first one after it)
    fill_n: int = -1                    # the session on which the entry filled (0 = filled at the signal itself)
    fill_px: float = _NAN
    init_stop: float = _NAN
    trail: float = _NAN                 # the stop level in force (starts equal to init_stop)
    hc: float = _NAN                    # highest close since the fill (at least the fill price)
    exit_n: int = -1
    exit_px: float = _NAN
    exit_reason: str = ""

    @property
    def held(self) -> int:
        """Sessions since the fill day (0 on the fill day)."""
        return self.sessions - self.fill_n if self.fill_n >= 0 else 0


# --------------------------------------------------------------------------- #
def _arm(plan: TradePlan, st: TradeState, fill_px: float, close_px: float) -> None:
    """Set the stop and trail once the entry has filled."""
    st.fill_px = fill_px
    st.init_stop = fill_px * (1.0 - plan.risk_pct)
    st.hc = max(fill_px, close_px)
    st.trail = max(st.init_stop, st.hc * (1.0 - plan.trail_mult * plan.atr_pct))


def new_state(plan: TradePlan) -> TradeState:
    """A close-mode trade is open from the start; a stop-mode trade waits for its trigger."""
    if plan.entry_mode == "close":
        st = TradeState(status=OPEN, sessions=0, fill_n=0)
        _arm(plan, st, plan.trigger, plan.trigger)
        return st
    if plan.entry_mode == "stop":
        return TradeState(status=PENDING)
    raise ValueError(f"unknown entry_mode {plan.entry_mode!r}")


def _close(st: TradeState, px: float, reason: str) -> TradeState:
    st.status = CLOSED
    st.exit_px = px
    st.exit_reason = reason
    st.exit_n = st.sessions
    return st


def step(plan: TradePlan, st: TradeState, o: float, h: float, l: float, c: float) -> TradeState:
    """
    Advance the trade by ONE completed session. Mutates and returns `st`.
    A finished trade (CLOSED / UNFILLED) is returned unchanged.
    """
    if st.status in (CLOSED, UNFILLED):
        return st
    st.sessions += 1

    # ---- waiting for the buy-stop to trigger ---------------------------------
    if st.status == PENDING:
        if h >= plan.trigger:
            px = o if o >= plan.trigger else plan.trigger
            if px > plan.trigger * (1.0 + plan.max_chase):
                st.status = UNFILLED
                st.exit_reason = "gapped_past_trigger"
                return st
            st.status = OPEN
            st.fill_n = st.sessions
            _arm(plan, st, px, c)
            if l <= st.init_stop:       # pessimistic: filled and stopped out the same day
                return _close(st, st.init_stop, "same_day_stop")
            return st
        if st.sessions >= plan.valid_sessions:
            st.status = UNFILLED
            st.exit_reason = "expired"
        return st

    # ---- an open position ----------------------------------------------------
    if o <= st.trail:                   # opened at/below the stop: filled at the open
        return _close(st, o, "gap_stop")
    if l <= st.trail:
        return _close(st, st.trail, "stop" if st.trail <= st.init_stop * (1 + 1e-12) else "trail")
    st.hc = max(st.hc, c)
    st.trail = max(st.trail, st.hc * (1.0 - plan.trail_mult * plan.atr_pct))
    if st.sessions - st.fill_n >= plan.max_hold:
        return _close(st, c, "time")
    return st


def replay(plan: TradePlan, bars: Iterable[tuple[float, float, float, float]], st: TradeState | None = None) -> TradeState:
    """Run a trade over (open, high, low, close) bars that come AFTER the signal."""
    st = new_state(plan) if st is None else st
    for o, h, l, c in bars:
        if st.status in (CLOSED, UNFILLED):
            break
        step(plan, st, float(o), float(h), float(l), float(c))
    return st


# --------------------------------------------------------------------------- #
def outcome(plan: TradePlan, st: TradeState, mark_px: float | None = None) -> dict | None:
    """
    Return on the entry price, after costs, and in R.
    A CLOSED trade uses its exit; an OPEN trade is marked at `mark_px` (the latest close).
    PENDING / UNFILLED trades have no outcome (they never held a position).
    """
    if st.status == CLOSED:
        px = st.exit_px
    elif st.status == OPEN and mark_px is not None and math.isfinite(mark_px):
        px = mark_px
    else:
        return None
    gross = px / st.fill_px - 1.0
    net = gross - plan.cost
    risk = plan.risk_pct
    return {"gross": gross, "net": net, "R": net / risk if risk > 0 else _NAN}


def peak_r(plan: TradePlan, st: TradeState) -> float:
    """Best closing level reached so far, in R (before costs)."""
    if st.status not in (OPEN, CLOSED) or not math.isfinite(st.hc) or not math.isfinite(st.fill_px):
        return _NAN
    risk = plan.risk_pct
    return (st.hc / st.fill_px - 1.0) / risk if risk > 0 else _NAN


# --------------------------------------------------------------------------- #
# (De)serialisation for the ledger
def plan_to_dict(plan: TradePlan) -> dict:
    return asdict(plan)


def state_to_dict(st: TradeState) -> dict:
    return asdict(st)


_PLAN_STR = ("entry_mode",)
_PLAN_INT = ("valid_sessions", "max_hold")
_STATE_STR = ("status", "exit_reason")
_STATE_INT = ("sessions", "fill_n", "exit_n")


def _coerce(d: dict, names: Iterable[str], str_keys: tuple, int_keys: tuple) -> dict:
    """Blank / missing values are left out (the dataclass default applies); the rest are converted
    from whatever a CSV round-trip produced (strings) to the field's real type."""
    kw: dict = {}
    for name in names:
        v = d.get(name)
        if v is None or (isinstance(v, str) and v.strip() == "") or (isinstance(v, float) and math.isnan(v) and name in str_keys + int_keys):
            continue
        if name in str_keys:
            kw[name] = str(v)
        elif name in int_keys:
            kw[name] = int(float(v))
        else:
            kw[name] = float(v)
    return kw


def plan_from_dict(d: dict) -> TradePlan:
    return TradePlan(**_coerce(d, [f.name for f in fields(TradePlan)], _PLAN_STR, _PLAN_INT))


def state_from_dict(d: dict) -> TradeState:
    return TradeState(**_coerce(d, [f.name for f in fields(TradeState)], _STATE_STR, _STATE_INT))

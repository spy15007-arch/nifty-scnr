"""
Translates an index-level setup (NIFTY/BANKNIFTY/etc) into an options
trade plan: suggested strike, direction, and spot-level entry/stop/5
targets from the same levels engine used for stocks.

Covers BOTH directions per index:
  - CE (bullish): computed off resistance breakout, for uptrends
  - PE (bearish): computed off support breakdown, for corrections/downtrends
Both are always attempted and returned when valid.

IMPORTANT LIMITATION: this does NOT fetch live option premiums or
Greeks - none of the broker clients are wired to an option chain yet.
What you get here is the SPOT trigger/target levels plus a suggested
strike; check the live premium for that strike yourself before entering.
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from scanner.levels import compute_trade_levels, TradeLevels
from scanner.trade_style import classify_index_execution, ExecutionPlan

STRIKE_INTERVALS = {
    "NIFTY": 50,
    "BANKNIFTY": 100,
    "FINNIFTY": 50,
    "MIDCPNIFTY": 25,
    "SENSEX": 100,
}


@dataclass
class IndexOptionPlan:
    index: str
    direction: str
    suggested_strike: int
    strike_type: str
    spot_entry: float
    spot_stop: float
    spot_targets: list[float]
    risk_rewards: list[float]
    execution: ExecutionPlan
    caveats: list[str]


def _build_plan(index_symbol: str, levels: TradeLevels, execution: ExecutionPlan) -> IndexOptionPlan:
    interval = STRIKE_INTERVALS.get(index_symbol.upper(), 50)
    atm_strike = round(levels.entry_trigger / interval) * interval

    if levels.direction == "bullish":
        suggested_strike = atm_strike + interval
        direction_label = "CE (bullish)"
        strike_note = f"{interval}-pt OTM call (ATM would be {int(atm_strike)})"
    else:
        suggested_strike = atm_strike - interval
        direction_label = "PE (bearish)"
        strike_note = f"{interval}-pt OTM put (ATM would be {int(atm_strike)})"

    caveats = [
        "Spot-level plan only - fetch the live premium for the suggested strike from "
        "your broker's option chain before entering; premium is NOT computed here.",
        "Options decay fast (theta) - if spot hasn't moved meaningfully within the entry "
        "window, consider exiting even before the spot stop is hit.",
    ]
    if execution.style.value == "INTRADAY":
        caveats.append("Intraday index options move fast both ways - use a hard premium stop, not just the spot stop.")
    if execution.style.value == "BTST":
        caveats.append("Overnight index option carry has real gap risk on either side - size smaller than an intraday-confirmed trade.")

    return IndexOptionPlan(
        index=index_symbol,
        direction=direction_label,
        suggested_strike=int(suggested_strike),
        strike_type=strike_note,
        spot_entry=levels.entry_trigger,
        spot_stop=levels.stop_loss,
        spot_targets=levels.targets,
        risk_rewards=levels.risk_rewards,
        execution=execution,
        caveats=caveats,
    )


def recommend_index_options(index_symbol: str, df: pd.DataFrame, features: dict) -> list[IndexOptionPlan]:
    """
    Returns a list with 0, 1, or 2 plans: a CE plan if a valid bullish
    breakout setup exists, a PE plan if a valid bearish breakdown setup
    exists. Both are computed independently.
    """
    plans = []

    bullish_levels = compute_trade_levels(df, direction="bullish")
    if bullish_levels:
        execution = classify_index_execution(df, features, bullish_levels, "bullish")
        plans.append(_build_plan(index_symbol, bullish_levels, execution))

    bearish_levels = compute_trade_levels(df, direction="bearish")
    if bearish_levels:
        execution = classify_index_execution(df, features, bearish_levels, "bearish")
        plans.append(_build_plan(index_symbol, bearish_levels, execution))

    return plans

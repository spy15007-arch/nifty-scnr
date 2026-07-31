"""
Translates an index-level breakout setup (NIFTY/BANKNIFTY/etc) into an
options trade plan: suggested strike, direction, and spot-level
entry/stop/targets carried over from the same scanner/levels engine
used for stocks.

IMPORTANT LIMITATION: this does NOT fetch live option premiums or
Greeks - none of the broker clients in data/brokers/ are wired to an
option chain yet. What you get here is the SPOT trigger/target levels
plus a suggested strike; you check the live premium for that strike
yourself before entering. Adding a live option-chain feed (for real
premium targets, IV, delta) would be the natural next step if this
proves useful.

Also currently BULLISH (CE) ONLY - the underlying scanner filters
(coiled_at_resistance, fibonacci_reclaim, ema_alignment) are all
built to detect upside breakouts. A mirror set of bearish/breakdown
filters would be needed for PE recommendations - not implemented yet.
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from scanner.levels import TradeLevels
from scanner.trade_style import ExecutionPlan

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
    spot_target_1: float
    spot_target_2: float
    spot_target_3: float
    execution: ExecutionPlan
    caveats: list[str]


def recommend_index_option(index_symbol: str, levels: TradeLevels | None,
                            execution: ExecutionPlan) -> IndexOptionPlan | None:
    if levels is None:
        return None

    interval = STRIKE_INTERVALS.get(index_symbol.upper(), 50)
    atm_strike = round(levels.entry_trigger / interval) * interval
    # slightly OTM: cheaper premium, defined risk, still benefits from a real move
    suggested_strike = atm_strike + interval

    caveats = [
        "Spot-level plan only - fetch the live premium for the suggested strike from "
        "your broker's option chain before entering; premium is NOT computed here.",
        "Weekly options decay fast (theta) - if spot hasn't moved meaningfully by midday, "
        "consider exiting even before the spot stop is hit.",
        "Bullish (CE) setups only - bearish/breakdown (PE) scanning isn't implemented yet.",
    ]
    if execution.style.value == "INTRADAY":
        caveats.append("Intraday index options move fast both ways - use a hard premium stop, not just the spot stop.")

    return IndexOptionPlan(
        index=index_symbol,
        direction="CE (bullish)",
        suggested_strike=int(suggested_strike),
        strike_type=f"{interval}-pt OTM (ATM would be {int(atm_strike)})",
        spot_entry=levels.entry_trigger,
        spot_stop=levels.stop_loss,
        spot_target_1=levels.target_1,
        spot_target_2=levels.target_2,
        spot_target_3=levels.target_3,
        execution=execution,
        caveats=caveats,
    )

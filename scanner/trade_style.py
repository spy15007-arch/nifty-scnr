"""
Classifies each candidate into a holding-period style - INTRADAY,
BTST (Buy Today Sell Tomorrow), or SWING (up to ~1 week) - and attaches
NSE-session-aware execution guidance. This is what makes a scan output
actually executable rather than just a ranked list.

Classification logic (heuristic, not a model - these are structural
rules about HOW CLOSE a setup is to triggering, not predictions):

  INTRADAY: price is already extremely close to the trigger AND
    today's relative volume is already elevated - the breakout could
    realistically happen in the current or next session.

  BTST: price is close to the trigger and the setup is well-formed
    (EMA-stacked, coiled at resistance) but volume hasn't surged YET -
    classic "wait for a late-session push through resistance, carry
    overnight" setup.

  SWING: price is further from the trigger but the broader structure
    (relative strength, volatility squeeze, EMA trend) is strong -
    needs more days to actually reach and clear the trigger.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import pandas as pd


class TradeStyle(str, Enum):
    INTRADAY = "INTRADAY"
    BTST = "BTST"
    SWING = "SWING"


@dataclass
class ExecutionPlan:
    style: TradeStyle
    reasoning: str
    entry_window_ist: str
    exit_window_ist: str
    max_hold: str


# NSE session reference (IST)
MARKET_PRE_OPEN = "09:00–09:15"
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
LATE_SESSION_START = "14:45"       # window where BTST triggers are watched for
SAFE_INTRADAY_EXIT_START = "15:10" # start squaring off intraday positions by here
SAFE_INTRADAY_EXIT_END = "15:20"   # hard cutoff, ahead of 15:30 close volatility


def classify_trade_style(df: pd.DataFrame, features: dict, levels) -> ExecutionPlan:
    if levels is None:
        return ExecutionPlan(
            style=TradeStyle.SWING,
            reasoning="No valid trigger levels - default to swing watch, do not execute.",
            entry_window_ist="N/A", exit_window_ist="N/A", max_hold="N/A",
        )

    current_price = df["close"].iloc[-1]
    distance_to_trigger = (levels.entry_trigger - current_price) / current_price
    rel_vol_score = features.get("relative_volume_score", 0)
    ema_score = features.get("ema_alignment_score", 0)
    coiled_score = features.get("coiled_at_resistance_score", 0)
    rs_score = features.get("relative_strength_score", 0)
    vsq_score = features.get("volatility_squeeze_score", 0)

    very_close = distance_to_trigger <= 0.008   # within ~0.8% of trigger
    close = distance_to_trigger <= 0.02          # within ~2%

    if very_close and rel_vol_score >= 0.5:
        return ExecutionPlan(
            style=TradeStyle.INTRADAY,
            reasoning=(
                f"Only {distance_to_trigger:.2%} below trigger with volume already running "
                f"{rel_vol_score:.0%} above its recent-average score - breakout can trigger intraday."
            ),
            entry_window_ist=f"{MARKET_OPEN}–11:30 (avoid chasing in the last 30 min unless trigger just broke)",
            exit_window_ist=f"Same day, square off by {SAFE_INTRADAY_EXIT_START}–{SAFE_INTRADAY_EXIT_END}",
            max_hold="Same trading session",
        )

    if close and (ema_score >= 0.4 or coiled_score >= 0.4):
        return ExecutionPlan(
            style=TradeStyle.BTST,
            reasoning=(
                f"{distance_to_trigger:.2%} below trigger with a well-formed setup "
                f"(EMA/resistance coil) but volume hasn't surged yet - watch for a late push."
            ),
            entry_window_ist=f"{LATE_SESSION_START}–{MARKET_CLOSE}, only if trigger breaks with volume confirmation",
            exit_window_ist=f"Next session, at open or first target hit — by ~10:15 to limit gap-down risk",
            max_hold="Overnight (1 session)",
        )

    return ExecutionPlan(
        style=TradeStyle.SWING,
        reasoning=(
            f"{distance_to_trigger:.2%} below trigger; broader structure supportive "
            f"(RS score {rs_score:.2f}, squeeze score {vsq_score:.2f}) but needs more days to reach trigger."
        ),
        entry_window_ist=f"Any session, on confirmed close above trigger ({MARKET_OPEN}–{MARKET_CLOSE})",
        exit_window_ist="Trail stop to breakeven after Target 1; review daily",
        max_hold="Up to 5 trading days (1 week)",
    )

"""
Classifies each candidate into a holding-period style - INTRADAY,
BTST (Buy Today Sell Tomorrow), or SWING (up to ~1 week) - and attaches
NSE-session-aware execution guidance.
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


MARKET_PRE_OPEN = "09:00–09:15"
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:30"
LATE_SESSION_START = "14:45"
SAFE_INTRADAY_EXIT_START = "15:10"
SAFE_INTRADAY_EXIT_END = "15:20"


def classify_trade_style(df: pd.DataFrame, features: dict, levels) -> ExecutionPlan:
    if levels is None:
        return ExecutionPlan(
            style=TradeStyle.SWING,
            reasoning="No valid trigger levels - watch only, do not execute.",
            entry_window_ist="N/A", exit_window_ist="N/A", max_hold="N/A",
        )

    current_price = df["close"].iloc[-1]
    distance_to_trigger = (levels.entry_trigger - current_price) / current_price
    rel_vol_score = features.get("relative_volume_score", 0)
    ema_score = features.get("ema_alignment_score", 0)
    coiled_score = features.get("coiled_at_resistance_score", 0)
    rs_score = features.get("relative_strength_score", 0)
    vsq_score = features.get("volatility_squeeze_score", 0)

    very_close = distance_to_trigger <= 0.008
    close = distance_to_trigger <= 0.02

    if very_close and rel_vol_score >= 0.5:
        return ExecutionPlan(
            style=TradeStyle.INTRADAY,
            reasoning=(
                f"Only {distance_to_trigger:.2%} below trigger, volume already elevated - "
                f"can break out today."
            ),
            entry_window_ist="Enter 09:15–11:30 on confirmed break",
            exit_window_ist=f"Exit by {SAFE_INTRADAY_EXIT_START}–{SAFE_INTRADAY_EXIT_END} same day",
            max_hold="Same session",
        )

    if close and (ema_score >= 0.4 or coiled_score >= 0.4):
        return ExecutionPlan(
            style=TradeStyle.BTST,
            reasoning=(
                f"{distance_to_trigger:.2%} below trigger, setup well-formed - "
                f"watch for a late push into the close."
            ),
            entry_window_ist=f"Enter {LATE_SESSION_START}–{MARKET_CLOSE} on confirmed break",
            exit_window_ist="Exit next day at open or first target",
            max_hold="Overnight (1 session)",
        )

    return ExecutionPlan(
        style=TradeStyle.SWING,
        reasoning=(
            f"{distance_to_trigger:.2%} below trigger; structure supportive "
            f"(RS {rs_score:.2f}, squeeze {vsq_score:.2f}) - needs a few more days."
        ),
        entry_window_ist="Enter anytime today once trigger breaks",
        exit_window_ist="Trail stop to breakeven after Target 1",
        max_hold="Up to 5 trading days",
    )


def classify_index_execution(df: pd.DataFrame, features: dict, levels, direction: str) -> ExecutionPlan:
    """
    Direction-aware timing classifier for index options (CE/PE). Kept
    separate from classify_trade_style (bullish-only, tuned for stocks)
    to avoid any risk of changing proven stock-scan behavior.
    """
    if levels is None:
        return ExecutionPlan(
            style=TradeStyle.SWING,
            reasoning="No valid trigger levels - watch only.",
            entry_window_ist="N/A", exit_window_ist="N/A", max_hold="N/A",
        )

    current_price = df["close"].iloc[-1]
    if direction == "bullish":
        distance_to_trigger = (levels.entry_trigger - current_price) / current_price
    else:
        distance_to_trigger = (current_price - levels.entry_trigger) / current_price

    rel_vol_score = features.get("relative_volume_score", 0)
    vsq_score = features.get("volatility_squeeze_score", 0)

    very_close = distance_to_trigger <= 0.008
    close = distance_to_trigger <= 0.02
    verb = "breakout" if direction == "bullish" else "breakdown"

    if very_close and rel_vol_score >= 0.5:
        return ExecutionPlan(
            style=TradeStyle.INTRADAY,
            reasoning=f"Only {distance_to_trigger:.2%} from trigger, volume elevated - {verb} likely today.",
            entry_window_ist="Enter 09:15–11:30 on confirmed break",
            exit_window_ist=f"Exit by {SAFE_INTRADAY_EXIT_START}–{SAFE_INTRADAY_EXIT_END} - don't hold hoping",
            max_hold="Same session only",
        )

    if close and vsq_score >= 0.35:
        return ExecutionPlan(
            style=TradeStyle.BTST,
            reasoning=f"{distance_to_trigger:.2%} from trigger, coiling - watch for a late push.",
            entry_window_ist=f"Enter {LATE_SESSION_START}–{MARKET_CLOSE} on confirmed {verb}",
            exit_window_ist="Exit early next day - theta decay is real overnight",
            max_hold="Overnight, higher risk than stock BTST",
        )

    return ExecutionPlan(
        style=TradeStyle.SWING,
        reasoning=f"{distance_to_trigger:.2%} from trigger - not close enough yet, keep watching.",
        entry_window_ist="Watch only until price nears trigger",
        exit_window_ist="N/A",
        max_hold="Not a swing instrument - re-check next session",
    )

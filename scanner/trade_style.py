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

    very_close = distance_to_trigger <= 0.008
    close = distance_to_trigger <= 0.02

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


def classify_index_execution(df: pd.DataFrame, features: dict, levels, direction: str) -> ExecutionPlan:
    """
    Direction-aware timing classifier for index options (CE/PE). Kept
    separate from classify_trade_style (bullish-only, tuned for stocks)
    to avoid any risk of changing proven stock-scan behavior.

    Uses only direction-agnostic signals (relative volume, volatility
    squeeze) since the bullish-only EMA/coiled-at-resistance scores
    aren't meaningful evidence for a bearish/breakdown setup.
    """
    if levels is None:
        return ExecutionPlan(
            style=TradeStyle.SWING,
            reasoning="No valid trigger levels - default to watch, do not execute.",
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
            reasoning=(
                f"Only {distance_to_trigger:.2%} from trigger with volume already elevated - "
                f"{verb} can trigger intraday."
            ),
            entry_window_ist=f"{MARKET_OPEN}–11:30, or immediately on confirmed {verb}",
            exit_window_ist=f"Same day, square off by {SAFE_INTRADAY_EXIT_START}–{SAFE_INTRADAY_EXIT_END} - options decay fast, don't hold hoping",
            max_hold="Same trading session (intraday only - do not carry index option premium overnight on a hope)",
        )

    if close and vsq_score >= 0.35:
        return ExecutionPlan(
            style=TradeStyle.BTST,
            reasoning=f"{distance_to_trigger:.2%} from trigger with volatility coiling - watch for a late push.",
            entry_window_ist=f"{LATE_SESSION_START}–{MARKET_CLOSE}, only on confirmed {verb} with volume",
            exit_window_ist="Next session at open - index options lose value fast overnight, exit early if no follow-through by 09:45",
            max_hold="Overnight, but treat as higher risk than stock BTST due to theta decay",
        )

    return ExecutionPlan(
        style=TradeStyle.SWING,
        reasoning=f"{distance_to_trigger:.2%} from trigger - not close enough for a same-day options play yet; keep on watchlist.",
        entry_window_ist=f"Watch only until price closes in on the trigger ({MARKET_OPEN}–{MARKET_CLOSE})",
        exit_window_ist="N/A - not a current options entry, re-check next session",
        max_hold="Not applicable yet - index options aren't a multi-day swing instrument the way stocks can be",
    )

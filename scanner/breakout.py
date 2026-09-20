"""
Hard entry-timing gates - REQUIRED before a candidate is considered at
all (not optional scoring signals).
"""
from __future__ import annotations
import pandas as pd


def check_pre_breakout_setup(df: pd.DataFrame, rsi_period: int = 14) -> dict:
    """
    PRIMARY gate (both modes): requires RSI in the 45-65 'building'
    zone, rising vs 5 days ago, hard-excluded above 68 (already-strong,
    possibly overextended momentum).
    """
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))

    current_rsi = round(rsi.iloc[-1], 1) if not pd.isna(rsi.iloc[-1]) else None
    rsi_5d_ago = rsi.iloc[-6] if len(rsi) > 5 and not pd.isna(rsi.iloc[-6]) else None

    if current_rsi is None or rsi_5d_ago is None:
        return {"flagged": False, "current_rsi": current_rsi, "reason": "insufficient RSI history"}

    in_building_zone = 45 <= current_rsi <= 65
    is_rising = current_rsi > rsi_5d_ago
    not_overextended = current_rsi <= 68

    flagged = in_building_zone and is_rising and not_overextended
    return {
        "flagged": flagged,
        "current_rsi": current_rsi,
        "reason": f"RSI {current_rsi} ({'in' if in_building_zone else 'outside'} building zone, "
                  f"{'rising' if is_rising else 'falling'} vs 5d ago)"
    }


def near_recent_base(df: pd.DataFrame, lookback: int = 25, max_distance_from_base_pct: float = 0.08) -> dict:
    """
    SWING-specific second gate: requires current price to be
    reasonably close to its own recent swing low - i.e. the stock
    hasn't already run up significantly before being flagged. Backtest-
    confirmed: genuinely needs ~30 days to develop once flagged - fits
    a patient swing hold, NOT an overnight BTST premise.
    """
    window = df.tail(lookback)
    recent_low = window['low'].min()
    current_price = df['close'].iloc[-1]
    distance_from_base_pct = (current_price - recent_low) / recent_low

    flagged = distance_from_base_pct <= max_distance_from_base_pct
    return {
        "flagged": flagged,
        "distance_from_base_pct": round(distance_from_base_pct, 4),
        "reason": f"{distance_from_base_pct:.1%} above its {lookback}-day low "
                  f"({'still near base' if flagged else 'already moved too far from base'})"
    }


def btst_momentum_setup(
    df: pd.DataFrame,
    prior_lookback: int = 15,
    max_prior_distance_from_base_pct: float = 0.08,
    min_today_volume_ratio: float = 2.0,
    min_today_gain_pct: float = 0.02,
    max_today_gain_pct: float = 0.10,
    min_close_position_in_range: float = 0.65,
) -> dict:
    """
    BTST-specific second gate: the opposite emphasis from
    near_recent_base. BTST's whole premise is an overnight-to-next-day
    move, so "still coiled, hasn't moved yet" (near_recent_base's
    logic) is the wrong fit - a stock that needs ~30 days to develop
    isn't a BTST candidate. Instead requires: the PRIOR days (before
    today) were near the base - confirming this isn't an already-
    extended, late-stage chase - but TODAY specifically shows genuine
    breakout confirmation: strong volume, a meaningful (not blow-off)
    gain, and closing strong in the day's range. Both the setup AND
    today's confirmation are required together.
    """
    if len(df) < prior_lookback + 1:
        return {"flagged": False, "reason": "insufficient history"}

    prior_df = df.iloc[:-1].tail(prior_lookback)
    prior_low = prior_df['low'].min()
    prior_close = prior_df['close'].iloc[-1]
    prior_distance_pct = (prior_close - prior_low) / prior_low
    was_near_base = prior_distance_pct <= max_prior_distance_from_base_pct

    today = df.iloc[-1]
    avg_volume = df['volume'].tail(prior_lookback + 1).iloc[:-1].mean()
    today_volume_ratio = today['volume'] / avg_volume if avg_volume > 0 else 0
    today_gain_pct = (today['close'] - prior_close) / prior_close
    today_range = (today['high'] - today['low']) + 1e-10
    close_position_in_range = (today['close'] - today['low']) / today_range

    strong_volume = today_volume_ratio >= min_today_volume_ratio
    meaningful_gain = min_today_gain_pct <= today_gain_pct <= max_today_gain_pct
    strong_close = close_position_in_range >= min_close_position_in_range

    flagged = was_near_base and strong_volume and meaningful_gain and strong_close
    return {
        "flagged": flagged,
        "was_near_base": was_near_base,
        "today_volume_ratio": round(today_volume_ratio, 2),
        "today_gain_pct": round(today_gain_pct, 4),
        "close_position_in_range": round(close_position_in_range, 2),
        "reason": (
            f"Today: {today_gain_pct:+.1%} on {today_volume_ratio:.1f}x volume, "
            f"closed at {close_position_in_range:.0%} of day's range"
            + (" - genuine breakout day from a real base" if flagged else " - doesn't meet BTST momentum criteria")
        ),
    }

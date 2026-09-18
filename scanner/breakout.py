"""
Hard entry-timing gates - both REQUIRED before a candidate is
considered at all (not optional scoring signals).
"""
from __future__ import annotations
import pandas as pd


def check_pre_breakout_setup(df: pd.DataFrame, rsi_period: int = 14) -> dict:
    """
    PRIMARY gate: requires RSI in the 45-65 'building' zone, rising vs
    5 days ago, hard-excluded above 68 (already-strong, possibly
    overextended momentum).
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
    SECOND hard gate: requires current price to be reasonably close to
    its own recent swing low - i.e. the stock hasn't already run up
    significantly before being flagged. Without this, a stock can run
    up 10-15%, cool back into the RSI 45-65 zone on a pullback, and
    pass the RSI gate while having already made most of its move - the
    exact failure mode this exists to close. A genuine retest (breaks
    out, pulls back to retest old resistance, bounces) naturally
    passes too, since the pullback itself creates a new recent low -
    no separate retest detector needed.
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

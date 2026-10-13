"""
Hard entry-timing gates - REQUIRED before a candidate is considered at
all (not optional scoring signals).

ADDED 2026-10-12: classify_setup_stage() - a structural TAG (not a new
hard gate) that answers the user's actual question - "has this just
broken out, or is it still coiled about to?" - using genuine
resistance-level structure rather than the proxies the existing gates
use (today's candle shape for BTST, distance-from-low for SWING).
Both existing gates stay exactly as backtested; this just labels
what's already passed them, so nothing's filtering behavior changes.
"""
from __future__ import annotations
import pandas as pd

from scanner.levels import _find_nearest_resistance_above


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


def classify_setup_stage(
    df: pd.DataFrame,
    lookback: int = 100,
    swing_window: int = 3,
    fresh_lookback_days: int = 3,
    fresh_max_extension_pct: float = 0.05,
    pre_breakout_max_distance_pct: float = 0.05,
    min_breakout_volume_ratio: float = 1.5,
) -> dict:
    """
    Tags a candidate's structural stage against a genuine swing-high
    resistance level - independent of scan mode, applied to whatever
    already passed the real gates above. NOT a filter: every candidate
    gets a label, "neither" included.

      - "fresh_breakout": a resistance level that was still above price
        as of `fresh_lookback_days` ago has since been closed above,
        confirmed by volume, and price hasn't extended more than
        `fresh_max_extension_pct` past it yet - still chaseable, not a
        stale move you'd be late to.
      - "pre_breakout": price is still BELOW the nearest swing
        resistance, but within `pre_breakout_max_distance_pct` of it -
        coiled right under the ceiling, not triggered yet.
      - "neither": no resistance level close enough in either
        direction right now.

    Uses the SAME nearest-swing-resistance logic as scanner.levels
    (not a stale single highest-high-in-window), so "fresh breakout"/
    "pre-breakout" here means the same thing as the entry triggers
    already being computed for these candidates - not a second,
    inconsistent notion of "breakout."
    """
    if len(df) < 30:
        return {"setup_stage": "neither", "resistance_level": None, "distance_pct": None,
                "volume_ratio": None, "reason": "insufficient history"}

    effective_lookback = min(lookback, len(df))
    current_price = df["close"].iloc[-1]

    # --- fresh_breakout: a level that WAS resistance as of
    # fresh_lookback_days ago, now cleared ---
    crossed_level = None
    prior_cutoff = len(df) - fresh_lookback_days
    min_prior_bars = swing_window * 2 + 20  # enough for swing detection + a volume baseline
    if prior_cutoff >= min_prior_bars:
        prior_df = df.iloc[:prior_cutoff]
        price_before_fresh_window = prior_df["close"].iloc[-1]
        window = prior_df.tail(effective_lookback).reset_index(drop=True)
        highs = window["high"]
        candidates = []
        for i in range(swing_window, len(highs) - swing_window):
            seg = highs.iloc[i - swing_window: i + swing_window + 1]
            level = highs.iloc[i]
            if level == seg.max() and level <= current_price and level >= price_before_fresh_window:
                candidates.append(level)
        crossed_level = max(candidates) if candidates else None

    if crossed_level is not None:
        extension_pct = (current_price - crossed_level) / crossed_level
        if 0 <= extension_pct <= fresh_max_extension_pct:
            fresh_window_df = df.tail(fresh_lookback_days)
            baseline_vol = df["volume"].iloc[:-fresh_lookback_days].tail(20).mean()
            peak_vol_in_window = fresh_window_df["volume"].max()
            volume_ratio = (peak_vol_in_window / baseline_vol) if baseline_vol > 0 else 0
            if volume_ratio >= min_breakout_volume_ratio:
                return {
                    "setup_stage": "fresh_breakout",
                    "resistance_level": round(float(crossed_level), 2),
                    "distance_pct": round(float(extension_pct), 4),
                    "volume_ratio": round(float(volume_ratio), 2),
                    "reason": (
                        f"Closed {extension_pct:.1%} above a resistance level ({round(float(crossed_level), 2)}) "
                        f"broken within the last {fresh_lookback_days} sessions, on {volume_ratio:.1f}x volume"
                    ),
                }

    # --- pre_breakout: still below the nearest resistance, but close ---
    resistance_above = _find_nearest_resistance_above(df, current_price, effective_lookback, swing_window)
    if resistance_above is not None:
        distance_pct = (resistance_above - current_price) / resistance_above
        if 0 <= distance_pct <= pre_breakout_max_distance_pct:
            return {
                "setup_stage": "pre_breakout",
                "resistance_level": round(float(resistance_above), 2),
                "distance_pct": round(float(distance_pct), 4),
                "volume_ratio": None,
                "reason": f"{distance_pct:.1%} below resistance at {round(float(resistance_above), 2)} - coiled, not yet triggered",
            }

    return {
        "setup_stage": "neither",
        "resistance_level": None,
        "distance_pct": None,
        "volume_ratio": None,
        "reason": "no resistance level close enough (recently broken or still overhead) to call either stage",
    }

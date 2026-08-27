"""
Ascending triangle / rising-trendline consolidation detector - the
pattern shown in your charts: a rising support line (higher lows,
fitted by linear regression) converging into a roughly flat
resistance ceiling, with the range narrowing as it approaches the
apex. Works on WEEKLY bars (resampled from daily), since this is
inherently a longer-horizon, multi-month base pattern - a daily-bar
version of this would just be noise.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class FilterResult:
    score: float
    passed: bool
    reason: str


def resample_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregates daily bars into weekly bars. Used instead of requesting
    weekly candles directly from the broker, since that's an extra API
    capability we haven't verified is available - resampling daily
    data we already fetch is more reliable and needs no new API calls.
    """
    weekly = daily_df.resample("W").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return weekly


def _find_swing_lows(series: pd.Series, window: int = 2) -> list[tuple[int, float]]:
    points = []
    for i in range(window, len(series) - window):
        seg = series.iloc[i - window: i + window + 1]
        if series.iloc[i] == seg.min():
            points.append((i, series.iloc[i]))
    return points


def _find_swing_highs(series: pd.Series, window: int = 2) -> list[tuple[int, float]]:
    points = []
    for i in range(window, len(series) - window):
        seg = series.iloc[i - window: i + window + 1]
        if series.iloc[i] == seg.max():
            points.append((i, series.iloc[i]))
    return points


def ascending_triangle_setup(
    weekly_df: pd.DataFrame,
    lookback: int = 60,
    min_swing_points: int = 3,
    resistance_flatness_max: float = 0.04,
    narrowing_ratio_max: float = 0.55,
    proximity_pct: float = 0.05,
) -> FilterResult:
    """
    Requires ALL of:
      1. A genuine rising trendline through swing lows (positive slope,
         reasonably good linear fit - not scattered noise)
      2. A roughly FLAT resistance ceiling (swing highs clustered
         tightly, not also rising/falling)
      3. The gap between support and resistance NARROWING over the
         window (true convergence, not just two unrelated lines)
      4. Current price sitting close to BOTH the trendline and the
         resistance - i.e. coiled right at the apex, not mid-triangle
         and not already broken out past it
    """
    if len(weekly_df) < lookback:
        return FilterResult(0.0, False, "insufficient weekly history")

    window_df = weekly_df.tail(lookback).reset_index(drop=True)

    swing_lows = _find_swing_lows(window_df["low"])
    swing_highs = _find_swing_highs(window_df["high"])

    if len(swing_lows) < min_swing_points or len(swing_highs) < min_swing_points:
        return FilterResult(0.0, False, "not enough swing points to fit a trendline")

    low_x = np.array([p[0] for p in swing_lows])
    low_y = np.array([p[1] for p in swing_lows])
    slope, intercept = np.polyfit(low_x, low_y, 1)

    if slope <= 0:
        return FilterResult(0.0, False, "support trendline is not rising")

    fitted = slope * low_x + intercept
    residuals = low_y - fitted
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((low_y - low_y.mean()) ** 2)
    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0
    if r_squared < 0.5:
        return FilterResult(0.0, False, f"support line fit too weak (R^2={r_squared:.2f}) - swing lows too scattered")

    high_values = np.array([p[1] for p in swing_highs])
    resistance_level = high_values.mean()
    resistance_flatness = high_values.std() / resistance_level if resistance_level else 1.0
    if resistance_flatness > resistance_flatness_max:
        return FilterResult(0.0, False, f"resistance not flat enough (variance {resistance_flatness:.1%})")

    trendline_at_start = slope * low_x[0] + intercept
    trendline_at_end = slope * (lookback - 1) + intercept
    width_start = resistance_level - trendline_at_start
    width_end = resistance_level - trendline_at_end
    if width_start <= 0 or width_end <= 0:
        return FilterResult(0.0, False, "trendline already at/above resistance - triangle geometry broken")

    narrowing_ratio = width_end / width_start
    if narrowing_ratio > narrowing_ratio_max:
        return FilterResult(0.0, False, f"range not narrowing enough (ratio {narrowing_ratio:.2f}) - not a converging triangle")

    current_price = window_df["close"].iloc[-1]
    dist_to_resistance = abs(current_price - resistance_level) / resistance_level
    dist_to_trendline = abs(current_price - trendline_at_end) / trendline_at_end if trendline_at_end else 1.0

    near_apex = dist_to_resistance <= proximity_pct or dist_to_trendline <= proximity_pct
    if not near_apex:
        return FilterResult(0.0, False, "price not yet near the triangle apex - still early in the consolidation")

    score = min(1.0, max(0.0, (r_squared * 0.4) + ((1 - resistance_flatness / resistance_flatness_max) * 0.3) + ((1 - narrowing_ratio / narrowing_ratio_max) * 0.3)))
    return FilterResult(
        score, True,
        f"Ascending triangle: rising support (R^2={r_squared:.2f}) into flat resistance ~{resistance_level:.1f}, "
        f"converged to {narrowing_ratio:.0%} of original width, price near apex"
    )

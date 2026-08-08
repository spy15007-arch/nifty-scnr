"""
Additional technical indicators: MACD, swing high/low trend structure
(higher-highs/higher-lows), and an approximate multi-day VWAP.

VWAP CAVEAT: true VWAP resets every session and needs intraday
(minute-level) data - this system only fetches daily bars. What's
computed here is a ROLLING multi-day VWAP (volume-weighted average of
typical price over a lookback window) as a daily-bar approximation,
NOT genuine intraday VWAP. Treat it as a volume-weighted trend
reference, not a precise intraday level - a real intraday VWAP would
need a separate minute-bar data feed, which isn't wired in yet.
"""
from __future__ import annotations
import pandas as pd
from dataclasses import dataclass


@dataclass
class FilterResult:
    score: float
    passed: bool
    reason: str


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_bullish(df: pd.DataFrame) -> FilterResult:
    """
    MACD line above signal line (the core bullish-crossover condition),
    with histogram trend used to score confidence rather than as a
    strict pass/fail gate. A single-bar histogram-vs-previous-bar
    comparison is too noise-sensitive for daily data - a genuinely
    strong uptrend can still have one day where the histogram ticks
    down slightly. Using a smoothed 3-bar average trend avoids
    rejecting real uptrends over normal day-to-day noise.
    """
    if len(df) < 35:
        return FilterResult(0.0, False, "insufficient history for MACD")

    macd_line, signal_line, hist = compute_macd(df["close"])
    curr_macd, curr_signal, curr_hist = macd_line.iloc[-1], signal_line.iloc[-1], hist.iloc[-1]

    passed = curr_macd > curr_signal
    if not passed:
        return FilterResult(0.0, False, "MACD below signal line - no bullish crossover")

    recent_avg = hist.tail(3).mean()
    prior_avg = hist.iloc[-6:-3].mean() if len(hist) >= 6 else hist.iloc[0]
    momentum_building = recent_avg > prior_avg

    price = df["close"].iloc[-1]
    strength = (curr_macd - curr_signal) / price if price else 0
    base_score = min(1.0, max(0.0, strength / 0.01))
    score = base_score * (1.15 if momentum_building else 0.85)
    score = min(1.0, max(0.0, score))

    trend_note = "momentum building" if momentum_building else "momentum cooling but still bullish"
    return FilterResult(score, True, f"MACD above signal line ({curr_hist:+.3f} histogram, {trend_note})")


def _find_swing_points(series: pd.Series, window: int = 3):
    """A point is a swing high/low if it's the max/min within +/- window bars around it."""
    points = []
    for i in range(window, len(series) - window):
        segment = series.iloc[i - window: i + window + 1]
        if series.iloc[i] == segment.max() or series.iloc[i] == segment.min():
            points.append((i, series.iloc[i]))
    return points


def higher_highs_higher_lows(df: pd.DataFrame, lookback: int = 60, swing_window: int = 3) -> FilterResult:
    """
    Detects genuine uptrend structure: are the last few swing highs
    ascending AND the last few swing lows ascending? This is stronger
    uptrend confirmation than EMA stacking alone - EMAs can stack
    bullish even in a choppy move; HH/HL is the classical technical-
    analysis definition of an actual uptrend.
    """
    if len(df) < lookback:
        return FilterResult(0.0, False, "insufficient history")

    window_df = df.tail(lookback).reset_index(drop=True)

    swing_highs = []
    for i in range(swing_window, len(window_df) - swing_window):
        seg = window_df["high"].iloc[i - swing_window: i + swing_window + 1]
        if window_df["high"].iloc[i] == seg.max():
            swing_highs.append(window_df["high"].iloc[i])

    swing_lows = []
    for i in range(swing_window, len(window_df) - swing_window):
        seg = window_df["low"].iloc[i - swing_window: i + swing_window + 1]
        if window_df["low"].iloc[i] == seg.min():
            swing_lows.append(window_df["low"].iloc[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return FilterResult(0.0, False, "not enough swing points identified")

    last_highs = swing_highs[-2:]
    last_lows = swing_lows[-2:]

    higher_highs = last_highs[-1] > last_highs[-2]
    higher_lows = last_lows[-1] > last_lows[-2]
    passed = higher_highs and higher_lows

    if not passed:
        return FilterResult(0.0, False, f"HH={higher_highs}, HL={higher_lows} - structure not confirmed uptrend")

    hh_pct = (last_highs[-1] / last_highs[-2] - 1) if last_highs[-2] else 0
    hl_pct = (last_lows[-1] / last_lows[-2] - 1) if last_lows[-2] else 0
    score = min(1.0, max(0.0, (hh_pct + hl_pct) / 0.10))
    return FilterResult(score, True, f"Higher-highs/higher-lows confirmed (HH +{hh_pct:.1%}, HL +{hl_pct:.1%})")


def rolling_vwap_position(df: pd.DataFrame, lookback: int = 20) -> FilterResult:
    """
    APPROXIMATION ONLY - see module docstring. Rolling multi-day
    volume-weighted average price; checks if price is holding above it.
    """
    if len(df) < lookback:
        return FilterResult(0.0, False, "insufficient history")

    window = df.tail(lookback)
    typical_price = (window["high"] + window["low"] + window["close"]) / 3
    total_vol = window["volume"].sum()
    if total_vol <= 0:
        return FilterResult(0.0, False, "zero volume in lookback window")
    vwap = (typical_price * window["volume"]).sum() / total_vol
    current_price = df["close"].iloc[-1]

    passed = current_price >= vwap
    if not passed:
        return FilterResult(0.0, False, f"price below {lookback}d rolling VWAP (approx)")

    distance = (current_price - vwap) / vwap
    score = min(1.0, max(0.0, distance / 0.05))
    return FilterResult(score, True, f"price {distance:.1%} above {lookback}d rolling VWAP (approx, not true intraday VWAP)")

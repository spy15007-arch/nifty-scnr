"""
Technical indicators for pre-breakout detection: MACD, higher-highs/
higher-lows trend structure, rolling VWAP, On-Balance Volume (OBV)
accumulation, and ADX trend-ignition detection.
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


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def macd_bullish(df: pd.DataFrame) -> FilterResult:
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
    score = min(1.0, max(0.0, base_score * (1.15 if momentum_building else 0.85)))
    trend_note = "momentum building" if momentum_building else "momentum cooling but still bullish"
    return FilterResult(score, True, f"MACD above signal line ({curr_hist:+.3f} histogram, {trend_note})")


def higher_highs_higher_lows(df: pd.DataFrame, lookback: int = 60, swing_window: int = 3) -> FilterResult:
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


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def obv_accumulation(df: pd.DataFrame, lookback: int = 20, max_price_move: float = 0.08) -> FilterResult:
    """
    On-Balance Volume rising while price stays CONTAINED (hasn't
    already made a big move) - the classic institutional accumulation
    footprint: buyers absorbing supply steadily without pushing price
    up much yet. If price has ALREADY moved >8% in this window, the
    accumulation phase is likely over, not still building.
    """
    if len(df) < lookback + 5:
        return FilterResult(0.0, False, "insufficient history")

    obv = compute_obv(df["close"], df["volume"])
    obv_recent = obv.tail(lookback)
    price_recent = df["close"].tail(lookback)

    x = np.arange(lookback)
    obv_slope = np.polyfit(x, obv_recent.values, 1)[0]
    price_pct_change = (price_recent.iloc[-1] / price_recent.iloc[0] - 1) if price_recent.iloc[0] else 0

    obv_rising = obv_slope > 0
    price_contained = price_pct_change < max_price_move
    passed = obv_rising and price_contained

    if not passed:
        reason = "OBV not rising" if not obv_rising else f"price already moved {price_pct_change:.1%} - accumulation phase likely over"
        return FilterResult(0.0, False, reason)

    avg_vol = df["volume"].tail(lookback).mean()
    score = min(1.0, max(0.0, (obv_slope / (avg_vol + 1e-9)) * 50))
    return FilterResult(score, True, f"OBV rising steadily while price contained (+{price_pct_change:.1%}) - accumulation signature")


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14):
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).rolling(period).mean() / (atr + 1e-10)
    minus_di = 100 * pd.Series(minus_dm, index=high.index).rolling(period).mean() / (atr + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(period).mean()
    return adx, plus_di, minus_di


def adx_building(df: pd.DataFrame, lookback: int = 10, low_threshold: float = 20.0) -> FilterResult:
    """
    ADX measures trend STRENGTH, not direction. A stock consolidating
    before a breakout typically has LOW ADX (weak/no trend, i.e. it's
    going sideways). This looks for ADX that WAS low and is now
    starting to RISE, with +DI (bullish directional movement) leading
    -DI - the signature of a fresh trend just beginning to ignite,
    which is exactly the "about to break out" moment, not "already
    trending hard" (which would show ADX already high).
    """
    if len(df) < 40:
        return FilterResult(0.0, False, "insufficient history for ADX")

    adx, plus_di, minus_di = compute_adx(df["high"], df["low"], df["close"])
    if adx.isna().iloc[-1] or adx.isna().iloc[-lookback]:
        return FilterResult(0.0, False, "insufficient ADX history")

    current_adx = adx.iloc[-1]
    prior_adx = adx.iloc[-lookback]
    bullish_di = plus_di.iloc[-1] > minus_di.iloc[-1]

    was_low = prior_adx < low_threshold
    rising = current_adx > prior_adx
    passed = was_low and rising and bullish_di

    if not passed:
        return FilterResult(0.0, False, f"ADX {current_adx:.1f} not showing fresh trend ignition")

    score = min(1.0, max(0.0, (current_adx - prior_adx) / 15))
    return FilterResult(score, True, f"ADX rising from low base ({prior_adx:.1f}->{current_adx:.1f}), +DI leading - fresh trend igniting")

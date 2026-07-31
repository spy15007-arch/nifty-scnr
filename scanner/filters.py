"""
Individual signal filters used to detect stocks *before* a breakout
is visible to retail. Each function takes a bars DataFrame (index:
date, columns: open/high/low/close/volume) and returns a score in
[0, 1] plus a human-readable reason. The engine combines these.

None of these are magic. They're proxies for "someone with more info
than the crowd is accumulating quietly." Treat outputs as probability
shifts, not certainties.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class FilterResult:
    score: float          # 0-1
    passed: bool
    reason: str


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def volatility_squeeze(df: pd.DataFrame, lookback: int = 20, percentile: float = 0.20) -> FilterResult:
    """
    Flags volatility contraction: current ATR% sitting near the bottom
    of its own recent range. Tight range often precedes expansion.
    """
    if len(df) < lookback + 14:
        return FilterResult(0.0, False, "insufficient history")

    atr = _atr(df)
    atr_pct = (atr / df["close"]).dropna()
    recent = atr_pct.tail(lookback)
    current = recent.iloc[-1]
    rank = (recent < current).mean()  # what fraction of recent days had lower ATR%

    passed = rank <= percentile
    score = max(0.0, 1 - rank / percentile) if passed else 0.0
    return FilterResult(score, passed, f"ATR% percentile rank {rank:.2f} over {lookback}d")


def relative_volume(df: pd.DataFrame, lookback: int = 20, min_mult: float = 1.3) -> FilterResult:
    """Today's volume vs its own recent average - rising interest."""
    if len(df) < lookback + 1:
        return FilterResult(0.0, False, "insufficient history")

    avg_vol = df["volume"].tail(lookback + 1).iloc[:-1].mean()
    today_vol = df["volume"].iloc[-1]
    mult = today_vol / avg_vol if avg_vol > 0 else 0

    passed = mult >= min_mult
    score = min(1.0, (mult - min_mult) / min_mult) if passed else 0.0
    return FilterResult(score, passed, f"rel volume {mult:.2f}x {lookback}d avg")


def quiet_accumulation(df: pd.DataFrame, lookback: int = 20) -> FilterResult:
    """
    Looks for rising volume on flat/down days specifically - a classic
    accumulation footprint (buyers absorbing supply without pushing
    price, often ahead of a move).
    """
    if len(df) < lookback + 1:
        return FilterResult(0.0, False, "insufficient history")

    window = df.tail(lookback).copy()
    window["ret"] = window["close"].pct_change()
    flat_or_down = window[window["ret"] <= 0.005]
    if len(flat_or_down) < 3:
        return FilterResult(0.0, False, "not enough flat/down days to assess")

    vol_trend = np.polyfit(range(len(flat_or_down)), flat_or_down["volume"].values, 1)[0]
    normalized = vol_trend / flat_or_down["volume"].mean() if flat_or_down["volume"].mean() > 0 else 0

    passed = normalized > 0
    score = min(1.0, normalized * 20) if passed else 0.0
    return FilterResult(score, passed, f"volume trend on quiet days: {normalized:+.3f}")


def relative_strength(df: pd.DataFrame, benchmark_df: pd.DataFrame, lookback: int = 63) -> FilterResult:
    """Outperformance vs benchmark (e.g. SPY) over the lookback window."""
    if len(df) < lookback or len(benchmark_df) < lookback:
        return FilterResult(0.0, False, "insufficient history")

    stock_ret = df["close"].iloc[-1] / df["close"].iloc[-lookback] - 1
    bench_ret = benchmark_df["close"].iloc[-1] / benchmark_df["close"].iloc[-lookback] - 1
    rs = stock_ret - bench_ret

    passed = rs > 0
    score = min(1.0, max(0.0, rs / 0.15)) if passed else 0.0
    return FilterResult(score, passed, f"RS vs benchmark: {rs:+.2%} over {lookback}d")


def coiled_at_resistance(df: pd.DataFrame, lookback: int = 50, near_pct: float = 0.03) -> FilterResult:
    """Price sitting just under a well-tested high, tightly - loaded spring."""
    if len(df) < lookback:
        return FilterResult(0.0, False, "insufficient history")

    resistance = df["high"].tail(lookback).max()
    current = df["close"].iloc[-1]
    distance = (resistance - current) / resistance

    passed = 0 <= distance <= near_pct
    score = 1 - (distance / near_pct) if passed else 0.0
    return FilterResult(score, passed, f"{distance:.2%} below {lookback}d high")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


EMA_PERIODS = (9, 21, 50, 200)


def ema_alignment(df: pd.DataFrame, periods: tuple = EMA_PERIODS) -> FilterResult:
    """
    Checks for a "stacked" bullish EMA alignment: fast > slow > slower,
    i.e. 9 > 21 > 50 > 200, with price above all of them. This is a
    trend-confirmation filter - it doesn't predict a breakout by itself,
    but it screens out stocks fighting their own trend, which is where
    most "breakouts" that precede a breakout thesis actually fail.
    """
    if len(df) < max(periods) + 5:
        return FilterResult(0.0, False, "insufficient history")

    emas = {p: ema(df["close"], p).iloc[-1] for p in periods}
    price = df["close"].iloc[-1]
    ordered = sorted(periods)  # e.g. 9, 21, 50, 200
    values = [emas[p] for p in ordered]

    # stacked bullish = each faster EMA above the next slower one, price above all
    is_stacked = all(values[i] >= values[i + 1] for i in range(len(values) - 1))
    price_above_all = price >= max(values)
    passed = is_stacked and price_above_all

    if not passed:
        return FilterResult(0.0, False, "EMAs not in bullish stacked order")

    # score by how much separation exists (more separation = more established trend,
    # less separation = freshly aligned, which is often the more interesting case
    # for a pre-breakout setup, so we score freshness, not distance)
    spread = (values[0] - values[-1]) / price if price else 0
    score = max(0.0, 1 - min(spread / 0.15, 1.0))  # tighter stack -> higher score
    reason = f"EMAs stacked bullish ({'>'.join(str(p) for p in ordered)}), spread {spread:.2%}"
    return FilterResult(score, passed, reason)


def fibonacci_levels(df: pd.DataFrame, lookback: int = 100) -> dict:
    """Returns key retracement levels for the most recent swing high/low."""
    window = df.tail(lookback)
    swing_high = window["high"].max()
    swing_low = window["low"].min()
    diff = swing_high - swing_low
    return {
        "0.0": swing_high,
        "0.236": swing_high - 0.236 * diff,
        "0.382": swing_high - 0.382 * diff,
        "0.5": swing_high - 0.5 * diff,
        "0.618": swing_high - 0.618 * diff,
        "0.786": swing_high - 0.786 * diff,
        "1.0": swing_low,
    }


def fibonacci_reclaim(df: pd.DataFrame, lookback: int = 100, near_pct: float = 0.02) -> FilterResult:
    """
    Flags price holding above the 0.5 or 0.618 retracement of the most
    recent swing - classic "healthy pullback that's about to resume"
    setup, as opposed to a swing that's broken down through its key
    support levels.
    """
    if len(df) < lookback:
        return FilterResult(0.0, False, "insufficient history")

    levels = fibonacci_levels(df, lookback)
    price = df["close"].iloc[-1]

    # only meaningful if there IS a real swing (avoid flagging on flat/noisy data)
    if levels["0.0"] <= levels["1.0"] * 1.02:
        return FilterResult(0.0, False, "no meaningful swing to measure")

    key_levels = {"0.5": levels["0.5"], "0.618": levels["0.618"]}
    distances = {name: abs(price - lvl) / price for name, lvl in key_levels.items()}
    nearest_name = min(distances, key=distances.get)
    nearest_dist = distances[nearest_name]
    holding_above = price >= key_levels[nearest_name]

    passed = holding_above and nearest_dist <= near_pct
    score = (1 - nearest_dist / near_pct) if passed else 0.0
    reason = f"holding above {nearest_name} fib retracement, {nearest_dist:.2%} away"
    return FilterResult(score, passed, reason)


ALL_FILTERS = {
    "volatility_squeeze": volatility_squeeze,
    "relative_volume": relative_volume,
    "quiet_accumulation": quiet_accumulation,
    "coiled_at_resistance": coiled_at_resistance,
    "ema_alignment": ema_alignment,
    "fibonacci_reclaim": fibonacci_reclaim,
    # relative_strength needs a benchmark df, wired in separately by the engine
}

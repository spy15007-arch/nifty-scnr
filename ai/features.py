"""
Turns raw bars + scanner filter outputs into a flat feature vector
the model can consume. Keep this the SAME code path for training and
for live inference - the #1 cause of "backtest was great, live is
garbage" is feature drift between the two.
"""
from __future__ import annotations
import pandas as pd
import numpy as np

from scanner.filters import (
    volatility_squeeze, relative_volume, quiet_accumulation,
    coiled_at_resistance, relative_strength, ema_alignment, fibonacci_reclaim, _atr,
)


def build_features(df: pd.DataFrame, benchmark_df: pd.DataFrame) -> dict:
    """df/benchmark_df must have enough history (200+ bars recommended, for the 200 EMA)."""
    atr = _atr(df)
    atr_pct = (atr / df["close"]).iloc[-1] if len(atr.dropna()) else np.nan

    vsq = volatility_squeeze(df)
    rv = relative_volume(df)
    qa = quiet_accumulation(df)
    car = coiled_at_resistance(df)
    rs = relative_strength(df, benchmark_df)
    ema_a = ema_alignment(df)
    fib = fibonacci_reclaim(df)

    close = df["close"]
    returns_5d = close.pct_change(5).iloc[-1]
    returns_20d = close.pct_change(20).iloc[-1]

    return {
        "atr_pct": atr_pct,
        "volatility_squeeze_score": vsq.score,
        "relative_volume_score": rv.score,
        "quiet_accumulation_score": qa.score,
        "coiled_at_resistance_score": car.score,
        "relative_strength_score": rs.score,
        "ema_alignment_score": ema_a.score,
        "fibonacci_reclaim_score": fib.score,
        "returns_5d": returns_5d,
        "returns_20d": returns_20d,
        "n_filters_passed": sum([vsq.passed, rv.passed, qa.passed, car.passed, rs.passed, ema_a.passed, fib.passed]),
    }


FEATURE_COLUMNS = [
    "atr_pct", "volatility_squeeze_score", "relative_volume_score",
    "quiet_accumulation_score", "coiled_at_resistance_score",
    "relative_strength_score", "ema_alignment_score", "fibonacci_reclaim_score",
    "returns_5d", "returns_20d", "n_filters_passed",
]


def make_training_row(df: pd.DataFrame, benchmark_df: pd.DataFrame, as_of_idx: int,
                       horizon_days: int, move_threshold: float) -> dict | None:
    """
    Builds one labeled training example: features as of `as_of_idx`,
    label = whether price rose >= move_threshold within horizon_days after.
    """
    if as_of_idx + horizon_days >= len(df) or as_of_idx < 60:
        return None

    hist = df.iloc[: as_of_idx + 1]
    bench_hist = benchmark_df.iloc[: as_of_idx + 1]
    feats = build_features(hist, bench_hist)
    if any(pd.isna(v) for v in feats.values()):
        return None

    entry_price = df["close"].iloc[as_of_idx]
    future_window = df["close"].iloc[as_of_idx + 1: as_of_idx + 1 + horizon_days]
    max_future = future_window.max()
    label = int((max_future / entry_price - 1) >= move_threshold)

    feats["label"] = label
    return feats

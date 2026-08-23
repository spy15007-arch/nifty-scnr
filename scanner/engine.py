"""
Scanner engine: runs all filters across a universe of symbols and
produces a ranked watchlist of "pre-breakout" candidates.

Weights are MODE-AWARE: a good intraday/BTST setup and a good 7-day
swing setup don't emphasize the same things. Intraday cares most about
immediate volume/trigger proximity; swing cares most about durable
relative strength and trend structure. Each profile is its own
probability distribution (sums to 1.0), hand-tuned for now - once
enough calibration data exists (see main.py's `calibrate` command),
these should be tuned against actual hit rates instead of intuition.
"""
from __future__ import annotations
import pandas as pd
from dataclasses import dataclass, field
from scanner.filters import (
    volatility_squeeze, relative_volume, quiet_accumulation,
    coiled_at_resistance, relative_strength, ema_alignment, fibonacci_reclaim,
)
import config

DEFAULT_WEIGHTS = {
    "volatility_squeeze": 0.18,
    "relative_volume": 0.15,
    "quiet_accumulation": 0.15,
    "coiled_at_resistance": 0.15,
    "relative_strength": 0.12,
    "ema_alignment": 0.15,
    "fibonacci_reclaim": 0.10,
}

INTRADAY_WEIGHTS = {
    "volatility_squeeze": 0.12,
    "relative_volume": 0.25,
    "quiet_accumulation": 0.10,
    "coiled_at_resistance": 0.20,
    "relative_strength": 0.08,
    "ema_alignment": 0.10,
    "fibonacci_reclaim": 0.15,
}

BTST_WEIGHTS = {
    "volatility_squeeze": 0.18,
    "relative_volume": 0.18,
    "quiet_accumulation": 0.15,
    "coiled_at_resistance": 0.17,
    "relative_strength": 0.12,
    "ema_alignment": 0.12,
    "fibonacci_reclaim": 0.08,
}

SWING_WEIGHTS = {
    "volatility_squeeze": 0.15,
    "relative_volume": 0.10,
    "quiet_accumulation": 0.15,
    "coiled_at_resistance": 0.10,
    "relative_strength": 0.25,
    "ema_alignment": 0.20,
    "fibonacci_reclaim": 0.05,
}

WEIGHTS_BY_MODE = {
    "morning": INTRADAY_WEIGHTS,
    "afternoon": BTST_WEIGHTS,
    "eod": SWING_WEIGHTS,
}


@dataclass
class ScanResult:
    symbol: str
    composite_score: float
    passed_filters: list[str]
    reasons: list[str] = field(default_factory=list)


class ScannerEngine:
    def __init__(self, weights: dict[str, float] | None = None, scan_mode: str | None = None):
        if weights is not None:
            self.weights = weights
        elif scan_mode is not None:
            self.weights = WEIGHTS_BY_MODE.get(scan_mode, DEFAULT_WEIGHTS)
        else:
            self.weights = DEFAULT_WEIGHTS

    def _passes_universe_filter(self, df: pd.DataFrame) -> bool:
        if df.empty or len(df) < 20:
            return False
        price = df["close"].iloc[-1]
        if not (config.MIN_PRICE <= price <= config.MAX_PRICE):
            return False
        avg_dollar_vol = (df["close"] * df["volume"]).tail(20).mean()
        return avg_dollar_vol >= config.MIN_AVG_DOLLAR_VOLUME

    def scan_symbol(self, symbol: str, df: pd.DataFrame, benchmark_df: pd.DataFrame) -> ScanResult | None:
        if not self._passes_universe_filter(df):
            return None

        results = {
            "volatility_squeeze": volatility_squeeze(df, config.ATR_SQUEEZE_LOOKBACK, config.ATR_SQUEEZE_PERCENTILE),
            "relative_volume": relative_volume(df, config.REL_VOLUME_LOOKBACK, config.REL_VOLUME_MIN),
            "quiet_accumulation": quiet_accumulation(df, config.RANGE_TIGHTNESS_LOOKBACK * 2),
            "coiled_at_resistance": coiled_at_resistance(df, config.RESISTANCE_LOOKBACK, config.NEAR_RESISTANCE_PCT),
            "relative_strength": relative_strength(df, benchmark_df, config.RS_LOOKBACK),
            "ema_alignment": ema_alignment(df),
            "fibonacci_reclaim": fibonacci_reclaim(df),
        }

        composite = sum(self.weights[name] * r.score for name, r in results.items())
        passed = [name for name, r in results.items() if r.passed]
        reasons = [r.reason for r in results.values() if r.passed]

        if len(passed) < 2:
            return None

        return ScanResult(symbol=symbol, composite_score=composite, passed_filters=passed, reasons=reasons)

    def scan_universe(
        self,
        bars_by_symbol: dict[str, pd.DataFrame],
        benchmark_df: pd.DataFrame,
        top_n: int = 25,
    ) -> list[ScanResult]:
        results = []
        for symbol, df in bars_by_symbol.items():
            if symbol == config.RS_BENCHMARK:
                continue
            r = self.scan_symbol(symbol, df, benchmark_df)
            if r:
                results.append(r)
        results.sort(key=lambda r: r.composite_score, reverse=True)
        return results[:top_n]

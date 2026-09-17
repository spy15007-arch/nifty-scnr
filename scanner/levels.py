"""
Computes concrete trade levels for a candidate: the trigger price, a
stop loss, and FIVE profit targets. Works in both directions:
  - "bullish": breakout above resistance (used for stock longs and CE options)
  - "bearish": breakdown below support (used for PE options, market corrections)

ENTRY TRIGGER uses the NEAREST genuine swing-high resistance above
current price (bullish) / nearest swing-low support below current
price (bearish) - NOT simply the highest high / lowest low over the
whole lookback window. A hard sanity cap (max_trigger_distance_pct)
rejects the candidate entirely if even the nearest identifiable level
is still unreasonably far from current price.

TARGET_1 TIGHTENED (2026-09-17): the backtest diagnostic showed a
consistent ~30% of unresolved trades sitting at 80%+ of target_1's
distance without quite completing it - across multiple horizon tests
(10/15/20 days), this subset didn't resolve simply by waiting longer,
suggesting target_1 itself was set a bit too far. Tightened from the
27.2% Fibonacci extension to the 23.6% level (a real Fib ratio, not an
arbitrary number) and lowered the minimum reward:risk floor from 1.0x
to 0.75x - together about a 13% closer target_1, evidence-proportionate
rather than an arbitrary guess. Targets 2-5 are unchanged.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

from scanner.filters import _atr, fibonacci_levels

FIB_EXTENSION_PCTS = [0.236, 0.618, 1.000, 1.272, 1.618]
REWARD_FLOOR_MULTIPLES = [0.75, 2.5, 4.0, 5.5, 7.0]  # cumulative min R:R per target


@dataclass
class TradeLevels:
    direction: str
    entry_trigger: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    target_4: float
    target_5: float
    risk_reward_1: float
    risk_reward_2: float
    risk_reward_3: float
    risk_reward_4: float
    risk_reward_5: float
    basis: str

    @property
    def targets(self) -> list[float]:
        return [self.target_1, self.target_2, self.target_3, self.target_4, self.target_5]

    @property
    def risk_rewards(self) -> list[float]:
        return [self.risk_reward_1, self.risk_reward_2, self.risk_reward_3, self.risk_reward_4, self.risk_reward_5]


def _round_step(price: float) -> float:
    if price < 100:
        return 5
    elif price < 500:
        return 10
    elif price < 2000:
        return 25
    elif price < 5000:
        return 50
    return 100


def _nearest_round_level(price: float, direction: str) -> float:
    step = _round_step(price)
    if direction == "bullish":
        return math.ceil(price / step) * step
    return math.floor(price / step) * step


def _beyond(a: float, b: float, direction: str) -> float:
    return max(a, b) if direction == "bullish" else min(a, b)


def _nearer(a: float, b: float, direction: str) -> float:
    return min(a, b) if direction == "bullish" else max(a, b)


def _find_nearest_resistance_above(df: pd.DataFrame, current_price: float, lookback: int, swing_window: int = 3) -> float | None:
    """
    Finds the NEAREST genuine swing-high resistance above current
    price - not just the single highest point over the whole window,
    which could be a stale peak the stock has already fallen far away
    from. Returns None if no swing high above current price exists in
    the window (e.g. the stock is already near its own recent highs).
    """
    window = df.tail(lookback).reset_index(drop=True)
    highs = window["high"]
    candidates = []
    for i in range(swing_window, len(highs) - swing_window):
        seg = highs.iloc[i - swing_window: i + swing_window + 1]
        if highs.iloc[i] == seg.max() and highs.iloc[i] > current_price:
            candidates.append(highs.iloc[i])
    return min(candidates) if candidates else None


def _find_nearest_support_below(df: pd.DataFrame, current_price: float, lookback: int, swing_window: int = 3) -> float | None:
    """Symmetric to the resistance finder above, for bearish/breakdown setups."""
    window = df.tail(lookback).reset_index(drop=True)
    lows = window["low"]
    candidates = []
    for i in range(swing_window, len(lows) - swing_window):
        seg = lows.iloc[i - swing_window: i + swing_window + 1]
        if lows.iloc[i] == seg.min() and lows.iloc[i] < current_price:
            candidates.append(lows.iloc[i])
    return max(candidates) if candidates else None


def compute_trade_levels(df: pd.DataFrame, lookback: int = 100, atr_stop_mult: float = 1.5,
                          direction: str = "bullish", max_trigger_distance_pct: float = 0.08) -> TradeLevels | None:
    if len(df) < lookback:
        return None
    if direction not in ("bullish", "bearish"):
        raise ValueError("direction must be 'bullish' or 'bearish'")

    atr = _atr(df).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None

    sign = 1 if direction == "bullish" else -1
    current_price = df["close"].iloc[-1]

    if direction == "bullish":
        nearest_resistance = _find_nearest_resistance_above(df, current_price, lookback)
        if nearest_resistance is None:
            level_ref = df["high"].tail(10).max()
        else:
            level_ref = nearest_resistance
        entry_trigger = round(level_ref * 1.002, 2)
        atr_stop = entry_trigger - atr * atr_stop_mult
        swing_extreme = df["low"].tail(lookback).min()
        stop_loss = round(max(atr_stop, swing_extreme), 2)
        if stop_loss >= entry_trigger:
            return None
    else:
        nearest_support = _find_nearest_support_below(df, current_price, lookback)
        if nearest_support is None:
            level_ref = df["low"].tail(10).min()
        else:
            level_ref = nearest_support
        entry_trigger = round(level_ref * 0.998, 2)
        atr_stop = entry_trigger + atr * atr_stop_mult
        swing_extreme = df["high"].tail(lookback).max()
        stop_loss = round(min(atr_stop, swing_extreme), 2)
        if stop_loss <= entry_trigger:
            return None

    distance_pct = abs(entry_trigger - current_price) / current_price
    if distance_pct > max_trigger_distance_pct:
        return None

    risk = abs(entry_trigger - stop_loss)

    fib = fibonacci_levels(df, lookback)
    swing_range = fib["0.0"] - fib["1.0"]
    fib_exts = [entry_trigger + sign * swing_range * pct for pct in FIB_EXTENSION_PCTS]

    targets: list[float] = []
    prior = entry_trigger
    for i, fib_ext in enumerate(fib_exts):
        round_ref = fib_ext if i == 0 else _beyond(fib_ext, prior * (1.01 if direction == "bullish" else 0.99), direction)
        round_lvl = _nearest_round_level(round_ref, direction)
        candidate = _nearer(fib_ext, round_lvl, direction) if i == 0 else _beyond(fib_ext, round_lvl, direction)
        floor = entry_trigger + sign * risk * REWARD_FLOOR_MULTIPLES[i]
        target = _beyond(candidate, floor, direction)
        targets.append(round(target, 2))
        prior = target

    risk_rewards = [round(abs(t - entry_trigger) / risk, 2) if risk > 0 else 0 for t in targets]

    basis = (
        f"{direction} nearest swing {'resistance' if direction == 'bullish' else 'support'} "
        f"+0.2% buffer trigger (within {max_trigger_distance_pct:.0%} of current price); "
        f"stop = tighter of {atr_stop_mult}x ATR or {lookback}d swing extreme; "
        f"targets = Fib extension (23.6/61.8/100/127.2/161.8%) blended with round levels, "
        f"minimum 0.75:1 through 7:1 reward:risk floors"
    )

    return TradeLevels(
        direction=direction,
        entry_trigger=entry_trigger,
        stop_loss=stop_loss,
        target_1=targets[0], target_2=targets[1], target_3=targets[2],
        target_4=targets[3], target_5=targets[4],
        risk_reward_1=risk_rewards[0], risk_reward_2=risk_rewards[1], risk_reward_3=risk_rewards[2],
        risk_reward_4=risk_rewards[3], risk_reward_5=risk_rewards[4],
        basis=basis,
    )

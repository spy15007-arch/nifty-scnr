"""
Computes concrete trade levels for a candidate: the trigger price, a
stop loss, and FIVE profit targets. Works in both directions:
  - "bullish": breakout above resistance (used for stock longs and CE options)
  - "bearish": breakdown below support (used for PE options, market corrections)

Target methodology (in order of preference), same in both directions:
  1. Fibonacci extension of the most recent swing (27.2% / 61.8% / 100% / 127.2% / 161.8%)
  2. The nearest round-number / psychological level beyond each fib
     target (round numbers act as real support/resistance because
     that's where retail limit orders and mental stops cluster)
  Each target also has a minimum reward:risk floor (T1 >= 1:1, then
  +1.5R per subsequent target) so a target never ends up sitting
  uselessly close to entry just because a round number landed nearby.

Stop = beyond the trigger by an ATR-based buffer, OR beyond the most
recent swing extreme, whichever is tighter (closer) - keeps risk
defined without being so tight it gets shaken out by normal noise.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

from scanner.filters import _atr, fibonacci_levels

FIB_EXTENSION_PCTS = [0.272, 0.618, 1.000, 1.272, 1.618]
REWARD_FLOOR_MULTIPLES = [1.0, 2.5, 4.0, 5.5, 7.0]  # cumulative min R:R per target


@dataclass
class TradeLevels:
    direction: str  # "bullish" or "bearish"
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
    """Rounds UP for bullish (resistance-like), DOWN for bearish (support-like)."""
    step = _round_step(price)
    if direction == "bullish":
        return math.ceil(price / step) * step
    return math.floor(price / step) * step


def _beyond(a: float, b: float, direction: str) -> float:
    """Whichever of a/b is further in the trade's direction (higher for bullish, lower for bearish)."""
    return max(a, b) if direction == "bullish" else min(a, b)


def _nearer(a: float, b: float, direction: str) -> float:
    """Whichever of a/b is closer to entry (lower for bullish, higher for bearish)."""
    return min(a, b) if direction == "bullish" else max(a, b)


def compute_trade_levels(df: pd.DataFrame, lookback: int = 100, atr_stop_mult: float = 1.5,
                          direction: str = "bullish") -> TradeLevels | None:
    if len(df) < lookback:
        return None
    if direction not in ("bullish", "bearish"):
        raise ValueError("direction must be 'bullish' or 'bearish'")

    atr = _atr(df).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None

    sign = 1 if direction == "bullish" else -1

    if direction == "bullish":
        level_ref = df["high"].tail(lookback).max()       # resistance
        entry_trigger = round(level_ref * 1.002, 2)         # breakout buffer above
        atr_stop = entry_trigger - atr * atr_stop_mult
        swing_extreme = df["low"].tail(lookback).min()
        stop_loss = round(max(atr_stop, swing_extreme), 2)   # tighter (higher) of the two
        if stop_loss >= entry_trigger:
            return None
    else:
        level_ref = df["low"].tail(lookback).min()          # support
        entry_trigger = round(level_ref * 0.998, 2)          # breakdown buffer below
        atr_stop = entry_trigger + atr * atr_stop_mult
        swing_extreme = df["high"].tail(lookback).max()
        stop_loss = round(min(atr_stop, swing_extreme), 2)   # tighter (lower) of the two
        if stop_loss <= entry_trigger:
            return None

    risk = abs(entry_trigger - stop_loss)

    fib = fibonacci_levels(df, lookback)
    swing_range = fib["0.0"] - fib["1.0"]  # always positive (0.0 = swing high, 1.0 = swing low)
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
        f"{direction} {lookback}d {'resistance' if direction == 'bullish' else 'support'} "
        f"+0.2% buffer trigger; stop = tighter of {atr_stop_mult}x ATR or {lookback}d swing extreme; "
        f"targets = Fib extension (27.2/61.8/100/127.2/161.8%) blended with round levels, "
        f"minimum 1:1 through 7:1 reward:risk floors"
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

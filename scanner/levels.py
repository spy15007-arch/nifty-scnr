"""
Computes concrete trade levels for a candidate: the breakout trigger
price, a stop loss, and three profit targets. This is what turns "this
stock looks interesting" into "buy above X, stop at Y, targets Z1/Z2/Z3" -
the actionable output most people actually want from a scan.

Target methodology (in order of preference):
  1. Fibonacci extension of the most recent swing (27.2% / 61.8% / 100%)
  2. The nearest round-number / psychological level above each fib
     target (round numbers act as real resistance because that's
     where retail limit orders and mental stops cluster)
  Each target = whichever of (fib extension, round level) is further
  out than the previous target, so T1 < T2 < T3 always holds.

Stop = below the breakout trigger by an ATR-based buffer, OR below the
most recent swing low, whichever is tighter (closer) - keeps risk
defined without being so tight it gets shaken out by normal noise.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import pandas as pd

from scanner.filters import _atr, fibonacci_levels


@dataclass
class TradeLevels:
    entry_trigger: float
    stop_loss: float
    target_1: float
    target_2: float
    target_3: float
    risk_reward_1: float
    risk_reward_2: float
    risk_reward_3: float
    basis: str


def _nearest_round_level(price: float) -> float:
    """
    Picks a sensible round-number step based on price magnitude, then
    rounds UP to the nearest one - these act as real psychological
    resistance (round rupee levels, round 50s, etc.)
    """
    if price < 100:
        step = 5
    elif price < 500:
        step = 10
    elif price < 2000:
        step = 25
    elif price < 5000:
        step = 50
    else:
        step = 100
    return math.ceil(price / step) * step


def compute_trade_levels(df: pd.DataFrame, lookback: int = 100, atr_stop_mult: float = 1.5) -> TradeLevels | None:
    if len(df) < lookback:
        return None

    resistance = df["high"].tail(lookback).max()
    atr = _atr(df).iloc[-1]
    if pd.isna(atr) or atr <= 0:
        return None

    # Entry trigger: breakout above recent resistance, with a small
    # buffer so it's not triggered by a single tick poking through.
    entry_trigger = round(resistance * 1.002, 2)

    # Stop: tighter of (ATR-based buffer below trigger) vs (recent swing low)
    atr_stop = entry_trigger - (atr * atr_stop_mult)
    swing_low = df["low"].tail(lookback).min()
    stop_loss = round(max(atr_stop, swing_low), 2)
    if stop_loss >= entry_trigger:
        return None

    # Fibonacci extension targets off the most recent swing
    levels = fibonacci_levels(df, lookback)
    swing_range = levels["0.0"] - levels["1.0"]
    fib_ext_272 = entry_trigger + swing_range * 0.272
    fib_ext_618 = entry_trigger + swing_range * 0.618
    fib_ext_1000 = entry_trigger + swing_range * 1.000

    round_1 = _nearest_round_level(entry_trigger)
    target_1 = round(max(min(fib_ext_272, round_1), entry_trigger * 1.005), 2)

    round_2 = _nearest_round_level(max(fib_ext_618, target_1 * 1.01))
    target_2 = round(max(fib_ext_618, round_2, target_1 * 1.015), 2)

    round_3 = _nearest_round_level(max(fib_ext_1000, target_2 * 1.01))
    target_3 = round(max(fib_ext_1000, round_3, target_2 * 1.02), 2)

    risk = entry_trigger - stop_loss
    rr1 = round((target_1 - entry_trigger) / risk, 2) if risk > 0 else 0
    rr2 = round((target_2 - entry_trigger) / risk, 2) if risk > 0 else 0
    rr3 = round((target_3 - entry_trigger) / risk, 2) if risk > 0 else 0

    basis = (
        f"trigger = {lookback}d resistance +0.2% buffer; "
        f"stop = tighter of {atr_stop_mult}x ATR or {lookback}d swing low; "
        f"targets = Fib extension (27.2%/61.8%/100%) blended with nearest round levels"
    )

    return TradeLevels(
        entry_trigger=entry_trigger,
        stop_loss=stop_loss,
        target_1=target_1,
        target_2=target_2,
        target_3=target_3,
        risk_reward_1=rr1,
        risk_reward_2=rr2,
        risk_reward_3=rr3,
        basis=basis,
    )

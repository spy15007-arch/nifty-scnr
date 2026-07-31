"""
Turns a probability + feature vector into a plain-language
explanation. No black box - every recommendation should say WHY.
"""
from __future__ import annotations
from dataclasses import dataclass

from scanner.levels import TradeLevels
from scanner.trade_style import ExecutionPlan


@dataclass
class Recommendation:
    symbol: str
    probability: float
    top_reasons: list[str]
    caveats: list[str]
    levels: TradeLevels | None = None
    execution: ExecutionPlan | None = None


LABELS = {
    "volatility_squeeze_score": "volatility has contracted sharply (coiling)",
    "relative_volume_score": "volume is running above its recent average",
    "quiet_accumulation_score": "volume has been rising on quiet/down days (possible accumulation)",
    "coiled_at_resistance_score": "price is sitting just below a well-tested resistance level",
    "relative_strength_score": "the stock has been outperforming the benchmark",
    "ema_alignment_score": "EMAs are stacked bullish (9 > 21 > 50 > 200) with price above all of them",
    "fibonacci_reclaim_score": "price is holding above a key 0.5/0.618 Fibonacci retracement level",
}


def explain(symbol: str, probability: float, features: dict,
            levels: TradeLevels | None = None, execution: ExecutionPlan | None = None) -> Recommendation:
    scored = [(LABELS[k], v) for k, v in features.items() if k in LABELS and v > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    top_reasons = [label for label, _ in scored[:3]]

    caveats = [
        "This is a probability shift based on historical pattern similarity, not a guarantee.",
        "Model has not seen every regime - validate against current market context before sizing up.",
    ]
    if features.get("n_filters_passed", 0) < 3:
        caveats.append("Fewer than 3 independent signals agree - treat as lower conviction.")
    if levels is None:
        caveats.append("No valid trade levels computed (insufficient history or invalid stop) - skip sizing this one.")
    if execution and execution.style.value == "INTRADAY":
        caveats.append("Intraday plans are the most timing-sensitive - confirm live volume before entering, don't rely on yesterday's close alone.")
    if execution and execution.style.value == "BTST":
        caveats.append("BTST carries overnight gap risk - size smaller than a confirmed breakout trade.")

    return Recommendation(
        symbol=symbol, probability=probability, top_reasons=top_reasons,
        caveats=caveats, levels=levels, execution=execution,
    )

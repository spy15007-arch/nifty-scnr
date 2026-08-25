"""
Backtests the CURRENT scanning logic (pre-breakout gate + 6 signals +
mode-aware weighting) against historical data, without needing to wait
weeks for live outcomes to accumulate.

CRITICAL DESIGN PRINCIPLE - no lookahead bias: at each simulated day i,
every computation uses ONLY df.iloc[:i+1] (bars up to and including day
i). The actual outcome check afterward uses df.iloc[i+1:i+1+horizon] -
bars the simulation never had access to when "deciding" to flag the
symbol. This mirrors exactly what live trading sees: you can only ever
act on data available up to today. Verified against hand-computed test
cases before shipping.
"""
from __future__ import annotations
import logging
import pandas as pd
from dataclasses import dataclass

from scanner.engine import ScannerEngine
from scanner.breakout import check_pre_breakout_setup
from scanner.technicals import (
    macd_bullish, higher_highs_higher_lows, rolling_vwap_position,
    obv_accumulation, adx_building,
)
from scanner.levels import compute_trade_levels

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    symbol: str
    signal_date: str
    predicted_prob: float
    n_signals: int
    entry_trigger: float
    stop_loss: float
    target_1: float
    outcome: int  # 1 = hit target first, 0 = hit stop first, -1 = inconclusive (neither hit within horizon)
    days_to_resolve: int | None


def _resolve_outcome(future_bars: pd.DataFrame, target_1: float, stop_loss: float) -> tuple[int, int | None]:
    """Given the bars AFTER a signal day, determines what happened first: target or stop."""
    target_hits = future_bars.index[future_bars["high"] >= target_1]
    stop_hits = future_bars.index[future_bars["low"] <= stop_loss]

    hit_target = len(target_hits) > 0
    hit_stop = len(stop_hits) > 0

    if hit_target and not hit_stop:
        days = future_bars.index.get_loc(target_hits[0]) + 1
        return 1, days
    if hit_stop and not hit_target:
        days = future_bars.index.get_loc(stop_hits[0]) + 1
        return 0, days
    if hit_target and hit_stop:
        t_pos = future_bars.index.get_loc(target_hits[0])
        s_pos = future_bars.index.get_loc(stop_hits[0])
        outcome = 1 if t_pos <= s_pos else 0
        return outcome, min(t_pos, s_pos) + 1
    return -1, None


def run_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    scan_mode: str = "eod",
    test_days: int = 120,
    horizon_days: int = 10,
    min_history: int = 100,
) -> list[BacktestTrade]:
    engine = ScannerEngine(scan_mode=scan_mode)
    trades: list[BacktestTrade] = []

    for symbol, full_df in bars_by_symbol.items():
        if len(full_df) < min_history + horizon_days:
            continue

        start_idx = max(min_history, len(full_df) - test_days - horizon_days)
        end_idx = len(full_df) - horizon_days

        for i in range(start_idx, end_idx):
            df_as_of = full_df.iloc[: i + 1]  # ONLY data up to this point
            if len(df_as_of) < min_history:
                continue

            bench_as_of = benchmark_df[benchmark_df.index <= df_as_of.index[-1]]
            if len(bench_as_of) < min_history:
                continue

            scan_result = engine.scan_symbol(symbol, df_as_of, bench_as_of)
            if scan_result is None:
                continue

            rsi_analysis = check_pre_breakout_setup(df_as_of)
            if not rsi_analysis["flagged"]:
                continue

            levels = compute_trade_levels(df_as_of)
            if levels is None:
                continue

            macd_result = macd_bullish(df_as_of)
            hh_hl_result = higher_highs_higher_lows(df_as_of)
            vwap_result = rolling_vwap_position(df_as_of)
            obv_result = obv_accumulation(df_as_of)
            adx_result = adx_building(df_as_of)

            n_extra = sum([macd_result.passed, hh_hl_result.passed, vwap_result.passed,
                           obv_result.passed, adx_result.passed])
            n_signals = 1 + n_extra
            conviction_boost = 1.0 + (0.08 * n_extra)
            predicted_prob = min(0.99, scan_result.composite_score * conviction_boost)

            # ONLY NOW do we look at what actually happened next - data
            # the decision above never had access to
            future_bars = full_df.iloc[i + 1: i + 1 + horizon_days]
            if future_bars.empty:
                continue

            outcome, days = _resolve_outcome(future_bars, levels.target_1, levels.stop_loss)

            trades.append(BacktestTrade(
                symbol=symbol,
                signal_date=str(df_as_of.index[-1].date()),
                predicted_prob=round(predicted_prob, 4),
                n_signals=n_signals,
                entry_trigger=levels.entry_trigger,
                stop_loss=levels.stop_loss,
                target_1=levels.target_1,
                outcome=outcome,
                days_to_resolve=days,
            ))

    return trades


def summarize_backtest(trades: list[BacktestTrade]) -> dict:
    resolved = [t for t in trades if t.outcome in (0, 1)]
    if not resolved:
        return {"total_signals": len(trades), "resolved": 0, "message": "No resolved trades to summarize"}

    overall_hit_rate = sum(t.outcome for t in resolved) / len(resolved)

    by_signals: dict[int, list[BacktestTrade]] = {}
    for t in resolved:
        by_signals.setdefault(t.n_signals, []).append(t)
    signal_breakdown = {
        n: {"count": len(ts), "hit_rate": round(sum(t.outcome for t in ts) / len(ts), 3)}
        for n, ts in sorted(by_signals.items())
    }

    return {
        "total_signals": len(trades),
        "resolved": len(resolved),
        "still_open_at_horizon_end": len(trades) - len(resolved),
        "overall_hit_rate": round(overall_hit_rate, 3),
        "by_signal_count": signal_breakdown,
    }


def write_backtest_report(trades: list[BacktestTrade], summary: dict, scan_mode: str, out_dir: str = "./reports/output") -> str:
    from pathlib import Path
    from datetime import date

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = f"{out_dir}/backtest_{scan_mode}_{date.today().isoformat()}.md"

    lines = [f"# Backtest Report - {scan_mode.upper()} - {date.today().isoformat()}", ""]

    if summary.get("resolved", 0) == 0:
        lines.append("No resolved trades - not enough historical data or nothing was flagged in the test window.")
        Path(path).write_text("\n".join(lines))
        return path

    lines.append(f"**{summary['total_signals']} total signals** ({summary['resolved']} resolved, "
                 f"{summary['still_open_at_horizon_end']} still open at horizon end)")
    lines.append(f"**Overall hit rate: {summary['overall_hit_rate']:.1%}** (target hit before stop)")
    lines.append("")
    lines.append("## Hit rate by signal-agreement count")
    lines.append("| Signals confirming | Count | Hit rate |")
    lines.append("| :--- | :--- | :--- |")
    for n, stats in summary["by_signal_count"].items():
        lines.append(f"| {n}/6 | {stats['count']} | {stats['hit_rate']:.1%} |")
    lines.append("")
    lines.append(
        "*If hit rate clearly rises with signal count, the grading system is working as intended - "
        "more agreement genuinely means better odds. If it's flat or inverted, the weights/thresholds "
        "need revisiting.*"
    )

    Path(path).write_text("\n".join(lines))
    return path

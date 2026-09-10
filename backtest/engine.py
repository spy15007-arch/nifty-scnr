"""
Backtests the CURRENT scanning logic (pre-breakout gate + 6 signals +
mode-aware weighting) against historical data, without needing to wait
weeks for live outcomes to accumulate.

CRITICAL DESIGN PRINCIPLE - no lookahead bias: at each simulated day i,
every computation uses ONLY df.iloc[:i+1] (bars up to and including day
i). The actual outcome check afterward uses df.iloc[i+1:i+1+horizon] -
bars the simulation never had access to when "deciding" to flag the
symbol.

HORIZON = 15 days (was 10): the diagnostic run showed 77% of unresolved
trades simply "went nowhere" within a 10-day window rather than clearly
failing - but the user's actual stated target was 15-20 days. Testing
against a shorter window than what's actually being asked for
understates real performance. 15 days matches that expectation more
honestly.

DIAGNOSTIC ADDITION: every trade (resolved OR unresolved) also records
how close it got to target_1 (max_gain_pct, pct_of_target_reached) and
where it ended up (final_gain_pct) - specifically for the trades that
hit neither target nor stop within the (now 15-day, matching the
user's actual 15-20 day target window rather than an artificially
shorter 10-day test) horizon: were they close to target (target too
aggressive), going nowhere (setup not genuinely predictive), or
drifting down without quite triggering the stop (a real warning sign)?
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
    max_gain_pct: float          # best % gain reached at any point in the horizon, regardless of outcome
    pct_of_target_reached: float  # max_gain_pct as a fraction of the target's intended gain (1.0 = fully reached)
    final_gain_pct: float         # % gain/loss at the END of the horizon window


def _resolve_outcome(future_bars: pd.DataFrame, entry_trigger: float, target_1: float, stop_loss: float) -> tuple[int, int | None, float, float, float]:
    """Given the bars AFTER a signal day, determines what happened first: target or stop, plus diagnostic detail."""
    target_hits = future_bars.index[future_bars["high"] >= target_1]
    stop_hits = future_bars.index[future_bars["low"] <= stop_loss]

    hit_target = len(target_hits) > 0
    hit_stop = len(stop_hits) > 0

    max_high = future_bars["high"].max()
    final_close = future_bars["close"].iloc[-1]
    max_gain_pct = (max_high - entry_trigger) / entry_trigger
    target_gain_pct = (target_1 - entry_trigger) / entry_trigger
    pct_of_target_reached = (max_gain_pct / target_gain_pct) if target_gain_pct > 0 else 0.0
    final_gain_pct = (final_close - entry_trigger) / entry_trigger

    if hit_target and not hit_stop:
        days = future_bars.index.get_loc(target_hits[0]) + 1
        return 1, days, max_gain_pct, pct_of_target_reached, final_gain_pct
    if hit_stop and not hit_target:
        days = future_bars.index.get_loc(stop_hits[0]) + 1
        return 0, days, max_gain_pct, pct_of_target_reached, final_gain_pct
    if hit_target and hit_stop:
        t_pos = future_bars.index.get_loc(target_hits[0])
        s_pos = future_bars.index.get_loc(stop_hits[0])
        outcome = 1 if t_pos <= s_pos else 0
        return outcome, min(t_pos, s_pos) + 1, max_gain_pct, pct_of_target_reached, final_gain_pct
    return -1, None, max_gain_pct, pct_of_target_reached, final_gain_pct


def run_backtest(
    bars_by_symbol: dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    scan_mode: str = "eod",
    test_days: int = 120,
    horizon_days: int = 15,
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
            df_as_of = full_df.iloc[: i + 1]
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

            future_bars = full_df.iloc[i + 1: i + 1 + horizon_days]
            if future_bars.empty:
                continue

            outcome, days, max_gain_pct, pct_of_target, final_gain_pct = _resolve_outcome(
                future_bars, levels.entry_trigger, levels.target_1, levels.stop_loss
            )

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
                max_gain_pct=round(max_gain_pct, 4),
                pct_of_target_reached=round(pct_of_target, 4),
                final_gain_pct=round(final_gain_pct, 4),
            ))

    return trades


def summarize_backtest(trades: list[BacktestTrade]) -> dict:
    resolved = [t for t in trades if t.outcome in (0, 1)]
    unresolved = [t for t in trades if t.outcome == -1]

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

    unresolved_diagnostic = {}
    if unresolved:
        close_to_target = [t for t in unresolved if t.pct_of_target_reached >= 0.80]
        went_nowhere = [t for t in unresolved if -0.10 <= t.final_gain_pct <= 0.10 and t.pct_of_target_reached < 0.80]
        drifted_down = [t for t in unresolved if t.final_gain_pct < -0.10]
        drifted_up_not_enough = [t for t in unresolved if 0.10 < t.final_gain_pct and t.pct_of_target_reached < 0.80]

        avg_pct_of_target = sum(t.pct_of_target_reached for t in unresolved) / len(unresolved)
        avg_final_gain = sum(t.final_gain_pct for t in unresolved) / len(unresolved)

        unresolved_diagnostic = {
            "count": len(unresolved),
            "avg_pct_of_target_reached": round(avg_pct_of_target, 3),
            "avg_final_gain_pct": round(avg_final_gain, 4),
            "got_close_80pct_or_more_of_target": len(close_to_target),
            "went_nowhere_flat": len(went_nowhere),
            "drifted_down_meaningfully": len(drifted_down),
            "drifted_up_but_not_enough": len(drifted_up_not_enough),
        }

    return {
        "total_signals": len(trades),
        "resolved": len(resolved),
        "still_open_at_horizon_end": len(unresolved),
        "overall_hit_rate": round(overall_hit_rate, 3),
        "by_signal_count": signal_breakdown,
        "unresolved_diagnostic": unresolved_diagnostic,
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

    diag = summary.get("unresolved_diagnostic")
    if diag:
        lines.append("## What happened to the unresolved trades?")
        lines.append(f"- **{diag['count']}** trades hit neither target nor stop within the horizon")
        lines.append(f"- Average max progress toward target: **{diag['avg_pct_of_target_reached']:.0%}** of the way there")
        lines.append(f"- Average gain/loss at horizon end: **{diag['avg_final_gain_pct']:+.1%}**")
        lines.append(f"- Got 80%+ of the way to target (target may be slightly too aggressive): **{diag['got_close_80pct_or_more_of_target']}**")
        lines.append(f"- Went essentially nowhere, flat (-10% to +10%, setup wasn't genuinely predictive): **{diag['went_nowhere_flat']}**")
        lines.append(f"- Drifted up meaningfully but still short of target: **{diag['drifted_up_but_not_enough']}**")
        lines.append(f"- Drifted down meaningfully without quite hitting stop (a warning sign): **{diag['drifted_down_meaningfully']}**")

    lines.append("")
    lines.append(
        "*If hit rate clearly rises with signal count, the grading system is working as intended. "
        "If unresolved trades mostly 'went nowhere flat', the entry signal itself isn't very predictive of an "
        "imminent move, even when it's not wrong about direction. If most 'got close to target', the target may "
        "simply be set too far out - a lower target_1 could convert many of these into real wins.*"
    )

    Path(path).write_text("\n".join(lines))
    return path

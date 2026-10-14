"""
Backtests the CURRENT scanning logic against historical data, without
needing to wait weeks for live outcomes to accumulate.

CRITICAL DESIGN PRINCIPLE - no lookahead bias: at each simulated day i,
every computation uses ONLY df.iloc[:i+1]. The outcome check afterward
uses df.iloc[i+1:i+1+horizon] - bars the simulation never saw.

HORIZON = 30 days for SWING (diagnostic-confirmed: base-level entries
genuinely need ~30 days to develop, recovering the hit rate lost when
near_recent_base went live). BTST uses a much shorter horizon (3 days)
matching its actual overnight-to-next-day premise - testing BTST
against a 30-day window would be meaningless, since it's supposed to
resolve almost immediately.

MODE-SPECIFIC SECOND GATE (2026-09-19): SWING uses near_recent_base
(patient, still-at-base). BTST uses btst_momentum_setup (same-day
breakout confirmation) - kept in sync with the same mode-specific
gates now in main.py's live scan.

SIGNAL-COUNT FILTER: requires 3+ signals before a candidate counts at
all - 1-2 signal hit rates were barely better than noise.

DIAGNOSTIC: every trade also records how close it got to target_1
(max_gain_pct, pct_of_target_reached) and where it ended up
(final_gain_pct). Also tags each trade with its setup_stage
(fresh_breakout / pre_breakout / neither, see scanner.breakout) purely
as a DIAGNOSTIC breakdown - evidence on whether that tag actually
correlates with hit rate, not a filter on what gets backtested.

HORIZON_DAYS_BY_MODE is the one shared source of truth for how long a
signal gets to resolve - tracking/outcomes.py (live) imports this same
constant so a trade's live "still open" window always matches what the
backtest itself used to validate these setups.

ADDED 2026-10-14 - two more DIAGNOSTIC-only breakdowns, same spirit as
setup_stage above (computed and recorded, never used to decide what
gets backtested or how a trade resolves):
  - days_since_break (from classify_setup_stage, already computed for
    the setup_stage tag): for fresh_breakout trades specifically, was
    the signal day itself the breakout candle (1) or a couple of
    sessions later (2-3)? This is the direct evidence for whether a
    single-candle "enter on next candle" breakout actually performs
    differently from a slightly older one.
  - market_trend: whether the NSE benchmark (via the same bench_as_of
    already computed below for relative_strength/relative_strength)
    was above/below its own trend on the signal day, per
    scanner.global_cues.compute_index_trend_bias - the evidence on
    whether that multiplier deserves to eventually become a harder
    filter rather than just a ranking nudge.
"""
from __future__ import annotations
import logging
import pandas as pd
from dataclasses import dataclass

from scanner.engine import ScannerEngine
from scanner.breakout import check_pre_breakout_setup, near_recent_base, btst_momentum_setup, classify_setup_stage
from scanner.technicals import (
    macd_bullish, higher_highs_higher_lows, rolling_vwap_position,
    obv_accumulation, adx_building,
)
from scanner.levels import compute_trade_levels
from scanner.global_cues import compute_index_trend_bias

logger = logging.getLogger(__name__)

HORIZON_DAYS_BY_MODE = {"btst": 3, "swing": 30}


@dataclass
class BacktestTrade:
    symbol: str
    signal_date: str
    predicted_prob: float
    n_signals: int
    entry_trigger: float
    stop_loss: float
    target_1: float
    outcome: int
    days_to_resolve: int | None
    max_gain_pct: float
    pct_of_target_reached: float
    final_gain_pct: float
    setup_stage: str = "neither"
    days_since_break: int | None = None
    market_trend: str = "unknown"


def _resolve_outcome(future_bars: pd.DataFrame, entry_trigger: float, target_1: float, stop_loss: float) -> tuple[int, int | None, float, float, float]:
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
    scan_mode: str = "swing",
    test_days: int = 120,
    horizon_days: int | None = None,
    min_history: int = 100,
) -> list[BacktestTrade]:
    if horizon_days is None:
        horizon_days = HORIZON_DAYS_BY_MODE.get(scan_mode, 30)

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

            # Computed once, here, and reused for both the SWING gate and
            # the final per-trade tag - mirrors main.py exactly, so the
            # backtest's definition of "passed the gate" never drifts
            # from what the live scan actually admits.
            stage_analysis = classify_setup_stage(df_as_of)

            if scan_mode == "btst":
                gate_analysis = btst_momentum_setup(df_as_of)
            else:
                # Additive OR, same widening as main.py: near_recent_base
                # alone can never pass an already-broken-out stock, so
                # SWING also admits a confirmed fresh_breakout tag.
                gate_analysis = near_recent_base(df_as_of)
                gate_analysis = {
                    **gate_analysis,
                    "flagged": gate_analysis["flagged"] or stage_analysis["setup_stage"] == "fresh_breakout",
                }
            if not gate_analysis["flagged"]:
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
            if n_signals < 3:
                continue
            conviction_boost = 1.0 + (0.08 * n_extra)
            predicted_prob = min(0.99, scan_result.composite_score * conviction_boost)

            future_bars = full_df.iloc[i + 1: i + 1 + horizon_days]
            if future_bars.empty:
                continue

            outcome, days, max_gain_pct, pct_of_target, final_gain_pct = _resolve_outcome(
                future_bars, levels.entry_trigger, levels.target_1, levels.stop_loss
            )

            # DIAGNOSTIC only (see module docstring) - derived from the
            # multiplier value rather than its descriptive label string,
            # since the label's embedded percentage varies trade to
            # trade and isn't a stable grouping key.
            try:
                _, trend_mult = compute_index_trend_bias(bench_as_of)
                if trend_mult > 1.0:
                    market_trend = "above_trend"
                elif trend_mult < 1.0:
                    market_trend = "below_trend"
                else:
                    market_trend = "flat_or_unknown"
            except Exception:
                market_trend = "flat_or_unknown"

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
                setup_stage=stage_analysis["setup_stage"],
                days_since_break=stage_analysis.get("days_since_break"),
                market_trend=market_trend,
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

    by_stage: dict[str, list[BacktestTrade]] = {}
    for t in resolved:
        by_stage.setdefault(t.setup_stage, []).append(t)
    stage_breakdown = {
        stage: {"count": len(ts), "hit_rate": round(sum(t.outcome for t in ts) / len(ts), 3)}
        for stage, ts in sorted(by_stage.items())
    }

    # Fresh-breakout trades only - the direct evidence on whether a
    # day-1 ("just printed its breakout candle") entry really performs
    # differently from a day-2/3 one.
    by_days: dict[int, list[BacktestTrade]] = {}
    for t in resolved:
        if t.setup_stage == "fresh_breakout" and t.days_since_break is not None:
            by_days.setdefault(t.days_since_break, []).append(t)
    days_since_break_breakdown = {
        days: {"count": len(ts), "hit_rate": round(sum(t.outcome for t in ts) / len(ts), 3)}
        for days, ts in sorted(by_days.items())
    }

    by_trend: dict[str, list[BacktestTrade]] = {}
    for t in resolved:
        by_trend.setdefault(t.market_trend, []).append(t)
    market_trend_breakdown = {
        trend: {"count": len(ts), "hit_rate": round(sum(t.outcome for t in ts) / len(ts), 3)}
        for trend, ts in sorted(by_trend.items())
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
        "by_setup_stage": stage_breakdown,
        "by_days_since_break": days_since_break_breakdown,
        "by_market_trend": market_trend_breakdown,
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

    stage_breakdown = summary.get("by_setup_stage")
    if stage_breakdown:
        lines.append("## Hit rate by setup stage (fresh breakout / pre-breakout tag)")
        lines.append("| Setup stage | Count | Hit rate |")
        lines.append("| :--- | :--- | :--- |")
        for stage, stats in stage_breakdown.items():
            lines.append(f"| {stage} | {stats['count']} | {stats['hit_rate']:.1%} |")
        lines.append("")

    days_breakdown = summary.get("by_days_since_break")
    if days_breakdown:
        lines.append("## Hit rate by days since the breakout candle (fresh_breakout trades only)")
        lines.append("*Day 1 = the signal day itself was the breakout candle - the \"enter on next candle\" case.*")
        lines.append("")
        lines.append("| Days since break | Count | Hit rate |")
        lines.append("| :--- | :--- | :--- |")
        for days, stats in days_breakdown.items():
            lines.append(f"| {days} | {stats['count']} | {stats['hit_rate']:.1%} |")
        lines.append("")

    trend_breakdown = summary.get("by_market_trend")
    if trend_breakdown:
        lines.append("## Hit rate by NSE market trend on the signal day")
        lines.append("| Market trend | Count | Hit rate |")
        lines.append("| :--- | :--- | :--- |")
        for trend, stats in trend_breakdown.items():
            lines.append(f"| {trend} | {stats['count']} | {stats['hit_rate']:.1%} |")
        lines.append("")

    diag = summary.get("unresolved_diagnostic")
    if diag:
        lines.append("## What happened to the unresolved trades?")
        lines.append(f"- **{diag['count']}** trades hit neither target nor stop within the horizon")
        lines.append(f"- Average max progress toward target: **{diag['avg_pct_of_target_reached']:.0%}** of the way there")
        lines.append(f"- Average gain/loss at horizon end: **{diag['avg_final_gain_pct']:+.1%}**")
        lines.append(f"- Got 80%+ of the way to target: **{diag['got_close_80pct_or_more_of_target']}**")
        lines.append(f"- Went essentially nowhere, flat: **{diag['went_nowhere_flat']}**")
        lines.append(f"- Drifted up meaningfully but still short of target: **{diag['drifted_up_but_not_enough']}**")
        lines.append(f"- Drifted down meaningfully without quite hitting stop: **{diag['drifted_down_meaningfully']}**")

    Path(path).write_text("\n".join(lines))
    return path

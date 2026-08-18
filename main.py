"""
Entry point. Centralized rate-insulated data lake with explicit strategy siloing,
advanced consolidation filtering, and structured two-tier root dashboard tracking.
"""
import argparse
import logging
import os
import shutil
import pandas as pd
import time
from datetime import datetime

from data.historical import AngelOneHistoricalStore, ParquetStore
from data.universe import load_universe
from scanner.engine import ScannerEngine
from scanner.levels import compute_trade_levels
from scanner.trade_style import classify_trade_style, TradeStyle
from scanner.index_options import recommend_index_options
from ai.model import BreakoutModel
from ai.features import build_features
from ai.explain import explain
from reports.generator import daily_scan_report, daily_options_report
from reports.notify import notify_scan_results, notify_option_results
import config

from scanner.breakout import check_pre_breakout_setup
from scanner.technicals import (
    macd_bullish, higher_highs_higher_lows, rolling_vwap_position,
    obv_accumulation, adx_building,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_DIR = "market_data"


def _get_store():
    """ALWAYS live data - used by every live scan command."""
    return AngelOneHistoricalStore()


def _get_training_store():
    """Training/backtesting ONLY - not currently wired to any command."""
    os.makedirs(DB_DIR, exist_ok=True)
    has_files = any(f.endswith('.parquet') for f in os.listdir(DB_DIR)) if os.path.exists(DB_DIR) else False
    if has_files:
        logger.info(f"💾 Local data cache detected inside ./{DB_DIR}. Using it for training/backtesting.")
        return ParquetStore(root=DB_DIR)
    else:
        logger.warning(f"⚠️ Local database path ./{DB_DIR} is blank. Falling back to live fetch for training.")
        return AngelOneHistoricalStore()


def _get_angelone_mapped_symbol(index_tag: str) -> str:
    mapping = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service"}
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)


def _ensure_report_directories():
    for folder in ["reports/morning", "reports/afternoon", "reports/eod", "reports/output"]:
        os.makedirs(folder, exist_ok=True)


def _grade_for_recommendation(r) -> str:
    """
    Letter grade from conviction probability + how many of the 6
    independent signals confirmed (RSI pre-breakout zone, MACD, HH/HL,
    VWAP, OBV, ADX). More agreement = higher grade.
    """
    n_signals = max(0, len(r.top_reasons) - 1)  # subtract the strategy-title tag
    prob = r.probability

    if prob >= 0.75 and n_signals >= 5:
        return "A+"
    elif prob >= 0.65 and n_signals >= 4:
        return "A"
    elif prob >= 0.55 and n_signals >= 3:
        return "B+"
    elif prob >= 0.45:
        return "B"
    return "C"


def _build_table_lines(recs: list) -> list[str]:
    """Shared table-building logic used by both the per-folder dashboard and the README section."""
    lines = [
        "| Rank | Grade | Ticker | Entry Trigger | Stop Loss | Targets (T1 - T4) | Signals (of 6) |",
        "| :--- | :---: | :--- | :--- | :--- | :--- | :--- |"
    ]
    if not recs:
        lines.append("| - | - | No candidates this session | - | - | - | - |")
    else:
        for idx, r in enumerate(recs, 1):
            grade = _grade_for_recommendation(r)
            entry = r.levels.entry_trigger if r.levels else "Market"
            sl = r.levels.stop_loss if r.levels else "Dynamic"
            tg = " | ".join(str(t) for t in r.levels.targets[:4]) if r.levels else "ATR Based"
            n_signals = max(0, len(r.top_reasons) - 1)
            lines.append(f"| **{idx}** | **{grade}** | **{r.symbol}** | {entry} | {sl} | {tg} | {n_signals}/6 |")
    return lines


def _update_readme_section(scan_mode: str, recs: list):
    """Updates a marked section of README.md with the latest scan results."""
    marker_tag = scan_mode.upper()
    start_marker = f"<!-- {marker_tag}_TABLE_START -->"
    end_marker = f"<!-- {marker_tag}_TABLE_END -->"
    date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    titles = {
        "morning": "⚡ Latest Morning Intraday Watchlist",
        "afternoon": "🌙 Latest Afternoon BTST Watchlist",
        "eod": "📈 Latest EOD Swing Watchlist",
    }
    title = titles.get(scan_mode, scan_mode.title())

    table_lines = _build_table_lines(recs)
    section = "\n".join([
        start_marker,
        f"### {title}",
        f"*Updated: {date_str}*",
        "",
        *table_lines,
        "",
        end_marker,
    ])

    readme_path = "README.md"
    if os.path.exists(readme_path):
        with open(readme_path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = "# NIFTY Scanner\n\nAutomated breakout scanner - live results below.\n\n"

    if start_marker in content and end_marker in content:
        pre = content.split(start_marker)[0]
        post = content.split(end_marker)[1]
        content = pre + section + post
    else:
        content = content.rstrip() + "\n\n" + section + "\n"

    with open(readme_path, "w", encoding="utf-8") as f:
        f.write(content)


def _generate_clean_dashboard_md(scan_mode: str, recs: list, target_path: str):
    """Generates a neat, prioritized, graded high-conviction Markdown dashboard view."""
    date_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if scan_mode == "morning":
        title = "⚡ MORNING INTRADAY WATCHLIST (Top High-Conviction)"
        hold_time = "Intraday (EOD Squareoff)"
    elif scan_mode == "afternoon":
        title = "🌙 AFTERNOON LIVE BTST ACCUMULATIONS (Top High-Conviction)"
        hold_time = "Overnight (1 Session)"
    else:
        title = "📈 POSITION SWING BREAKOUTS (Top High-Conviction)"
        hold_time = "7-10 Days Trend Horizon"

    lines = [
        f"# {title}\n",
        f"*Evaluation Window:* `{date_str}`\n",
        f"🏆 Displaying the top **{len(recs)} high-conviction alpha ideas**, best to worst, graded by conviction and signal agreement.\n",
    ]
    lines.extend(_build_table_lines(recs))
    lines.append("\n---\n")
    lines.append(
        "*Grade key: A+ = probability >=75% with 5+ of 6 signals (RSI pre-breakout zone, MACD, HH/HL, VWAP, OBV, ADX) agreeing. "
        "A = >=65% with 4+ agreeing. B+ = >=55% with 3+ agreeing. B = >=45%. C = below that but still made the cut.*\n"
    )
    with open(target_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def process_scans_with_shared_data(scan_mode: str, bars: dict, benchmark: pd.DataFrame):
    """Processes explicit strategy variations and pushes clean files directly to the root main tree."""
    _ensure_report_directories()
    date_str = datetime.utcnow().strftime("%Y-%m-%d")

    if scan_mode == "morning":
        strategy_title = "MORNING INTRADAY"
        output_subfolder = "reports/morning"
        style_label = "INTRADAY"
    elif scan_mode == "afternoon":
        strategy_title = "AFTERNOON BTST"
        output_subfolder = "reports/afternoon"
        style_label = "BTST"
    else:
        strategy_title = "EOD SWING"
        output_subfolder = "reports/eod"
        style_label = "SWING"

    recs = []

    if bars and len(bars) > 0:
        engine = ScannerEngine()
        try:
            candidates = engine.scan_universe(bars, benchmark, top_n=100)
        except Exception:
            candidates = []

        for cand in candidates:
            df = bars.get(cand.symbol)
            if df is None or df.empty or len(df) < 20:
                continue

            # PRE-BREAKOUT gate - NOT a "has already broken out" gate.
            # RSI is required to be BUILDING in a 45-65 zone (momentum
            # accumulating) and is HARD-EXCLUDED above 68 (already
            # overbought = the move likely already happened). This
            # replaces the old RSI>=60 requirement, which - because RSI
            # is a lagging measure - could only ever surface stocks
            # that had ALREADY moved several percent to get RSI there.
            rsi_analysis = check_pre_breakout_setup(df)
            if not rsi_analysis["flagged"]:
                continue

            if scan_mode == "morning":
                avg_volume = df['volume'].tail(20).mean()
                if df['volume'].iloc[-1] < (avg_volume * 1.0):
                    continue
            elif scan_mode == "afternoon":
                day_high = df['high'].iloc[-1]
                day_low = df['low'].iloc[-1]
                day_close = df['close'].iloc[-1]
                range_span = (day_high - day_low) + 1e-10
                if ((day_high - day_close) / range_span) > 0.40:
                    continue

            feats = build_features(df, benchmark)
            levels = compute_trade_levels(df)

            macd_result = macd_bullish(df)
            hh_hl_result = higher_highs_higher_lows(df)
            vwap_result = rolling_vwap_position(df)
            obv_result = obv_accumulation(df)
            adx_result = adx_building(df)

            confirming_signals = [f"RSI {rsi_analysis['current_rsi']} (pre-breakout building zone)"]
            if macd_result.passed:
                confirming_signals.append(macd_result.reason)
            if hh_hl_result.passed:
                confirming_signals.append(hh_hl_result.reason)
            if vwap_result.passed:
                confirming_signals.append(vwap_result.reason)
            if obv_result.passed:
                confirming_signals.append(obv_result.reason)
            if adx_result.passed:
                confirming_signals.append(adx_result.reason)

            n_extra_confirming = len(confirming_signals) - 1
            conviction_boost = 1.0 + (0.08 * n_extra_confirming)
            adjusted_probability = min(0.99, cand.composite_score * conviction_boost)

            execution = classify_trade_style(df, feats, levels)
            if execution:
                execution.__dict__["style"] = TradeStyle(style_label)

            rec_package = explain(cand.symbol, adjusted_probability, feats, levels, execution)
            rec_package.top_reasons = [f"[{strategy_title}]"] + confirming_signals[:6]
            recs.append(rec_package)

    recs.sort(key=lambda r: r.probability, reverse=True)
    high_conviction_recs = recs[:25]

    try:
        path = daily_scan_report(recs)
    except Exception:
        path = f"{output_subfolder}/scan_raw.md"
        with open(path, "w") as pf:
            pf.write("# Temp Initialization")

    target_md_path = f"{output_subfolder}/scan_{date_str}.md"
    target_csv_path = f"{output_subfolder}/scan_results_{scan_mode}_{date_str}.csv"

    _generate_clean_dashboard_md(scan_mode, high_conviction_recs, f"{output_subfolder}/summary_{scan_mode}.md")
    shutil.copy(f"{output_subfolder}/summary_{scan_mode}.md", f"summary_{scan_mode}.md")
    _update_readme_section(scan_mode, high_conviction_recs)

    if os.path.exists(path):
        try:
            os.replace(path, target_md_path)
        except Exception:
            pass

    csv_rows = []
    for r in recs:
        csv_rows.append({
            "symbol": r.symbol,
            "probability": round(r.probability, 4),
            "grade": _grade_for_recommendation(r),
            "entry_trigger": r.levels.entry_trigger if r.levels else None,
            "stop_loss": r.levels.stop_loss if r.levels else None,
            "target_1": r.levels.targets[0] if r.levels else None,
            "target_2": r.levels.targets[1] if r.levels else None,
            "target_3": r.levels.targets[2] if r.levels else None,
            "target_4": r.levels.targets[3] if r.levels else None,
        })
    pd.DataFrame(csv_rows).to_csv(target_csv_path, index=False)
    pd.DataFrame(csv_rows).to_csv(f"scan_results_{scan_mode}.csv", index=False)

    with open("summary.md", "a") as master_f:
        if os.path.exists(f"summary_{scan_mode}.md"):
            with open(f"summary_{scan_mode}.md", "r") as sf:
                master_f.write(sf.read() + "\n\n")

    if high_conviction_recs:
        notify_scan_results(high_conviction_recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


def execute_isolated_scan(scan_mode: str, test_limit=None):
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]

    store = _get_store()
    try:
        bars = store.get_universe_bars(universe, lookback_days=250)
    except Exception:
        bars = {}

    try:
        benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
    except Exception:
        valid_keys = list(bars.keys()) if bars else []
        benchmark_df = bars[valid_keys[0]] if valid_keys else pd.DataFrame()

    process_scans_with_shared_data(scan_mode, bars, benchmark_df)


def cmd_options(args, shared_store=None):
    store = shared_store if shared_store else _get_store()
    plans = []
    for index_symbol in config.INDEX_UNIVERSE:
        raw_symbol = index_symbol.strip().upper()
        mapped_spot_symbol = _get_angelone_mapped_symbol(raw_symbol)
        try:
            df = store.get_bars(mapped_spot_symbol, lookback_days=250)
        except Exception:
            continue
        if df is None or df.empty or len(df) < 100:
            continue
        feats = build_features(df, df)
        index_plans = recommend_index_options(raw_symbol, df, feats)
        plans.extend(index_plans)

    path = daily_options_report(plans)
    if plans:
        notify_option_results(plans, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan_morning", "scan_afternoon", "scan_eod", "options", "run_all"])
    parser.add_argument("--test-limit", dest="test_limit", default=os.getenv("TRADING_TEST_LIMIT") or None,
                         help="Limit scan to N symbols for testing")
    args = parser.parse_args()

    if args.command == "scan_morning":
        execute_isolated_scan("morning", test_limit=args.test_limit)

    elif args.command == "scan_afternoon":
        execute_isolated_scan("afternoon", test_limit=args.test_limit)

    elif args.command == "scan_eod":
        execute_isolated_scan("eod", test_limit=args.test_limit)

    elif args.command == "options":
        cmd_options(args)

    elif args.command == "run_all":
        logger.info("⚡ Central Data Lake Engaged: Downloading data matrix exactly once...")
        universe = load_universe()
        if args.test_limit:
            universe = universe[: int(args.test_limit)]

        store = _get_store()
        try:
            bars = store.get_universe_bars(universe, lookback_days=250)
        except Exception:
            bars = {}

        try:
            benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
        except Exception:
            valid_keys = list(bars.keys()) if bars else []
            benchmark_df = bars[valid_keys[0]] if valid_keys else pd.DataFrame()

        for mode in ["morning", "afternoon", "eod"]:
            process_scans_with_shared_data(mode, bars, benchmark_df)

        cmd_options(args, shared_store=store)

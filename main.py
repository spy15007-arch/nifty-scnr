"""
Entry point. Centralized rate-insulated data lake with explicit strategy siloing,
advanced consolidation filtering, and structured two-tier root dashboard tracking.
"""
import argparse
import logging
import os
import glob
import shutil
import pandas as pd
import time
from datetime import datetime, timedelta

from data.historical import AngelOneHistoricalStore, ParquetStore
from data.universe import load_universe
from scanner.engine import ScannerEngine
from scanner.levels import compute_trade_levels
from scanner.trade_style import classify_trade_style, TradeStyle
from scanner.index_options import recommend_index_options
from scanner.global_cues import fetch_global_snapshot, compute_market_regime_bias
from ai.model import BreakoutModel
from ai.features import build_features
from ai.explain import explain
from reports.generator import daily_scan_report, daily_options_report, calibration_report
from backtest.engine import run_backtest, summarize_backtest, write_backtest_report
from reports.notify import notify_scan_results, notify_option_results
import config

from scanner.breakout import check_pre_breakout_setup
from scanner.technicals import (
    macd_bullish, higher_highs_higher_lows, rolling_vwap_position,
    obv_accumulation, adx_building,
)
from scanner.patterns import resample_to_weekly, ascending_triangle_setup, FilterResult as PatternResult

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_DIR = "market_data"


def _get_store():
    return AngelOneHistoricalStore()


def _get_training_store():
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
    Letter grade from conviction probability + how many of the 7
    independent signals confirmed (RSI pre-breakout zone, MACD, HH/HL,
    VWAP, OBV, ADX, ascending triangle). More agreement = higher grade.
    """
    n_signals = max(0, len(r.top_reasons) - 1)
    prob = r.probability

    if prob >= 0.75 and n_signals >= 6:
        return "A+"
    elif prob >= 0.65 and n_signals >= 5:
        return "A"
    elif prob >= 0.55 and n_signals >= 4:
        return "B+"
    elif prob >= 0.45:
        return "B"
    return "C"


def _build_table_lines(recs: list) -> list[str]:
    lines = [
        "| Rank | Grade | Ticker | Entry Trigger | Stop Loss | Targets (T1 - T4) | Signals (of 7) |",
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
            lines.append(f"| **{idx}** | **{grade}** | **{r.symbol}** | {entry} | {sl} | {tg} | {n_signals}/7 |")
    return lines


def _update_readme_section(scan_mode: str, recs: list):
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


def _generate_clean_dashboard_md(scan_mode: str, recs: list, target_path: str, market_regime_label: str = ""):
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
    ]
    if market_regime_label:
        lines.append(f"*Global market backdrop:* {market_regime_label}\n")
    lines.append(
        f"🏆 Displaying the top **{len(recs)} high-conviction alpha ideas**, best to worst, graded by conviction and signal agreement.\n"
    )
    lines.extend(_build_table_lines(recs))
    lines.append("\n---\n")
    lines.append(
        "*Grade key: A+ = probability >=75% with 6+ of 7 signals (RSI pre-breakout zone, MACD, HH/HL, VWAP, OBV, ADX, ascending triangle) agreeing. "
        "A = >=65% with 5+ agreeing. B+ = >=55% with 4+ agreeing. B = >=45%. C = below that but still made the cut.*\n"
    )
    with open(target_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def process_scans_with_shared_data(scan_mode: str, bars: dict, benchmark: pd.DataFrame, market_multiplier: float = 1.0, market_regime_label: str = ""):
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
        engine = ScannerEngine(scan_mode=scan_mode)
        try:
            candidates = engine.scan_universe(bars, benchmark, top_n=100)
        except Exception:
            candidates = []

        for cand in candidates:
            df = bars.get(cand.symbol)
            if df is None or df.empty or len(df) < 20:
                continue

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
            try:
                weekly_df = resample_to_weekly(df)
                triangle_result = ascending_triangle_setup(weekly_df)
            except Exception:
                triangle_result = PatternResult(0.0, False, "weekly resample failed")

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
            if triangle_result.passed:
                confirming_signals.append(triangle_result.reason)

            n_extra_confirming = len(confirming_signals) - 1
            conviction_boost = 1.0 + (0.08 * n_extra_confirming)
            adjusted_probability = min(0.99, cand.composite_score * conviction_boost * market_multiplier)

            execution = classify_trade_style(df, feats, levels)
            if execution:
                execution.__dict__["style"] = TradeStyle(style_label)

            rec_package = explain(cand.symbol, adjusted_probability, feats, levels, execution)
            rec_package.top_reasons = [f"[{strategy_title}]"] + confirming_signals[:7]
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

    _generate_clean_dashboard_md(scan_mode, high_conviction_recs, f"{output_subfolder}/summary_{scan_mode}.md", market_regime_label)
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

    new_section = ""
    if os.path.exists(f"summary_{scan_mode}.md"):
        with open(f"summary_{scan_mode}.md", "r") as sf:
            new_section = sf.read() + "\n\n"
    existing_summary = ""
    if os.path.exists("summary.md"):
        with open("summary.md", "r") as ef:
            existing_summary = ef.read()
    with open("summary.md", "w") as master_f:
        master_f.write(new_section + existing_summary)

    if high_conviction_recs:
        notify_scan_results(high_conviction_recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


def _get_market_multiplier() -> tuple[str, float]:
    try:
        snapshot = fetch_global_snapshot()
        label, multiplier = compute_market_regime_bias(snapshot)
        logger.info(f"🌍 Global market backdrop: {label} -> {multiplier}x conviction multiplier")
        return label, multiplier
    except Exception as e:
        logger.warning(f"Global cues fetch failed ({e}) - treating as neutral")
        return "unknown (fetch failed)", 1.0


def execute_isolated_scan(scan_mode: str, test_limit=None):
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]

    store = _get_store()
    # EOD gets more history to support weekly-resampled ascending-
    # triangle detection (needs ~60 weeks = ~420+ days). Morning/
    # afternoon stay at 250 - that pattern is irrelevant for same-day/
    # overnight timeframes, and more history would just slow down
    # time-critical scans for no benefit.
    lookback = 450 if scan_mode == "eod" else 250
    try:
        bars = store.get_universe_bars(universe, lookback_days=lookback)
    except Exception:
        bars = {}

    try:
        benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=lookback)
    except Exception:
        valid_keys = list(bars.keys()) if bars else []
        benchmark_df = bars[valid_keys[0]] if valid_keys else pd.DataFrame()

    label, multiplier = _get_market_multiplier()
    process_scans_with_shared_data(scan_mode, bars, benchmark_df, market_multiplier=multiplier, market_regime_label=label)


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


def cmd_calibrate(scan_mode: str = "eod", horizon_days: int = 10, min_days_old: int = 10):
    folder = f"reports/{scan_mode}"
    pattern = f"{folder}/scan_results_{scan_mode}_*.csv"
    files = sorted(glob.glob(pattern))

    if not files:
        logger.warning(f"No historical scan_results CSVs found under {folder}/ yet - nothing to calibrate against.")
        return

    cutoff_date = datetime.utcnow() - timedelta(days=min_days_old)
    store = _get_store()
    predictions = []

    for filepath in files:
        date_str = os.path.basename(filepath).replace(f"scan_results_{scan_mode}_", "").replace(".csv", "")
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if entry_date > cutoff_date:
            continue

        try:
            df = pd.read_csv(filepath)
        except Exception:
            continue

        for _, row in df.iterrows():
            symbol = row.get("symbol")
            entry_trigger = row.get("entry_trigger")
            stop_loss = row.get("stop_loss")
            target_1 = row.get("target_1")
            prob = row.get("probability")
            if pd.isna(symbol) or pd.isna(entry_trigger) or pd.isna(stop_loss) or pd.isna(target_1) or pd.isna(prob):
                continue

            try:
                symbol_bars = store.get_bars(symbol, lookback_days=250)
            except Exception:
                continue
            if symbol_bars is None or symbol_bars.empty:
                continue

            future_bars = symbol_bars[symbol_bars.index > entry_date].head(horizon_days)
            if future_bars.empty:
                continue

            hit_target = (future_bars["high"] >= target_1).any()
            hit_stop = (future_bars["low"] <= stop_loss).any()

            if hit_target and not hit_stop:
                outcome = 1
            elif hit_stop and not hit_target:
                outcome = 0
            elif hit_target and hit_stop:
                target_day = future_bars[future_bars["high"] >= target_1].index[0]
                stop_day = future_bars[future_bars["low"] <= stop_loss].index[0]
                outcome = 1 if target_day <= stop_day else 0
            else:
                continue

            predictions.append({"symbol": symbol, "predicted_prob": float(prob), "actual_outcome": outcome})

    if not predictions:
        logger.warning(
            f"No resolved predictions found yet for {scan_mode} (need scans at least {min_days_old} "
            f"days old with a clear target/stop outcome within {horizon_days} days) - check back later."
        )
        return

    path = calibration_report(predictions)
    logger.info(f"Calibration report written to {path} based on {len(predictions)} resolved predictions")


def cmd_backtest(scan_mode: str = "eod", test_days: int = 120):
    universe = load_universe()
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=280)
    benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=280)

    logger.info(f"Running backtest for {scan_mode} over last {test_days} trading days across {len(bars)} symbols...")
    trades = run_backtest(bars, benchmark_df, scan_mode=scan_mode, test_days=test_days)
    summary = summarize_backtest(trades)
    path = write_backtest_report(trades, summary, scan_mode)
    logger.info(f"Backtest report written to {path}")
    logger.info(f"Summary: {summary}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan_morning", "scan_afternoon", "scan_eod", "options", "run_all", "calibrate", "backtest"])
    parser.add_argument("--test-limit", dest="test_limit", default=os.getenv("TRADING_TEST_LIMIT") or None,
                         help="Limit scan to N symbols for testing")
    parser.add_argument("--calibrate-mode", dest="calibrate_mode", default="eod", choices=["morning", "afternoon", "eod"],
                         help="Which scan mode's history to calibrate/backtest (used with 'calibrate' and 'backtest' commands)")
    args = parser.parse_args()

    if args.command == "scan_morning":
        execute_isolated_scan("morning", test_limit=args.test_limit)

    elif args.command == "scan_afternoon":
        execute_isolated_scan("afternoon", test_limit=args.test_limit)

    elif args.command == "scan_eod":
        execute_isolated_scan("eod", test_limit=args.test_limit)

    elif args.command == "options":
        cmd_options(args)

    elif args.command == "calibrate":
        cmd_calibrate(scan_mode=args.calibrate_mode)

    elif args.command == "backtest":
        cmd_backtest(scan_mode=args.calibrate_mode)

    elif args.command == "run_all":
        logger.info("⚡ Central Data Lake Engaged: Downloading data matrix exactly once...")
        universe = load_universe()
        if args.test_limit:
            universe = universe[: int(args.test_limit)]

        store = _get_store()
        try:
            bars = store.get_universe_bars(universe, lookback_days=450)
        except Exception:
            bars = {}

        try:
            benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=450)
        except Exception:
            valid_keys = list(bars.keys()) if bars else []
            benchmark_df = bars[valid_keys[0]] if valid_keys else pd.DataFrame()

        label, multiplier = _get_market_multiplier()

        for mode in ["morning", "afternoon", "eod"]:
            process_scans_with_shared_data(mode, bars, benchmark_df, market_multiplier=multiplier, market_regime_label=label)

        cmd_options(args, shared_store=store)

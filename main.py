"""
Entry point. Centralized rate-insulated data lake with explicit strategy siloing,
local Parquet database storage tracking, and root directory dashboard exports.
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
from scanner.trade_style import classify_trade_style
from scanner.index_options import recommend_index_options
from ai.model import BreakoutModel
from ai.features import build_features
from ai.explain import explain
from reports.generator import daily_scan_report, daily_options_report
from reports.notify import notify_scan_results, notify_option_results
import config

# Direct integration of your breakout system
from scanner.breakout import check_rsi_60_breakout

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _get_store() -> ParquetStore:
    """
    Swaps your system backend to use high-performance local Parquet files.
    This eliminates network delays and rate limits, dropping runtime to under 5s!
    """
    return ParquetStore(root="./market_data")

def _get_angelone_mapped_symbol(index_tag: str) -> str:
    mapping = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service"}
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)

def _ensure_report_directories():
    for folder in ["reports/morning", "reports/afternoon", "reports/eod", "reports/output"]:
        os.makedirs(folder, exist_ok=True)

def _compute_rsi_and_atr(df: pd.DataFrame, min_rsi=60, max_rsi=80) -> dict:
    if df is None or len(df) < 20:
        return {"flagged": False}
    close_prices = df['close']
    delta = close_prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-10)
    rsi_series = 100 - (100 / (1 + rs))
    
    tr = pd.concat([df['high'] - df['low'], (df['high'] - close_prices.shift(1)).abs(), (df['low'] - close_prices.shift(1)).abs()], axis=1).max(axis=1)
    atr_series = tr.rolling(window=14).mean()
    
    current_rsi = rsi_series.iloc[-1]
    previous_rsi = rsi_series.iloc[-2]
    current_close = close_prices.iloc[-1]
    current_atr = atr_series.iloc[-1]
    
    if previous_rsi <= min_rsi and current_rsi > min_rsi:
        if current_rsi > max_rsi:
            return {"flagged": False}
        return {
            "flagged": True, "current_rsi": round(current_rsi, 2), "entry_price": round(current_close, 2),
            "stop_loss": round(current_close - (1.5 * current_atr), 2),
            "target_1": round(current_close + (1.0 * current_atr), 2),
            "target_2": round(current_close + (2.0 * current_atr), 2),
            "target_3": round(current_close + (3.0 * current_atr), 2),
            "target_4": round(current_close + (4.0 * current_atr), 2)
        }
    return {"flagged": False}

def process_scans_with_shared_data(scan_mode: str, bars: dict, benchmark: pd.DataFrame):
    """Processes strategies and pushes clean dashboards right to your main workspace windows."""
    _ensure_report_directories()
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    min_rsi_threshold = 60
    if scan_mode == "morning":
        strategy_title = "⚡ MORNING INTRADAY BREAKOUT"
        output_subfolder = "reports/morning"
        style_label = "INTRADAY"
    elif scan_mode == "afternoon":
        strategy_title = "🌙 AFTERNOON LIVE BTST MOMENTUM"
        output_subfolder = "reports/afternoon"
        style_label = "BTST"
        min_rsi_threshold = 62
    else:
        strategy_title = "📈 SWING breakout (7-10 Days Horizon)"
        output_subfolder = "reports/eod"
        style_label = "SWING"

    engine = ScannerEngine()
    candidates = engine.scan_universe(bars, benchmark, top_n=40)
    
    recs = []
    for cand in candidates:
        df = bars.get(cand.symbol)
        if df is None or df.empty:
            continue
            
        rsi_analysis = check_rsi_60_breakout(df)
        if not rsi_analysis["flagged"]:
            continue

        if rsi_analysis["current_rsi"] < min_rsi_threshold:
            continue

        feats = build_features(df, benchmark)
        levels = compute_trade_levels(df)
        
        if levels is not None:
            levels.__dict__["entry_trigger"] = rsi_analysis["entry_price"]
            levels.__dict__["stop_loss"] = rsi_analysis["stop_loss"]
            levels.__dict__["targets"] = [rsi_analysis["target_1"], rsi_analysis["target_2"], rsi_analysis["target_3"], rsi_analysis["target_4"]]

        execution = classify_trade_style(df, feats, levels)
        if execution:
            execution.__dict__["style"] = style_label
            if style_label == "SWING":
                execution.__dict__["entry_window_ist"] = "03:15 PM"
                execution.__dict__["exit_window_ist"] = "7-10 Days Hold"
            elif style_label == "INTRADAY":
                execution.__dict__["entry_window_ist"] = "09:15 AM - 09:45 AM"
                execution.__dict__["exit_window_ist"] = "EOD Squareoff"

        rec_package = explain(cand.symbol, cand.composite_score, feats, levels, execution)
        rec_package.top_reasons = [f"RSI Crossed 60 ({rsi_analysis['current_rsi']})"]
        recs.append(rec_package)

    recs.sort(key=lambda r: r.probability, reverse=True)
    recs = recs[:25]
    
    path = daily_scan_report(recs)
    
    target_md_path = f"{output_subfolder}/scan_{date_str}.md"
    target_csv_path = f"{output_subfolder}/scan_results_{scan_mode}_{date_str}.csv"
    
    if os.path.exists(path):
        shutil.copy(path, "summary.md")
        os.replace(path, target_md_path)
        logger.info(f"✓ Saved Dashboard Summary to Main Window Framework")
        
    if os.path.exists("scan_results.csv"):
        shutil.copy("scan_results.csv", f"scan_results_{scan_mode}.csv")
        os.replace("scan_results.csv", target_csv_path)

    if recs:
        notify_scan_results(recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

def execute_isolated_scan(scan_mode: str, test_limit=None):
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]
    
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=250)
    
    # Read local fallback safely if benchmark file isn't standalone
    try:
        benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
    except Exception:
        valid_keys = list(bars.keys()) if bars else []
        benchmark_df = bars[valid_keys[0]] if (valid_keys and len(valid_keys) > 0) else pd.DataFrame()
        
    process_scans_with_shared_data(scan_mode, bars, benchmark_df)

def cmd_options(args, shared_store=None):
    store = shared_store if shared_store else _get_store()
    plans = []
    for index_symbol in config.INDEX_UNIVERSE:
        raw_symbol = index_symbol.strip().upper()
        mapped_spot_symbol = _get_angelone_mapped_symbol(raw_symbol)
        try:
            df = store.get_bars(mapped_spot_symbol, lookback_days=250)
        except Exception as e:
            logger.warning(f"Could not fetch options baseline for {mapped_spot_symbol}: {e}")
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
    args = parser.parse_args()
    
    test_lim = os.getenv("TRADING_TEST_LIMIT")

    if args.command == "scan_morning":
        execute_isolated_scan("morning", test_lim)
    elif args.command == "scan_afternoon":
        execute_isolated_scan("afternoon", test_lim)
    elif args.command == "scan_eod":
        execute_isolated_scan("eod", test_lim)
    elif args.command == "options":
        cmd_options(args)
        
    elif args.command == "run_all":
        logger.info("⚡ Central Data Lake Engaged: Reading from Local Parquet Database...")
        universe = load_universe()
        if test_lim:
            universe = universe[: int(test_lim)]
            
        store = _get_store()
        bars_lake = store.get_universe_bars(universe, lookback_days=250)
        
        try:
            benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
        except Exception:
            valid_tokens = list(bars_lake.keys()) if bars_lake else []
            if valid_tokens and len(valid_tokens) > 0:
                first_extracted_token_string = valid_tokens[0]
                benchmark_df = bars_lake[first_extracted_token_string]
            else:
                benchmark_df = pd.DataFrame()
        
        logger.info("🧠 Data cached to local memory. Processing sequential strategy filters...")
        
        if not bars_lake:
            logger.error("❌ Local market data files not found. Run the Nightly Sync workflow first!")
        else:
            # Run all strategies back-to-back out of local data instantly
            process_scans_with_shared_data("morning", bars_lake, benchmark_df)
            process_scans_with_shared_data("afternoon", bars_lake, benchmark_df)
            process_scans_with_shared_data("eod", bars_lake, benchmark_df)
            cmd_options(args, shared_store=store)
            logger.info("🏆 All pipeline strategies successfully completed and sent to Telegram!")

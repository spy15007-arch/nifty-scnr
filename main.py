"""
Entry point. Three operational modes with isolated data lakes, csv tracks and sandbox siloing:
  python main.py scan_morning      -> run pre-market intraday scanner, output to reports/morning/
  python main.py scan_afternoon    -> run live BTST momentum scanner, output to reports/afternoon/
  python main.py scan_eod          -> run full universe swing scanner, output to reports/eod/
  python main.py options           -> scan indices for CE + PE option setups
  python main.py run_all           -> sequential data-lake manual override (Sub-60 seconds)
"""
import argparse
import logging
import os
import pandas as pd
import time
from datetime import datetime

from data.historical import AngelOneHistoricalStore
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _get_store() -> AngelOneHistoricalStore:
    return AngelOneHistoricalStore()

def _get_angelone_mapped_symbol(index_tag: str) -> str:
    mapping = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service"}
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)

def _ensure_report_directories():
    """Dynamically structures deep isolation paths for standalone csv and markdown states."""
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
    """Processes filters and preserves standalone markdown and CSV metrics into sandboxes."""
    _ensure_report_directories()
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    min_rsi_threshold = 60
    if scan_mode == "morning":
        strategy_title = "MORNING INTRADAY BREAKOUT"
        output_subfolder = "reports/morning"
    elif scan_mode == "afternoon":
        strategy_title = "AFTERNOON LIVE BTST MOMENTUM"
        output_subfolder = "reports/afternoon"
        min_rsi_threshold = 62
    else:
        strategy_title = "END-OF-DAY SWING COMPILATION"
        output_subfolder = "reports/eod"

    engine = ScannerEngine()
    candidates = engine.scan_universe(bars, benchmark, top_n=40)
    
    recs = []
    for cand in candidates:
        df = bars.get(cand.symbol)
        if df is None or df.empty:
            continue
            
        rsi_analysis = _compute_rsi_and_atr(df, min_rsi=min_rsi_threshold)
        if not rsi_analysis["flagged"]:
            continue

        feats = build_features(df, benchmark)
        levels = compute_trade_levels(df)
        
        if levels is not None:
            levels.__dict__["entry_trigger"] = rsi_analysis["entry_price"]
            levels.__dict__["stop_loss"] = rsi_analysis["stop_loss"]
            levels.__dict__["targets"] = [rsi_analysis["target_1"], rsi_analysis["target_2"], rsi_analysis["target_3"], rsi_analysis["target_4"]]

        execution = classify_trade_style(df, feats, levels)
        if execution and hasattr(execution, 'style') and isinstance(execution.style, str):
            pass
            
        rec_package = explain(cand.symbol, cand.composite_score, feats, levels, execution)
        rec_package.top_reasons.insert(0, f"[{strategy_title}] RSI Crossed 60 ({rsi_analysis['current_rsi']})")
        recs.append(rec_package)

    recs.sort(key=lambda r: r.probability, reverse=True)
    recs = recs[:25]
    
    # --- SANDBOX SEPARATION ARCHITECTURE (BYPASS SYSTEM OVERWRITES) ---
    # Intercept files instantly before global generators merge or trample them
    path = daily_scan_report(recs)
    
    target_md_path = f"{output_subfolder}/scan_{date_str}.md"
    target_csv_path = f"{output_subfolder}/scan_results_{scan_mode}_{date_str}.csv"
    
    if os.path.exists(path):
        os.replace(path, target_md_path)
        logger.info(f"✓ Isolated Report Generated: {target_md_path}")
        
    # Isolate root tracking csv states out of conflict zones immediately
    if os.path.exists("scan_results.csv"):
        os.replace("scan_results.csv", target_csv_path)
        logger.info(f"✓ Isolated CSV Matrix Saved: {target_csv_path}")

    if recs:
        notify_scan_results(recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

def execute_isolated_scan(scan_mode: str, test_limit=None):
    """Fallback initialization block for solo runner entries."""
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]
    
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=250)
    
    time.sleep(3.0)
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
        logger.info("⚡ Central Data Lake Engaged: Downloading data matrix exactly once...")
        universe = load_universe()
        if test_lim:
            universe = universe[: int(test_lim)]
            logger.info(f"Sub-scale test universe enabled: processing {len(universe)} symbols.")
            
        store = _get_store()
        bars_lake = store.get_universe_bars(universe, lookback_days=250)
        
        logger.info("⏳ Pacing session connections before benchmark extraction...")
        time.sleep(5.0) 
        
        try:
            benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
        except Exception as e:
            logger.warning(f"⚠️ Benchmark asset download rate-blocked: {e}. Activating inline matrix fallback...")
            # --- CRITICAL FIX FOR TYPE MISMATCH ---
            # Safely unpack a single valid string token instead of an raw unhashable list index array
            valid_tokens = list(bars_lake.keys()) if bars_lake else []
            if valid_tokens and len(valid_tokens) > 0:
                first_extracted_token_string = valid_tokens[0]
                benchmark_df = bars_lake[first_extracted_token_string]
                logger.info(f"✓ Extraction complete: Matched benchmark sequence to matrix anchor: {first_extracted_token_string}")
            else:
                benchmark_df = pd.DataFrame()
        
        logger.info("🧠 Data cached to local memory. Processing sequential strategy filters...")
        
        if not benchmark_df.empty:

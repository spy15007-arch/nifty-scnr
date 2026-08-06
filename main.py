"""
Entry point. Centralized rate-insulated data lake with explicit strategy siloing,
mode-specific filtering logic, and root directory dashboard exports.
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

# Standardized parent path matching your visual screenshot folder tree
DB_DIR = "data/market_data"

def _get_store():
    """Dynamically initializes local store layer with nested directory presence validation."""
    os.makedirs(DB_DIR, exist_ok=True)
    has_files = any(f.endswith('.parquet') for f in os.listdir(DB_DIR)) if os.path.exists(DB_DIR) else False
    
    if has_files:
        logger.info(f"💾 Local data cache detected inside ./{DB_DIR}. Running via ultra-fast Parquet engine.")
        return ParquetStore(root=DB_DIR)
    else:
        logger.warning(f"⚠️ Local database path ./{DB_DIR} is blank. Shifting to live network gateway mode.")
        return AngelOneHistoricalStore()

def _get_angelone_mapped_symbol(index_tag: str) -> str:
    mapping = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service"}
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)

def _ensure_report_directories():
    for folder in ["reports/morning", "reports/afternoon", "reports/eod", "reports/output"]:
        os.makedirs(folder, exist_ok=True)

def _generate_clean_dashboard_md(scan_mode: str, recs: list, target_path: str):
    """Generates a clean strategy summary report for your main cockpit window."""
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    
    if scan_mode == "morning":
        title = "⚡ MORNING INTRADAY BREAKOUTS"
        hold_time = "Intraday Square-off Window"
    elif scan_mode == "afternoon":
        title = "🌙 AFTERNOON LIVE BTST ACCUMULATIONS"
        hold_time = "Overnight (1 Session)"
    else:
        title = "📈 POSITION SWING WATCHLISTS"
        hold_time = "7-10 Days Horizon Hold"

    lines = [
        f"# {title} — {date_str}\n",
        f"**{len(recs)} candidates** passed strategy filters today.\n",
        "---",
        ""
    ]
    
    for r in recs:
        lines.append(f"### {r.symbol}")
        if r.levels:
            lv = r.levels
            lines.append(f"**Trade Vector:** Buy Trigger **{lv.entry_trigger}** | Stop Loss **{lv.stop_loss}**")
            targets_str = " | ".join(f"T{i+1}: {t}" for i, t in enumerate(lv.targets[:4]))
            lines.append(f"**Targets:** {targets_str}")
        lines.append(f"**Execution Window:** {hold_time}")
        lines.append(f"*Metrics:* {', '.join(r.top_reasons) if r.top_reasons else 'RSI Breakout Match'}")
        lines.append("")
        
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

    engine = ScannerEngine()
    candidates = engine.scan_universe(bars, benchmark, top_n=40)
    
    recs = []
    for cand in candidates:
        df = bars.get(cand.symbol)
        if df is None or df.empty or len(df) < 20:
            continue
            
        rsi_analysis = check_rsi_60_breakout(df)
        if not rsi_analysis["flagged"]:
            continue

        # Strategy Dynamic Variations
        if scan_mode == "morning":
            avg_volume = df['volume'].tail(20).mean()
            if df['volume'].iloc[-1] < (avg_volume * 1.1):
                continue
        elif scan_mode == "afternoon":
            day_high = df['high'].iloc[-1]
            day_low = df['low'].iloc[-1]
            day_close = df['close'].iloc[-1]
            range_span = (day_high - day_low) + 1e-10
            if ((day_high - day_close) / range_span) > 0.30:
                continue
        elif scan_mode == "eod":
            if rsi_analysis["current_rsi"] > 75:
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

        rec_package = explain(cand.symbol, cand.composite_score, feats, levels, execution)
        rec_package.top_reasons = [f"[{strategy_title}] RSI Crossed 60 ({rsi_analysis['current_rsi']})"]
        recs.append(rec_package)

    recs.sort(key=lambda r: r.probability, reverse=True)
    recs = recs[:25]
    
    path = daily_scan_report(recs)
    
    target_md_path = f"{output_subfolder}/scan_{date_str}.md"
    target_csv_path = f"{output_subfolder}/scan_results_{scan_mode}_{date_str}.csv"
    
    _generate_clean_dashboard_md(scan_mode, recs, f"{output_subfolder}/summary_{scan_mode}.md")
    
    # --- ROOT DASHBOARD MAPPER: PIN FRESH DATA OBJECTS ONTO THE REPOSITORY DASHBOARD SCREEN ---
    shutil.copy(f"{output_subfolder}/summary_{scan_mode}.md", f"summary_{scan_mode}.md")
    
    if os.path.exists(path):
        os.replace(path, target_md_path)
        
    if os.path.exists("scan_results.csv"):
        shutil.copy("scan_results.csv", f"scan_results_{scan_mode}.csv")
        os.replace("scan_results.csv", target_csv_path)

    # Concat strategies to the master landing summary.md screen
    with open("summary.md", "a") as master_f:
        if os.path.exists(f"summary_{scan_mode}.md"):
            with open(f"summary_{scan_mode}.md", "r") as sf:
                master_f.write(sf.read() + "\n\n")

    if recs:
        notify_scan_results(recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)

def execute_isolated_scan(scan_mode: str, test_limit=None):
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]
    
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=250)
    
    try:
        benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
    except Exception:
        valid_keys = list(bars.keys()) if bars else []
        benchmark_df = bars[valid_keys] if (valid_keys and len(valid_keys) > 0) else pd.DataFrame()
        
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
        logger.info("⚡ Central Data Lake Engaged: Verifying active storage pathways...")
        
        if os.path.exists("summary.md"):
            os.remove("summary.md")
            
        universe = load_universe()
        if test_lim:
            universe = universe[: int(test_lim)]
            
        store = _get_store()
        bars_lake = store.get_universe_bars(universe, lookback_days=250)
        
        try:
            benchmark_df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)
        except Exception:

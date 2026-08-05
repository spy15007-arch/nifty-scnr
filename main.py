"""
Entry point. Three modes:
  python main.py scan       -> run scanner + AI on universe, write daily report
  python main.py options    -> scan indices for CE + PE option setups
  python main.py backtest   -> run backtester over stored historical data
  python main.py train      -> train the breakout model on stored history
"""
import argparse
import logging
import os
import pandas as pd

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
from backtest.engine import Backtester, compute_metrics
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_store() -> AngelOneHistoricalStore:
    return AngelOneHistoricalStore()


def _get_angelone_mapped_symbol(index_tag: str) -> str:
    """Translates raw tickers to Angel One exact master names for indices."""
    mapping = {
        "NIFTY": "Nifty 50",
        "BANKNIFTY": "Nifty Bank",
        "FINNIFTY": "Nifty Fin Service"
    }
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)


def _compute_rsi_and_atr(df: pd.DataFrame) -> dict:
    """In-memory calculations for RSI 60 crossover checks and 4 targets."""
    if df is None or len(df) < 20:
        return {"flagged": False}
        
    close_prices = df['close']
    delta = close_prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss + 1e-10)
    rsi_series = 100 - (100 / (1 + rs))
    
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - close_prices.shift(1)).abs()
    tr3 = (df['low'] - close_prices.shift(1)).abs()
    atr_series = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(window=14).mean()
    
    current_rsi = rsi_series.iloc[-1]
    previous_rsi = rsi_series.iloc[-2]
    current_close = close_prices.iloc[-1]
    current_atr = atr_series.iloc[-1]
    
    if previous_rsi <= 60 and current_rsi > 60:
        if current_rsi > 80:
            return {"flagged": False} # Exclude overextended stocks
            
        return {
            "flagged": True,
            "current_rsi": round(current_rsi, 2),
            "entry_price": round(current_close, 2),
            "stop_loss": round(current_close - (1.5 * current_atr), 2),
            "target_1": round(current_close + (1.0 * current_atr), 2),
            "target_2": round(current_close + (2.0 * current_atr), 2),
            "target_3": round(current_close + (3.0 * current_atr), 2),
            "target_4": round(current_close + (4.0 * current_atr), 2)
        }
    return {"flagged": False}


def cmd_scan(args):
    universe = load_universe()

    test_limit = os.getenv("TRADING_TEST_LIMIT")
    if test_limit:
        universe = universe[: int(test_limit)]
        logger.info(f"TEST MODE: limiting universe to {len(universe)} symbols")

    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=250)
    benchmark = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=250)

    engine = ScannerEngine()
    candidates = engine.scan_universe(bars, benchmark, top_n=40)
    logger.info(f"Scanner flagged {len(candidates)} candidates")

    model = None
    try:
        candidate_model = BreakoutModel()
        candidate_model.load()
        model = candidate_model
    except FileNotFoundError:
        logger.warning("No trained model found - reporting scanner scores only (run `train` first for AI probabilities)")

    recs = []
    for cand in candidates:
        df = bars.get(cand.symbol)
        if df is None or df.empty:
            continue
            
        # Screen candidates dynamically for the RSI 60 launchpad criteria
        rsi_analysis = _compute_rsi_and_atr(df)
        if not rsi_analysis["flagged"]:
            continue

        feats = build_features(df, benchmark)
        prob = model.predict_proba(feats) if model else cand.composite_score
        levels = compute_trade_levels(df)
        
        # Override trade levels dynamically with our required 4 momentum targets
        if levels is not None:
            levels.entry_trigger = rsi_analysis["entry_price"]
            levels.stop_loss = rsi_analysis["stop_loss"]
            levels.targets = [
                rsi_analysis["target_1"],
                rsi_analysis["target_2"],
                rsi_analysis["target_3"],
                rsi_analysis["target_4"]
            ]

        execution = classify_trade_style(df, feats, levels)
        rec_package = explain(cand.symbol, prob, feats, levels, execution)
        
        if f"RSI Crossed 60 ({rsi_analysis['current_rsi']})" not in rec_package.top_reasons:
            rec_package.top_reasons.insert(0, f"RSI Crossed 60 ({rsi_analysis['current_rsi']})")
            
        recs.append(rec_package)

    recs = [r for r in recs if r.levels is not None]
    recs.sort(key=lambda r: r.probability, reverse=True)
    recs = recs[:25]
    logger.info(f"{len(recs)} candidates have valid trade levels - final watchlist size")

    path = daily_scan_report(recs)
    logger.info(f"Report written to {path}")

    notify_scan_results(recs, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


def cmd_options(args):
    store = _get_store()
    plans = []

    for index_symbol in config.INDEX_UNIVERSE:
        raw_symbol = index_symbol.strip()
        mapped_symbol = _get_angelone_mapped_symbol(raw_symbol)
        
        try:
            df = store.get_bars(mapped_symbol, lookback_days=250)
        except Exception as e:
            logger.warning(f"Could not fetch {mapped_symbol} ({raw_symbol}): {e}")
            continue
            
        if df is None or df.empty or len(df) < 100:
            logger.warning(f"{mapped_symbol}: insufficient history, skipping")
            continue

        feats = build_features(df, df)
        index_plans = recommend_index_options(mapped_symbol, df, feats)
        plans.extend(index_plans)

    logger.info(f"{len(plans)} index option setups found (CE and/or PE per index)")
    path = daily_options_report(plans)
    logger.info(f"Options report written to {path}")

    notify_option_results(plans, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


def cmd_train(args):
    universe = load_universe()
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=1000)
    benchmark = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=1000)

    model = BreakoutModel()
    model.train(bars, benchmark)
    model.save()
    logger.info(f"Model saved to {config.MODEL_PATH}")


def cmd_backtest(args):
    universe = load_universe()
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=1000)
    benchmark = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=1000)

    model = None
    if args.use_model:
        model = BreakoutModel()
        model.load()

    bt = Backtester(bars, benchmark, model=model)
    result = bt.run()
    metrics = compute_metrics(result)
    logger.info(f"Backtest metrics: {metrics}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan")
    sub.add_parser("options")
    sub.add_parser("train")
    bt_parser = sub.add_parser("backtest")
    bt_parser.add_argument("--use-model", action="store_true")

    args = parser.parse_args()
    {"scan": cmd_scan, "options": cmd_options, "train": cmd_train, "backtest": cmd_backtest}[args.command](args)

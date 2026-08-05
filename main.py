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


def cmd_scan(args):
    universe = load_universe()

    test_limit = os.getenv("TRADING_TEST_LIMIT")
    if test_limit:
        universe = universe[: int(test_limit)]
        logger.info(f"TEST MODE: limiting universe to {len(universe)} symbols")

    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=250)
    benchmark = store.get_bars(config.RS_BENCHMARK, lookback_days=250)

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
        df = bars[cand.symbol]
        feats = build_features(df, benchmark)
        prob = model.predict_proba(feats) if model else cand.composite_score
        levels = compute_trade_levels(df)
        execution = classify_trade_style(df, feats, levels)
        recs.append(explain(cand.symbol, prob, feats, levels, execution))

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
        index_symbol = index_symbol.strip()
        try:
            df = store.get_bars(index_symbol, lookback_days=250)
        except Exception as e:
            logger.warning(f"Could not fetch {index_symbol}: {e} - check the tradingsymbol for your broker")
            continue
        if df.empty or len(df) < 100:
            logger.warning(f"{index_symbol}: insufficient history, skipping")
            continue

        feats = build_features(df, df)
        index_plans = recommend_index_options(index_symbol, df, feats)
        plans.extend(index_plans)

    logger.info(f"{len(plans)} index option setups found (CE and/or PE per index)")
    path = daily_options_report(plans)
    logger.info(f"Options report written to {path}")

    notify_option_results(plans, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)


def cmd_train(args):
    universe = load_universe()
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=1000)
    benchmark = store.get_bars(config.RS_BENCHMARK, lookback_days=1000)

    model = BreakoutModel()
    model.train(bars, benchmark)
    model.save()
    logger.info(f"Model saved to {config.MODEL_PATH}")


def cmd_backtest(args):
    universe = load_universe()
    store = _get_store()
    bars = store.get_universe_bars(universe, lookback_days=1000)
    benchmark = store.get_bars(config.RS_BENCHMARK, lookback_days=1000)

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

"""
Entry point.

WHAT A SCAN DOES (rewritten 2026-10-19/20; the evidence behind every rule is in reports/evidence/):

  1. fetch daily bars from Angel One (a stock's price is NOT a filter - no price cap)
  2. refresh the end-of-day DELIVERY % file from NSE (the best free "institutional footprint")
  3. replay the tracked trades on the completed sessions (the scoreboard, after costs)
  4. rank the stocks:  a SETUP (fresh breakout on volume / about to break out)  AND  a top-half
     QUALITY score (52-week-high proximity, 200-day trend, 12-month return, low volatility,
     liquidity, delivery %)  -> scanner/pipeline.py
  5. every pick gets a full plan: entry (buy now / buy-stop), stop, trailing stop, share count
  6. write the CSV / Markdown files and send the Telegram message

TWO-STAGE BTST (so the BTST message lands before 2:30 PM):
  * an evening full-market run (the Swing scan) saves data_cache/btst_watchlist.json - the ~100
    stocks that are close to breaking out - plus the whole market's scoring yardstick;
  * the afternoon BTST run fetches only those ~100 stocks (about 2 minutes, not 10+), scores them
    against the stored yardstick and messages. With no usable watchlist it falls back to the whole
    market and stops fetching at 2:25 PM IST.

RUN SOURCES (env TRADING_RUN_SOURCE, set by the workflows): "schedule" = GitHub's own cron, which
is often hours late - such a run is skipped if it is too late to be useful or has already been done;
"timer" = the external on-time timer; "manual" = a button press or a local run. See scanner/runguard.py.

Index-option strike alerts are not wanted (the strike has moved by the time a scan reports) - the
`options` command remains for manual use only. The overall MARKET DIRECTION is a one-line header on
the scan messages and the `market` command (scanner/market_direction.py); it is display-only.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from datetime import date, datetime, timedelta

import pandas as pd

import config
from data import nse_eod
from reports import notify, summary
from scanner import pipeline as pl
from scanner import runguard, session
from tracking import outcomes

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

SCAN_LOOKBACK = 320                          # bars per stock (the 12-month return needs 253)
WATCHLIST_PATH = "data_cache/btst_watchlist.json"
DB_DIR = "market_data"
MIN_WATCHLIST_SAVE = 150                     # a full-market run must have scored at least this many stocks to save a watchlist


# --------------------------------------------------------------------------- #
# Secrets never reach the logs
# --------------------------------------------------------------------------- #
def _secrets() -> list[str]:
    return [config.ANGEL_API_KEY, config.ANGEL_CLIENT_ID, config.ANGEL_PASSWORD,
            config.ANGEL_TOTP_SECRET, config.TELEGRAM_BOT_TOKEN]


class _ScrubFormatter(logging.Formatter):
    """Masks every credential in everything that is logged (messages and tracebacks alike)."""

    def format(self, record: logging.LogRecord) -> str:
        return notify.scrub(super().format(record), *_secrets())


def _install_log_scrubbing() -> None:
    for h in logging.getLogger().handlers:
        fmt = getattr(h.formatter, "_fmt", None) or "%(levelname)s:%(name)s:%(message)s"
        h.setFormatter(_ScrubFormatter(fmt))


def _safe(text) -> str:
    return notify.scrub(str(text), *_secrets())


def _alert(text: str) -> None:
    """Best-effort short notice to Telegram (e.g. the run could not start). Never raises."""
    try:
        notify.notify_text(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, text)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _get_store():
    from data.historical import AngelOneHistoricalStore
    return AngelOneHistoricalStore()


def _get_angelone_mapped_symbol(index_tag: str) -> str:
    mapping = {"NIFTY": "Nifty 50", "BANKNIFTY": "Nifty Bank", "FINNIFTY": "Nifty Fin Service"}
    cleaned_tag = index_tag.strip().upper()
    return mapping.get(cleaned_tag, cleaned_tag)


def _ensure_report_directories() -> None:
    for folder in ["reports/btst", "reports/swing", "reports/output", "data_cache", "tracking"]:
        os.makedirs(folder, exist_ok=True)


def _load_universe(test_limit=None) -> list[str]:
    from data.universe import load_universe
    universe = load_universe()
    if test_limit:
        universe = universe[: int(test_limit)]
    return universe


def _global_snapshot() -> dict:
    try:
        from scanner.global_cues import fetch_global_snapshot
        return fetch_global_snapshot() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Global cues fetch failed ({_safe(e)}) - the market line will not count them")
        return {}


def _sector_map() -> dict:
    try:
        from scanner.sectors import fetch_sector_map
        return fetch_sector_map() or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Sector map fetch failed ({_safe(e)}) - sector notes will be skipped this run")
        return {}


def _fetch_benchmark(store, lookback: int) -> tuple[pd.DataFrame, bool]:
    try:
        df = store.get_bars(_get_angelone_mapped_symbol(config.RS_BENCHMARK), lookback_days=lookback)
        if df is not None and len(df):
            return df, True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Benchmark fetch failed ({_safe(e)}) - the market line will say index data is unavailable")
    return pd.DataFrame(), False


def _market_line(ctx: dict) -> str:
    """One-line market direction for the message header. ANY failure just leaves it out."""
    try:
        from scanner.market_direction import assess_market_direction, format_direction_line
        md = assess_market_direction(
            ctx["benchmark_df"] if ctx.get("benchmark_is_index") else None,
            ctx.get("snapshot") or {},
            bars=None,                       # breadth is shown separately, from the whole liquid universe
            nifty_source=config.RS_BENCHMARK,
        )
        return format_direction_line(md)
    except Exception:  # noqa: BLE001
        logger.exception("Market direction header failed - sending the scan message without it")
        return ""


# --------------------------------------------------------------------------- #
# Watchlist (the two-stage BTST hand-over)
# --------------------------------------------------------------------------- #
def _read_watchlist(path: str = WATCHLIST_PATH) -> dict | None:
    try:
        with open(path) as f:
            wl = json.load(f)
    except (OSError, ValueError):
        return None
    return wl if isinstance(wl, dict) else None


def load_watchlist(now: datetime, path: str = WATCHLIST_PATH) -> dict | None:
    """The saved watchlist if it is complete and recent enough, else None."""
    wl = _read_watchlist(path)
    if not wl or wl.get("version") != 1 or not wl.get("symbols"):
        return None
    if not (wl.get("stats") or {}).get("score_q"):
        return None
    try:
        age = (session.ist_date(now) - date.fromisoformat(wl["as_of"])).days
    except (KeyError, ValueError):
        return None
    if age < 0 or age > config.WATCHLIST_MAX_AGE_DAYS:
        logger.info(f"Saved watchlist is {age} days old (limit {config.WATCHLIST_MAX_AGE_DAYS}) - not using it")
        return None
    return wl


def save_watchlist(wl: dict, path: str = WATCHLIST_PATH) -> bool:
    """Atomic write; never replaces a newer watchlist with an older one."""
    try:
        old = _read_watchlist(path)
        if old and str(old.get("as_of", "")) > str(wl.get("as_of", "")):
            return False
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(wl, f, separators=(",", ":"))
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        logger.warning("Could not save the BTST watchlist", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Delivery % (end-of-day public data; optional - the scan runs without it)
# --------------------------------------------------------------------------- #
def _delivery(as_of: date | None, fast: bool) -> tuple[pd.Series | None, dict]:
    try:
        nse_eod.update_cache(max_fetches=4 if fast else 14, time_budget_s=20.0 if fast else 100.0)
        return nse_eod.get_dlv20(as_of or session.ist_date())
    except Exception:  # noqa: BLE001
        logger.warning("Delivery-% layer failed - scoring without it", exc_info=True)
        return None, {"reason": "delivery layer error"}


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _btst_deadline(now: datetime, source: str) -> datetime:
    """When the afternoon BTST fetch must stop so the message lands before about 2:30 PM IST."""
    h, m = config.BTST_FETCH_DEADLINE_IST
    t = now.astimezone(session.IST)
    deadline = t.replace(hour=h, minute=m, second=0, microsecond=0)
    if (deadline - t).total_seconds() < 90 and source != "schedule":
        deadline = t + timedelta(seconds=240)        # a hand-started run past the deadline still gets a fair chance
    return deadline


def _fetch(store, symbols: list[str], lookback: int, deadline: datetime | None = None,
           cap_seconds: int = 5400) -> tuple[dict, dict]:
    """Sequential fetch (the store's own circuit breakers apply) within a time budget."""
    budget = float(cap_seconds)
    if deadline is not None:
        budget = max(0.0, min(budget, (deadline - session.now_ist()).total_seconds()))
    t0 = time.time()
    try:
        bars = store.get_universe_bars(list(symbols), lookback_days=lookback, max_duration_seconds=int(budget))
    except Exception as e:  # noqa: BLE001
        logger.error(f"Universe fetch failed: {_safe(e)}")
        bars = {}
    info = {"requested": len(symbols), "loaded": len(bars), "seconds": round(time.time() - t0), "budget_s": round(budget)}
    logger.info(f"📥 Fetched {info['loaded']}/{info['requested']} stocks in {info['seconds']}s (budget {info['budget_s']}s)")
    return bars, info


def fetch_phase(store, modes: list[str], test_limit, source: str, now: datetime) -> dict:
    """Everything a scan needs from the outside world, gathered once (run_all shares it between both scans)."""
    state = session.session_state(now)
    live_btst = modes == ["btst"] and state == "open"
    wl = load_watchlist(now) if (live_btst and not test_limit) else None

    if wl:
        symbols = list(wl["symbols"])[: config.WATCHLIST_SIZE]
        kind = "watchlist"
        logger.info(f"⚡ Fast BTST check: {len(symbols)} watchlist stocks (built from the close of {wl['as_of']})")
    else:
        symbols = _load_universe(test_limit)
        kind = "universe"
        if live_btst:
            logger.info("No usable BTST watchlist - scanning the whole market with a 2:25 PM stop")
    deadline = _btst_deadline(now, source) if live_btst else None

    bars, info = _fetch(store, symbols, SCAN_LOOKBACK, deadline)
    ctx = {"kind": kind, "watchlist": wl, "bars": bars, "track_bars": bars, "fetch": info,
           "benchmark_df": pd.DataFrame(), "benchmark_is_index": False, "snapshot": {}, "sector_map": {}}
    if not bars:
        return ctx

    # tracked trades whose stock is no longer in this run's bars (e.g. it left the index) still need prices
    if kind == "universe" and not test_limit:
        try:
            led = outcomes.load_ledger()
            active = set(led.loc[led["status"].isin(outcomes.ACTIVE), "symbol"])
            missing = sorted(active - set(bars))[:40]
            if missing:
                extra, _ = _fetch(store, missing, SCAN_LOOKBACK, None, cap_seconds=120)
                ctx["track_bars"] = {**bars, **extra}
        except Exception:  # noqa: BLE001
            logger.warning("Could not fetch prices for tracked trades outside the universe", exc_info=True)

    ctx["benchmark_df"], ctx["benchmark_is_index"] = _fetch_benchmark(store, SCAN_LOOKBACK)
    ctx["snapshot"] = _global_snapshot()
    ctx["sector_map"] = _sector_map()
    return ctx


# --------------------------------------------------------------------------- #
# One scan: analyse -> track -> write -> message
# --------------------------------------------------------------------------- #
def analyse(scan_mode: str, ctx: dict, source: str) -> int:
    """Returns a process exit code (0 = fine, 1 = no data at all)."""
    _ensure_report_directories()
    bars = ctx["bars"]
    now = session.now_ist()                  # AFTER the fetch: the volume projection must match the data's age
    run_notes: list[str] = []
    if ctx["kind"] == "watchlist":
        wl = ctx["watchlist"]
        run_notes.append(f"Fast afternoon check: the {len(bars)} stocks on last evening's watchlist ({wl['as_of']}), re-scored with "
                         f"live prices against the whole market's yardstick; market breadth is as of that close")
    elif ctx["fetch"].get("requested") and ctx["fetch"]["loaded"] < 0.9 * ctx["fetch"]["requested"] and ctx["fetch"]["loaded"] > 0:
        run_notes.append(f"Partial scan: {ctx['fetch']['loaded']} of {ctx['fetch']['requested']} stocks were loaded before the "
                         f"time limit or the broker's rate limit, so the list may miss some names")

    # --- 1. the scoreboard: replay tracked trades on completed sessions -------------------------
    report = {"checked": 0, "events": []}
    try:
        if ctx["track_bars"]:
            report = outcomes.update_trades(ctx["track_bars"], now)
    except Exception:  # noqa: BLE001
        logger.exception("Scoreboard: could not update the tracked trades - ledger left as it was")

    # --- 2. delivery % ----------------------------------------------------------------------------
    ref = pl.reference_date(bars)
    dlv20, dmeta = _delivery(ref, fast=ctx["kind"] == "watchlist")
    if dlv20 is None and ctx["kind"] == "watchlist":
        saved = (ctx["watchlist"] or {}).get("dlv20") or {}
        if saved:
            dlv20 = pd.Series(saved, dtype=float)        # last evening's values, better than none
    if dlv20 is not None:
        logger.info(f"📦 Delivery %: {dmeta.get('symbols', len(dlv20))} stocks, newest day {dmeta.get('newest', 'n/a')}")
    else:
        logger.info(f"📦 Delivery %: not used this run ({dmeta.get('reason', 'unavailable')})")

    # --- 3. selection ---------------------------------------------------------------------------------
    try:
        exclude = outcomes.active_symbols(today=session.ist_date(now))
    except Exception:  # noqa: BLE001
        logger.exception("Scoreboard: could not read the ledger for repeats")
        exclude = set()
    wl = ctx["watchlist"]
    result = pl.select_picks(
        scan_mode, bars, now=now, dlv20=dlv20, sector_map=ctx["sector_map"],
        stats=(wl["stats"] if wl else None), exclude=exclude,
        skip_symbols={config.RS_BENCHMARK}, breadth_hint=(wl["breadth"] if wl else None),
    )
    if dlv20 is None and result.used_delivery:
        result.used_delivery = False
    if session.session_state(now) == "open" and result.ref_date and result.ref_date < session.ist_date(now):
        run_notes.append(f"The newest price bar is from {result.ref_date.strftime('%a %d %b')}, not today - today may be a market holiday, "
                         f"so this list is based on that close")
    f = result.funnel
    b50 = (result.breadth or {}).get("breadth50")
    b50_text = f"{b50:.0%}" if isinstance(b50, (int, float)) and math.isfinite(b50) else "n/a"
    logger.info(f"🔎 [{scan_mode.upper()}] funnel: {f['loaded']} loaded -> {f['scored']} scored -> {f['fresh']} fresh + "
                f"{f['primed']} primed setups -> {f['passed_quality']} in the top half by quality -> "
                f"{f['repeats']} already on the list -> {f['picks']} picks | breadth {b50_text} ({result.regime})")

    # --- 4. messages and files -----------------------------------------------------------------------------
    try:
        led = outcomes.load_ledger()
        open_rows = outcomes.open_positions(led).to_dict("records")
        tracked_block = notify.format_tracked_block(report["events"], open_rows)
        scoreboard_line = outcomes.format_scoreboard_line(led)
    except Exception:  # noqa: BLE001
        logger.exception("Scoreboard: could not summarise the ledger")
        tracked_block, scoreboard_line = "", ""

    try:
        outcomes.log_new_picks(result.picks, now, regime=result.regime)
    except Exception:  # noqa: BLE001
        logger.exception("Scoreboard: could not log the new picks")

    market_line = _market_line(ctx)
    try:
        summary.write_scan_outputs(scan_mode, result, now, market_line=market_line, tracked_text=tracked_block,
                                   scoreboard_line=scoreboard_line, notes=run_notes)
    except Exception:  # noqa: BLE001
        logger.exception("Could not write the report files")

    # the next afternoon's BTST watchlist: only from a full-market run on a COMPLETED session
    if ctx["kind"] == "universe" and not result.live and len(result.feats) >= MIN_WATCHLIST_SAVE:
        try:
            new_wl = pl.build_watchlist(result, dlv20)
            if new_wl and save_watchlist(new_wl):
                logger.info(f"📝 Saved BTST watchlist for the next session: {len(new_wl['symbols'])} stocks (as of {new_wl['as_of']})")
        except Exception:  # noqa: BLE001
            logger.exception("Could not build the BTST watchlist")

    sent = notify.notify_scan(result, scan_mode, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, now=now,
                              market_line=market_line, tracked_block=tracked_block,
                              scoreboard_line=scoreboard_line, notes=run_notes)
    if not sent:
        logger.warning("The scan message was not delivered to Telegram (not configured, or the send failed) - the files have the full result")

    ok = len(bars) > 0
    runguard.record_run(scan_mode, now, source, len(result.picks), ok=ok)
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_scan(scan_mode: str, test_limit=None, source: str | None = None) -> int:
    source = source or runguard.run_source()
    now0 = session.now_ist()
    skip, why = runguard.should_skip(scan_mode, source, now0)
    if skip:
        logger.info(f"⏭️  Skipping this {scan_mode} run: {why}")
        return 0
    try:
        store = _get_store()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Broker login failed: {_safe(e)}")
        _alert(f"⚠️ The {scan_mode.upper()} scan could not start: Angel One login failed. Check the broker secrets / TOTP in the repo settings.")
        return 1
    ctx = fetch_phase(store, [scan_mode], test_limit, source, now0)
    return analyse(scan_mode, ctx, source)


def cmd_run_all(test_limit=None, source: str | None = None) -> int:
    source = source or runguard.run_source()
    now0 = session.now_ist()
    modes = []
    for m in ("btst", "swing"):
        skip, why = runguard.should_skip(m, source, now0)
        if skip:
            logger.info(f"⏭️  Skipping the {m} part: {why}")
        else:
            modes.append(m)
    if not modes:
        return 0
    try:
        store = _get_store()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Broker login failed: {_safe(e)}")
        _alert("⚠️ The scans could not start: Angel One login failed. Check the broker secrets / TOTP in the repo settings.")
        return 1
    logger.info("⚡ Central data lake: downloading the market once for both scans...")
    ctx = fetch_phase(store, ["all"] if len(modes) > 1 else modes, test_limit, source, now0)
    code = 0
    for m in modes:
        code = max(code, analyse(m, ctx, source))
    return code


def cmd_market(args=None) -> int:
    """
    On-demand market-direction readout (the "Market Direction (Manual)" workflow). No stock scan and no
    option strikes - just which way the market is leaning, sent to Telegram and printed in the run log.
    """
    from scanner.market_direction import (
        assess_market_direction, format_direction_message, fetch_index_history, to_plain_text,
    )
    try:
        store = _get_store()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Angel One login failed ({_safe(e)}) - index data will come from Yahoo Finance only")
        store = None

    nifty_df, nifty_source = fetch_index_history("Nifty 50", store)
    bank_df, bank_source = fetch_index_history("Bank Nifty", store)
    snapshot = _global_snapshot()
    direction = assess_market_direction(nifty_df, snapshot, bank_df=bank_df, nifty_source=nifty_source, bank_source=bank_source)
    message = format_direction_message(direction)
    logger.info("🧭 Market direction readout:\n" + to_plain_text(message))
    if not notify.send_telegram_message(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, message):
        logger.warning("The readout was not delivered to Telegram (not configured, or the send failed) - the log above has the full text")
    return 0


def cmd_options(args=None, shared_store=None) -> int:
    """Index option setups (manual only; not part of the automated run)."""
    from ai.features import build_features
    from reports.generator import daily_options_report
    from scanner.index_options import recommend_index_options

    store = shared_store if shared_store else _get_store()
    plans, checked, fetch_failures = [], 0, 0
    for index_symbol in config.INDEX_UNIVERSE:
        checked += 1
        raw_symbol = index_symbol.strip().upper()
        try:
            df = store.get_bars(_get_angelone_mapped_symbol(raw_symbol), lookback_days=250)
        except Exception:  # noqa: BLE001
            fetch_failures += 1
            continue
        if df is None or df.empty or len(df) < 100:
            continue
        plans.extend(recommend_index_options(raw_symbol, df, build_features(df, df)))

    daily_options_report(plans)
    logger.info(f"🔎 [OPTIONS] funnel: {checked} indices checked ({fetch_failures} fetch failures) -> {len(plans)} setup(s) found")
    diagnostic = ""
    if not plans:
        diagnostic = (f"Checked {checked} index(es) ({', '.join(config.INDEX_UNIVERSE)}); {fetch_failures} failed to fetch. "
                      + ("All fetches failed - check the broker connection/logs." if fetch_failures == checked and checked > 0
                         else "Data came through fine; no valid option setup met the criteria today."))
    notify.notify_option_results(plans, config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, diagnostic=diagnostic)
    return 0


def cmd_calibrate(scan_mode: str = "swing") -> int:
    """
    Live calibration: do higher-quality picks really do better in the tracked trades? Reads the ledger only
    (no broker needed) and writes reports/output/live_scoreboard.md.
    """
    from backtest.engine import live_scoreboard_report
    path, text = live_scoreboard_report(outcomes.load_ledger(), scan_mode=None)
    logger.info("Live scoreboard report written to " + path + "\n" + text)
    return 0


def cmd_backtest(scan_mode: str = "swing", test_days: int = 250) -> int:
    """
    Replays today's selection rule over the recent past on the bars Angel One returns, with the same trade
    simulator the live scoreboard uses. (Price-only quality score: delivery history is not available here.)
    """
    from backtest.engine import run_backtest, summarize_backtest, write_backtest_report
    store = _get_store()
    universe = _load_universe()
    bars, info = _fetch(store, universe, 700, None)
    if not bars:
        logger.error("No price data was loaded - nothing to backtest")
        return 1
    trades = run_backtest(bars, scan_mode=scan_mode, test_days=test_days)
    stats = summarize_backtest(trades)
    path = write_backtest_report(trades, stats, scan_mode, n_stocks=len(bars))
    logger.info(f"Backtest report written to {path}")
    logger.info(f"Summary: {stats}")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan_btst", "scan_swing", "market", "options", "run_all", "calibrate", "backtest"])
    parser.add_argument("--test-limit", dest="test_limit", default=os.getenv("TRADING_TEST_LIMIT") or None,
                        help="Limit scan to N symbols for testing")
    parser.add_argument("--calibrate-mode", dest="calibrate_mode", default="swing", choices=["btst", "swing"],
                        help="Which scan mode's history to calibrate/backtest")
    parser.add_argument("--source", default=None, choices=["schedule", "timer", "manual"],
                        help="Where this run came from (default: env TRADING_RUN_SOURCE, else manual)")
    args = parser.parse_args(argv)
    _install_log_scrubbing()

    try:
        if args.command == "scan_btst":
            return cmd_scan("btst", args.test_limit, args.source)
        if args.command == "scan_swing":
            return cmd_scan("swing", args.test_limit, args.source)
        if args.command == "run_all":
            return cmd_run_all(args.test_limit, args.source)
        if args.command == "market":
            return cmd_market(args)
        if args.command == "options":
            return cmd_options(args)
        if args.command == "calibrate":
            return cmd_calibrate(args.calibrate_mode)
        if args.command == "backtest":
            return cmd_backtest(args.calibrate_mode)
    except Exception as e:  # noqa: BLE001 - the last line of defence: say so, loudly, instead of failing silently
        logger.exception(f"{args.command} failed")
        if args.command in ("scan_btst", "scan_swing", "run_all"):
            _alert(f"⚠️ The {args.command.replace('scan_', '').upper()} scan failed: {type(e).__name__}: {_safe(e)[:300]}. "
                   f"See the run log in GitHub Actions.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

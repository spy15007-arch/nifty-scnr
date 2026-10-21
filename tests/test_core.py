"""
Self-contained tests: no network, no broker, no Telegram. Run:  python -m unittest discover tests -v

They use small synthetic markets (random walks with one planted breakout) so they run in seconds, and check the
parts where a silent mistake would cost money: the trade simulator, the session clock, the run guard, the setup
and plan arithmetic, the scoreboard (including re-runs and split gaps), the message splitter and the full
`main.py scan_*` flow with a stub broker.
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime
from html.parser import HTMLParser
from unittest import mock

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402
from data import nse_eod  # noqa: E402
from reports import notify  # noqa: E402
from scanner import footprint as fp  # noqa: E402
from scanner import pipeline as pl  # noqa: E402
from scanner import runguard, session  # noqa: E402
from scanner import tradesim as ts  # noqa: E402
from tracking import outcomes  # noqa: E402

IST = session.IST


def at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- #
# synthetic market
def make_market(n_stocks=120, n_bars=330, end=date(2026, 10, 13), seed=1, plant=True):
    """Random-walk stocks with enough turnover to pass the liquidity floor; 'PLANT' breaks out on the last bar."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n_bars).tz_localize("Asia/Kolkata")
    bars = {}
    for k in range(n_stocks):
        price0 = rng.uniform(50, 2000)
        rets = rng.normal(0.0002, rng.uniform(0.012, 0.03), n_bars)
        close = price0 * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, 0.004, n_bars))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n_bars)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n_bars)))
        vol = (rng.uniform(8e7, 4e8) / close) * rng.uniform(0.7, 1.3, n_bars)       # 8-40 crore a day
        bars[f"S{k:03d}"] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)
    if plant:
        # a calm stock in a steady 12-month up-trend, coiled under 100, then a +4% breakout on 3x volume on the last bar
        t = np.arange(n_bars)
        close = 60 + 38 * (t / (n_bars - 2)) + rng.normal(0, 0.15, n_bars)
        close[-1] = close[-2] * 1.045
        open_ = np.r_[close[0], close[:-1]] * 1.001
        high = np.maximum(open_, close) * 1.002
        low = np.minimum(open_, close) * 0.998
        high[-1] = close[-1] * 1.004
        vol = np.full(n_bars, 6e8 / 80.0)
        vol[-1] *= 3.2
        bars["PLANT"] = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)
    return bars


# --------------------------------------------------------------------------- #
class TradeSim(unittest.TestCase):
    def plan(self, mode="stop", trigger=100.0, atr=0.02, **kw):
        return ts.TradePlan(mode, trigger, atr, **kw)

    def test_close_mode_is_open_immediately(self):
        st = ts.new_state(self.plan("close"))
        self.assertEqual(st.status, ts.OPEN)
        self.assertAlmostEqual(st.init_stop, 100 * (1 - 2.5 * 0.02))

    def test_buy_stop_fills_then_stops_out(self):
        p = self.plan()
        st = ts.replay(p, [(99, 99.5, 98, 99, ), (99.5, 101, 99, 100.5), (100, 100.5, 94, 95)])
        self.assertEqual(st.status, ts.CLOSED)
        self.assertEqual(st.exit_reason, "stop")
        o = ts.outcome(p, st)
        self.assertAlmostEqual(o["gross"], 95 / 100 - 1, places=6) if st.exit_px == 95 else None
        self.assertLess(o["net"], o["gross"])                       # costs deducted

    def test_gap_above_trigger_fills_at_the_open_and_gap_too_far_is_skipped(self):
        p = self.plan()
        st = ts.replay(p, [(101.5, 103, 101, 102)])
        self.assertEqual((st.status, st.fill_px), (ts.OPEN, 101.5))
        st = ts.replay(p, [(104, 106, 103, 105)])                   # opened more than 3% past the trigger
        self.assertEqual((st.status, st.exit_reason), (ts.UNFILLED, "gapped_past_trigger"))

    def test_expiry_and_same_day_stop(self):
        p = self.plan(valid_sessions=3)
        st = ts.replay(p, [(95, 98, 94, 96)] * 3)
        self.assertEqual((st.status, st.exit_reason), (ts.UNFILLED, "expired"))
        st = ts.replay(p, [(99, 102, 90, 91)])                      # fills and breaks the stop on the same bar
        self.assertEqual((st.status, st.exit_reason), (ts.CLOSED, "same_day_stop"))

    def test_trailing_stop_only_moves_up_and_gap_through_fills_at_open(self):
        p = self.plan("close", 100.0, 0.02)
        st = ts.new_state(p)
        stops = []
        for o, h, l, c in [(100, 104, 99, 103), (103, 112, 102, 111), (111, 111.5, 106, 107)]:
            ts.step(p, st, o, h, l, c)
            stops.append(st.trail)
        self.assertEqual(stops, sorted(stops))
        self.assertGreater(stops[-1], 100 * (1 - 2.5 * 0.02))
        ts.step(p, st, 90, 91, 88, 89)                              # gaps below the stop
        self.assertEqual((st.status, st.exit_reason, st.exit_px), (ts.CLOSED, "gap_stop", 90))

    def test_time_exit(self):
        p = self.plan("close", 100.0, 0.02, max_hold=5)
        st = ts.replay(p, [(100, 101, 99.5, 100.5)] * 10)
        self.assertEqual((st.status, st.exit_reason), (ts.CLOSED, "time"))

    def test_dict_roundtrip_survives_csv(self):
        p = self.plan()
        st = ts.replay(p, [(99, 101, 98.5, 100.5)])
        rows = pd.DataFrame([{**ts.plan_to_dict(p), **ts.state_to_dict(st)}]).astype(str).to_dict("records")[0]
        p2, st2 = ts.plan_from_dict(rows), ts.state_from_dict(rows)
        self.assertEqual((p2.entry_mode, p2.valid_sessions), (p.entry_mode, p.valid_sessions))
        self.assertEqual((st2.status, st2.fill_n), (st.status, st.fill_n))


class Session(unittest.TestCase):
    def test_states_and_volume_fraction(self):
        self.assertEqual(session.session_state(at(2026, 10, 13, 8, 0)), "pre_open")
        self.assertEqual(session.session_state(at(2026, 10, 13, 14, 0)), "open")
        self.assertEqual(session.session_state(at(2026, 10, 13, 16, 0)), "closed")
        self.assertEqual(session.session_state(at(2026, 10, 17, 11, 0)), "closed")          # Saturday
        self.assertAlmostEqual(session.volume_fraction(at(2026, 10, 13, 14, 0)), 0.64, places=2)
        self.assertEqual(session.volume_fraction(at(2026, 10, 13, 17, 0)), 1.0)
        self.assertGreaterEqual(session.volume_fraction(at(2026, 10, 13, 9, 16)), 0.05)

    def test_partial_bar(self):
        self.assertTrue(session.bar_is_partial(date(2026, 10, 13), at(2026, 10, 13, 14, 0)))
        self.assertFalse(session.bar_is_partial(date(2026, 10, 12), at(2026, 10, 13, 14, 0)))
        self.assertFalse(session.bar_is_partial(date(2026, 10, 13), at(2026, 10, 13, 16, 0)))


class RunGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "markers.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manual_always_runs(self):
        runguard.record_run("btst", at(2026, 10, 13, 14, 0), "manual", 3, path=self.path)
        self.assertFalse(runguard.should_skip("btst", "manual", at(2026, 10, 13, 15, 0), self.path)[0])

    def test_late_scheduled_btst_is_skipped_but_on_time_one_runs(self):
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 16, 40), self.path)[0])
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 14, 25), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 13, 50), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 13, 14, 40), self.path)[0])

    def test_duplicates_are_skipped_in_both_directions(self):
        runguard.record_run("btst", at(2026, 10, 13, 13, 47), "schedule", 4, path=self.path)
        self.assertTrue(runguard.should_skip("btst", "timer", at(2026, 10, 13, 14, 0), self.path)[0])
        runguard.record_run("swing", at(2026, 10, 13, 16, 5), "timer", 4, path=self.path)
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 13, 22, 30), self.path)[0])
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 1, 10), self.path)[0])   # after midnight, same session
        self.assertFalse(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 16, 5), self.path)[0])  # next session

    def test_failed_run_does_not_block_a_retry(self):
        runguard.record_run("swing", at(2026, 10, 13, 16, 5), "timer", 0, ok=False, path=self.path)
        self.assertFalse(runguard.should_skip("swing", "schedule", at(2026, 10, 13, 20, 0), self.path)[0])


class SetupAndPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = make_market()

    def test_planted_breakout_is_a_fresh_pick_with_a_correct_plan(self):
        now = at(2026, 10, 13, 17, 0)
        res = pl.select_picks("swing", self.bars, now=now)
        self.assertFalse(res.live)
        self.assertIn("PLANT", [p.symbol for p in res.picks])
        p = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertEqual((p.setup, p.entry_mode), ("fresh", "stop"))
        self.assertAlmostEqual(p.risk_pct, 2.5 * p.atr_pct, places=6)
        self.assertLessEqual(p.stop, p.entry_price * (1 - p.risk_pct) + 0.051)
        self.assertGreater(p.max_entry, p.entry_price)
        for v in (p.entry_price, p.stop, p.max_entry):
            self.assertAlmostEqual(round(v / 0.05) * 0.05, v, places=6)          # tick-rounded
        self.assertGreater(p.qty, 0)
        self.assertLessEqual(p.position_value, config.CAPITAL_INR * config.MAX_POSITION_PCT / 100 + p.entry_price)
        fresh_idx = [i for i, q in enumerate(res.picks) if q.setup == "fresh"]
        primed_idx = [i for i, q in enumerate(res.picks) if q.setup == "primed"]
        if fresh_idx and primed_idx:
            self.assertLess(max(fresh_idx), min(primed_idx))                       # fresh listed before primed

    def test_live_fresh_is_buy_now_and_partial_volume_is_projected(self):
        live = {s: d.copy() for s, d in self.bars.items()}
        d = live["PLANT"]
        d.iloc[-1, d.columns.get_loc("volume")] *= 0.64                             # 2:00 PM: 64% of the day's volume so far
        res = pl.select_picks("btst", live, now=at(2026, 10, 13, 14, 0))
        self.assertTrue(res.live)
        p = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertEqual(p.entry_mode, "close")
        self.assertAlmostEqual(p.vol_ratio, pl.fp.compute_features(self.bars["PLANT"])["rv15"], delta=0.4)

    def test_repeats_are_reported_not_repicked(self):
        res = pl.select_picks("swing", self.bars, now=at(2026, 10, 13, 17, 0), exclude={"PLANT"})
        self.assertNotIn("PLANT", [p.symbol for p in res.picks])
        self.assertIn("PLANT", [s for s, _ in res.repeats])

    def test_no_price_cap_and_illiquid_stocks_are_dropped(self):
        res = pl.select_picks("swing", self.bars, now=at(2026, 10, 13, 17, 0), max_picks=1000)
        self.assertTrue(any(p.price > 500 for p in res.picks) or any(d["close"].iloc[-1] > 500 for d in self.bars.values()))
        thin = {s: d.copy() for s, d in self.bars.items()}
        thin["PLANT"]["volume"] = thin["PLANT"]["volume"] / 1000.0
        res2 = pl.select_picks("swing", thin, now=at(2026, 10, 13, 17, 0))
        self.assertNotIn("PLANT", [p.symbol for p in res2.picks])

    def test_watchlist_rescoring_matches_the_full_run(self):
        now = at(2026, 10, 13, 17, 0)
        full = pl.select_picks("swing", self.bars, now=now, max_picks=1000)
        wl = pl.build_watchlist(full, max_n=60)
        self.assertIsNotNone(wl)
        json.dumps(wl)                                                              # serialisable
        sub = {s: self.bars[s] for s in wl["symbols"]}
        part = pl.select_picks("swing", sub, now=now, stats=wl["stats"], breadth_hint=wl["breadth"], max_picks=1000)
        a = full.feats["quality_pct"].reindex(wl["symbols"])
        b = part.feats["quality_pct"].reindex(wl["symbols"])
        self.assertLess((a - b).abs().max(), 2.5)                                   # same yardstick -> same rank (up to the quantile grid)


class Scoreboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "trades.csv")
        self.bars = make_market()
        self.now = at(2026, 10, 13, 17, 0)
        self.res = pl.select_picks("swing", self.bars, now=self.now, max_picks=20)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _extend(self, sym, rows):
        """Append future sessions (open, high, low, close) to a stock's bars."""
        d = self.bars[sym]
        idx = pd.bdate_range(start=d.index[-1].tz_localize(None) + pd.Timedelta(days=1), periods=len(rows)).tz_localize("Asia/Kolkata")
        add = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
        add["volume"] = d["volume"].iloc[-20:].mean()
        self.bars[sym] = pd.concat([d, add])

    def test_log_is_idempotent_and_update_is_replay_safe(self):
        n1 = outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        n2 = outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        p = next(p for p in self.res.picks if p.symbol == "PLANT")
        t = p.entry_price
        self._extend("PLANT", [(t * 1.002, t * 1.03, t * 1.0, t * 1.02), (t * 1.02, t * 1.06, t * 1.01, t * 1.05)])
        later = at(2026, 10, 15, 17, 0)
        rep = outcomes.update_trades(self.bars, later, path=self.path)
        kinds = [e["kind"] for e in rep["events"] if e["symbol"] == "PLANT"]
        self.assertIn("filled", kinds)
        a = outcomes.load_ledger(self.path)
        outcomes.update_trades(self.bars, later, path=self.path)
        b = outcomes.load_ledger(self.path)
        pd.testing.assert_frame_equal(a, b)                                          # second run changes nothing
        row = b[b["symbol"] == "PLANT"].iloc[0]
        self.assertEqual(row["status"], "OPEN")
        self.assertGreater(float(row["trail"]), float(row["init_stop"]))             # the trail has moved up

    def test_split_gap_voids_an_unfinished_trade(self):
        outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        p = next(p for p in self.res.picks if p.symbol == "PLANT")
        t = p.entry_price
        self._extend("PLANT", [(t * 1.002, t * 1.03, t * 1.0, t * 1.02), (t * 0.5, t * 0.52, t * 0.49, t * 0.5)])   # 2-for-1 split
        rep = outcomes.update_trades(self.bars, at(2026, 10, 15, 17, 0), path=self.path)
        self.assertIn("void", [e["kind"] for e in rep["events"] if e["symbol"] == "PLANT"])
        self.assertEqual(outcomes.scoreboard(outcomes.load_ledger(self.path))["void"], 1)

    def test_unfilled_is_neither_win_nor_loss_and_cooldown_blocks_repeats(self):
        outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        p = next(p for p in self.res.picks if p.symbol == "PLANT")
        self._extend("PLANT", [(p.entry_price * 0.97,) * 4] * 6)                     # never reaches the buy-stop
        outcomes.update_trades(self.bars, at(2026, 10, 21, 17, 0), path=self.path)
        s = outcomes.scoreboard(outcomes.load_ledger(self.path))
        self.assertGreaterEqual(s["unfilled"], 1)
        self.assertEqual(s["closed"], 0)
        self.assertIn("PLANT", outcomes.active_symbols(outcomes.load_ledger(self.path), today=date(2026, 10, 21)))
        self.assertNotIn("PLANT", outcomes.active_symbols(outcomes.load_ledger(self.path), today=date(2026, 12, 1)))

    def test_forming_bar_is_never_used(self):
        outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        p = next(p for p in self.res.picks if p.symbol == "PLANT")
        t = p.entry_price
        self._extend("PLANT", [(t * 1.01, t * 1.05, t * 0.5, t * 1.0)])               # a wild forming bar for 'today'
        today = date(2026, 10, 14)
        rep = outcomes.update_trades(self.bars, at(2026, 10, 14, 11, 0), path=self.path)   # market open on that same date
        self.assertEqual([e for e in rep["events"] if e["symbol"] == "PLANT"], [])
        self.assertEqual(outcomes.load_ledger(self.path).query("symbol == 'PLANT'").iloc[0]["status"], "PENDING")
        self.assertEqual(today, session.ist_date(at(2026, 10, 14, 11, 0)))


class Delivery(unittest.TestCase):
    def test_parse_and_average(self):
        rows = ["SYMBOL, SERIES, DATE1, DELIV_PER"] + [f"S{i}, EQ, 12-Oct-2026, {20 + i % 60}" for i in range(150)] + ["X, BE, 12-Oct-2026, 99"]
        df = nse_eod.parse_bhavcopy("\n".join(rows), expect_day=date(2026, 10, 12))
        self.assertEqual(len(df), 150)
        self.assertIsNone(nse_eod.parse_bhavcopy("\n".join(rows), expect_day=date(2026, 10, 13)))   # a different day's file
        self.assertIsNone(nse_eod.parse_bhavcopy("<html>blocked</html>"))

    def test_lagged_mean_excludes_the_signal_day_and_goes_stale(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "d.csv")
            days = pd.bdate_range(end="2026-10-13", periods=25)
            rec = [(d.date().isoformat(), "AAA", 50.0 if d < days[-1] else 99.0) for d in days]
            pd.DataFrame(rec, columns=["date", "symbol", "dlv_pct"]).to_csv(path, index=False)
            s, meta = nse_eod.get_dlv20(date(2026, 10, 13), path)
            self.assertAlmostEqual(float(s["AAA"]), 50.0)                              # the 13th itself (99) is excluded
            s2, meta2 = nse_eod.get_dlv20(date(2026, 12, 30), path)
            self.assertIsNone(s2)                                                      # far too old to use
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Messages(unittest.TestCase):
    class _Check(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.err = [], []

        def handle_starttag(self, tag, attrs):
            if tag not in ("b", "i", "u", "s", "code", "pre", "a"):
                self.err.append(tag)
            self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack or self.stack[-1] != tag:
                self.err.append("/" + tag)
            else:
                self.stack.pop()

    def test_message_is_valid_telegram_html_and_splits_under_the_limit(self):
        bars = make_market()
        res = pl.select_picks("swing", bars, now=at(2026, 10, 13, 17, 0), max_picks=15)
        msg = notify.format_scan_message(res, "swing", now=at(2026, 10, 13, 17, 0), market_line="🧭 <b>Market:</b> ⚪ <b>Unclear</b>")
        chk = self._Check()
        chk.feed(msg)
        self.assertEqual((chk.err, chk.stack), ([], []))
        chunks = notify.split_message(msg)
        self.assertTrue(all(len(c) <= 4096 for c in chunks))
        self.assertEqual("\n\n".join(chunks).replace("\n\n", "\n"), msg.replace("\n\n", "\n"))   # nothing lost

    def test_oversized_block_is_split_on_lines(self):
        big = "\n".join(f"line {i} " + "x" * 80 for i in range(120))
        chunks = notify.split_message(big, max_len=3900)
        self.assertTrue(all(len(c) <= 3900 for c in chunks) and len(chunks) >= 3)

    def test_scrub_masks_secrets(self):
        self.assertEqual(notify.scrub("url https://x/botABC123SECRET/send", "ABC123SECRET"), "url https://x/bot***/send")


# --------------------------------------------------------------------------- #
class EndToEnd(unittest.TestCase):
    """main.py with a stub broker, in a scratch directory."""

    @classmethod
    def setUpClass(cls):
        cls.bars = make_market(n_stocks=200)                                       # above main.MIN_WATCHLIST_SAVE, like the real universe

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        os.makedirs("data_cache")
        import main as M
        self.M = M
        self.sent = []

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, argv, now, source="manual", bars=None):
        bars = self.bars if bars is None else bars

        class Store:
            def get_universe_bars(s, symbols, lookback_days, max_duration_seconds=5400):
                s.symbols = list(symbols)
                return {k: bars[k].tail(lookback_days).copy() for k in symbols if k in bars}

            def get_bars(s, symbol, lookback_days):
                raise KeyError(symbol)

        store = Store()

        def post(url, json=None, timeout=None, **kw):
            self.sent.append(json["text"])
            return mock.Mock(ok=True, status_code=200, text="ok")

        os.environ["TRADING_RUN_SOURCE"] = source
        with mock.patch.object(self.M, "_get_store", lambda: store), \
                mock.patch.object(self.M, "_load_universe", lambda test_limit=None: list(bars)), \
                mock.patch.object(self.M, "_global_snapshot", lambda: {}), \
                mock.patch.object(self.M, "_sector_map", lambda: {}), \
                mock.patch.object(nse_eod, "update_cache", lambda *a, **k: {}), \
                mock.patch.object(config, "TELEGRAM_BOT_TOKEN", "123456:TESTTOKENVALUE"), \
                mock.patch.object(config, "TELEGRAM_CHAT_ID", "1"), \
                mock.patch("requests.post", post), \
                mock.patch.object(session, "now_ist", lambda: now):
            logging.disable(logging.CRITICAL)
            try:
                code = self.M.main(argv)
            finally:
                logging.disable(logging.NOTSET)
        return code, store

    def test_swing_run_writes_everything_and_saves_the_watchlist(self):
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        self.assertEqual(code, 0)
        csv = pd.read_csv("scan_results_swing.csv")
        self.assertIn("PLANT", list(csv["symbol"]))
        for f in ("summary_swing.md", "summary.md", "tracking/trades.csv", "data_cache/btst_watchlist.json", "data_cache/run_markers.json"):
            self.assertTrue(os.path.exists(f), f)
        self.assertTrue(self.sent and any("PLANT" in m for m in self.sent))
        self.assertTrue(all(len(m) <= 4096 for m in self.sent))
        self.assertEqual(_load_json("data_cache/btst_watchlist.json")["as_of"], "2026-10-13")

    def test_afternoon_btst_fetches_only_the_watchlist_and_a_late_cron_is_skipped(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        nxt = {s: d.copy() for s, d in self.bars.items()}
        for s, d in nxt.items():                                                    # next session = a copy of the last bar, a day later
            last = d.iloc[[-1]].copy()
            last.index = [d.index[-1] + pd.Timedelta(days=1)]
            nxt[s] = pd.concat([d, last])
        self.sent.clear()
        code, store = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), source="timer", bars=nxt)
        wl = _load_json("data_cache/btst_watchlist.json")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(store.symbols), config.WATCHLIST_SIZE)
        self.assertEqual(set(store.symbols), set(wl["symbols"][:config.WATCHLIST_SIZE]))
        self.sent.clear()
        code, store2 = self._run(["scan_btst"], at(2026, 10, 14, 18, 30), source="schedule", bars=nxt)
        self.assertEqual((code, self.sent), (0, []))                                 # too late + already done

    def test_a_thin_fetch_never_overwrites_the_watchlist(self):
        small = {k: v for k, v in list(self.bars.items())[:90]}                       # a partial fetch: far fewer stocks than the universe
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0), bars=small)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists("data_cache/btst_watchlist.json"))

    def test_no_data_is_reported_and_exit_code_is_nonzero(self):
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0), bars={})
        self.assertEqual(code, 1)
        self.assertTrue(any("No price data" in m for m in self.sent))


if __name__ == "__main__":
    unittest.main()

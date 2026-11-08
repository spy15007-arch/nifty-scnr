"""
Self-contained tests: no network, no broker, no Telegram. Run:  python -m unittest discover tests -v

They use small synthetic markets (random walks with one planted breakout) so they run in seconds, and check the
parts where a silent mistake would cost money: the trade simulator, the session clock, the run guard, the setup
and plan arithmetic, the scoreboard (including re-runs and split gaps), the message splitter and the full
`main.py scan_*` flow with a stub broker.
"""
import dataclasses
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import types
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


class PullbackOrder(unittest.TestCase):
    """The research-only 'limit' entry (waiting for a pull-back): it must behave like a real buy-limit and leave the live modes alone."""

    @staticmethod
    def _plan(limit=95.0, atr=0.03, **kw):
        return ts.TradePlan("limit", limit, atr, **kw)

    def test_fills_at_the_limit_when_the_low_touches_it(self):
        p = self._plan()
        st = ts.replay(p, [(100.0, 101.0, 96.0, 99.0), (99.0, 99.0, 94.0, 96.0)])              # day 1 stays above, day 2 dips through 95
        self.assertEqual((st.status, st.fill_n, st.fill_px), (ts.OPEN, 2, 95.0))
        self.assertAlmostEqual(st.init_stop, 95.0 * (1 - p.risk_pct))

    def test_an_open_below_the_limit_pays_the_better_price(self):
        st = ts.replay(self._plan(), [(93.0, 96.0, 92.0, 95.0)])
        self.assertEqual((st.status, st.fill_px), (ts.OPEN, 93.0))

    def test_expires_when_the_price_never_comes_down(self):
        p = self._plan(valid_sessions=3)
        st = ts.replay(p, [(100, 102, 97, 101)] * 5)
        self.assertEqual((st.status, st.exit_reason, st.fill_n), (ts.UNFILLED, "expired", -1))
        self.assertIsNone(ts.outcome(p, st))                                                      # never held a position

    def test_filled_and_stopped_out_the_same_day_is_booked_as_a_loss(self):
        p = self._plan()
        st = ts.replay(p, [(97.0, 97.0, 80.0, 82.0)])                                            # touches 95 and keeps falling through the stop
        self.assertEqual((st.status, st.exit_reason), (ts.CLOSED, "same_day_stop"))
        self.assertLess(ts.outcome(p, st)["net"], 0)

    def test_a_gap_down_far_below_the_limit_is_still_bought_at_the_open(self):
        """The 'skip if it gaps too far' rule belongs to buy-stops (chasing). A buy-limit simply pays the lower open."""
        st = ts.replay(self._plan(), [(80.0, 85.0, 79.0, 84.0)])
        self.assertEqual((st.status, st.fill_px), (ts.OPEN, 80.0))

    def test_after_the_fill_it_is_managed_exactly_like_any_other_trade(self):
        p = self._plan(max_hold=5)
        st = ts.replay(p, [(97, 98, 94, 96)] + [(96, 97, 95.5, 96.5)] * 6)
        self.assertEqual((st.status, st.exit_reason), (ts.CLOSED, "time"))
        self.assertGreaterEqual(st.exit_n - st.fill_n, 5)

    def test_the_live_modes_are_unchanged(self):
        stop = ts.TradePlan("stop", 100.0, 0.03)
        self.assertEqual(ts.replay(stop, [(99, 101, 98, 100.5)]).fill_px, 100.0)                 # a buy-stop still fills when the HIGH reaches it
        self.assertEqual(ts.replay(stop, [(99, 99.5, 94, 95)]).status, ts.PENDING)               # ... and not when the price only falls
        self.assertEqual(ts.new_state(ts.TradePlan("close", 100.0, 0.03)).status, ts.OPEN)
        with self.assertRaises(ValueError):
            ts.new_state(ts.TradePlan("market", 100.0, 0.03))


def fake_yfinance(frames: dict, calls: list):
    """A stand-in for the `yfinance` module: Ticker(t).history(**kw) hands back frames[t] (or raises it, when it is an Exception)."""
    class Ticker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, **kw):
            calls.append((self.ticker, kw))
            got = frames[self.ticker]
            if isinstance(got, Exception):
                raise got
            return got
    return types.SimpleNamespace(Ticker=Ticker)


class YahooBars(unittest.TestCase):
    """The research data loader (used only by the back-test): the tidy-up rules and the fetch loop, with a fake Yahoo."""

    @staticmethod
    def raw(n=5, end="2026-10-28", cols=("Open", "High", "Low", "Close", "Volume")):
        idx = pd.bdate_range(end=end, periods=n).tz_localize("Asia/Kolkata")
        base = np.linspace(100.0, 104.0, n)
        data = {"Open": base, "High": base * 1.01, "Low": base * 0.99, "Close": base * 1.002, "Volume": np.full(n, 1e6)}
        return pd.DataFrame({c: data[c] for c in cols}, index=idx)

    def test_symbol_to_ticker(self):
        from data import yahoo_bars as yb
        self.assertEqual(yb.yahoo_ticker("reliance"), "RELIANCE.NS")
        self.assertEqual(yb.yahoo_ticker(" M&M "), "M&M.NS")
        self.assertEqual(yb.yahoo_ticker("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")

    def test_tidy_gives_lower_case_columns_oldest_first(self):
        from data import yahoo_bars as yb
        d = yb.tidy(self.raw().iloc[::-1], now_ist=at(2026, 10, 30, 12, 0))                      # handed over newest first
        self.assertEqual(list(d.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(d), 5)
        self.assertTrue(d.index.is_monotonic_increasing)

    def test_tidy_drops_todays_forming_bar_until_the_evening(self):
        from data import yahoo_bars as yb
        raw = self.raw(end="2026-10-30")                                                         # the last row is Friday the 30th
        self.assertEqual(len(yb.tidy(raw, now_ist=at(2026, 10, 30, 12, 0))), 4)                  # market still open: that bar is forming
        self.assertEqual(len(yb.tidy(raw, now_ist=at(2026, 10, 30, 17, 5))), 5)                  # after the close it is a finished bar
        self.assertEqual(len(yb.tidy(self.raw(end="2026-10-28"), now_ist=at(2026, 10, 30, 12, 0))), 5)   # an earlier day's bar stays

    def test_tidy_drops_contradictory_missing_and_duplicate_rows(self):
        from data import yahoo_bars as yb
        raw = self.raw(n=6)
        raw.iloc[2, raw.columns.get_loc("High")] = raw.iloc[2]["Low"] * 0.5                      # a high below the low
        raw.iloc[3, raw.columns.get_loc("Close")] = np.nan                                       # no close
        raw = pd.concat([raw, raw.iloc[[0]]])                                                    # the first day twice
        d = yb.tidy(raw, now_ist=at(2026, 10, 30, 12, 0))
        self.assertEqual(len(d), 4)
        self.assertTrue(d.index.is_unique)

    def test_tidy_gives_nothing_for_empty_or_incomplete_tables(self):
        from data import yahoo_bars as yb
        self.assertIsNone(yb.tidy(None))
        self.assertIsNone(yb.tidy(pd.DataFrame()))
        self.assertIsNone(yb.tidy(self.raw(cols=("Open", "High", "Low", "Close"))))              # no volume column

    def test_fetch_loop_reports_what_it_got(self):
        from data import yahoo_bars as yb
        frames = {"GOOD.NS": self.raw(n=320), "SHORT.NS": self.raw(n=50), "GONE.NS": pd.DataFrame(), "BROKEN.NS": RuntimeError("yahoo is down")}
        calls: list = []
        with mock.patch.dict(sys.modules, {"yfinance": fake_yfinance(frames, calls)}), mock.patch.object(yb.time, "sleep", lambda s: None):
            bars, rep = yb.fetch_yahoo_bars(["GOOD", "SHORT", "GONE", "BROKEN"], start="2016-01-01", pause=0, attempts=2)
        self.assertEqual(list(bars), ["GOOD"])
        self.assertEqual((rep["asked"], rep["usable"], rep["no_data_n"], rep["too_short_n"]), (4, 1, 2, 1))
        self.assertEqual(sorted(rep["no_data"]), ["BROKEN", "GONE"])
        self.assertTrue(any("RuntimeError" in msg for msg, _ in rep["errors"]), rep["errors"])
        for _, kw in calls:                       # split-adjusted but not dividend-adjusted, daily, from the asked-for start
            self.assertEqual((kw["start"], kw["interval"], kw["auto_adjust"]), ("2016-01-01", "1d", False))
        self.assertEqual(sum(1 for t, _ in calls if t == "BROKEN.NS"), 2)                         # tried twice, then given up

    def test_fetch_loop_stops_at_its_time_budget_and_says_so(self):
        from data import yahoo_bars as yb
        frames = {f"S{i}.NS": self.raw(n=320) for i in range(3)}
        with mock.patch.dict(sys.modules, {"yfinance": fake_yfinance(frames, [])}), mock.patch.object(yb.time, "sleep", lambda s: None):
            bars, rep = yb.fetch_yahoo_bars(["S0", "S1", "S2"], pause=0, max_seconds=-1)
        self.assertEqual((len(bars), rep["stopped"]), (0, "time budget"))


class ResearchStudy(unittest.TestCase):
    """The extra columns and pull-back variants the back-test records for the 'already spiked' study. They must never change the real trades."""

    @classmethod
    def setUpClass(cls):
        from backtest import engine
        cls.engine = engine
        cls.bars = make_market(n_stocks=100, n_bars=420, seed=3, plant=False)
        cls.trades = engine.run_backtest(cls.bars, "swing", test_days=120)

    @staticmethod
    def _core(trades):
        keys = ("symbol", "signal_date", "setup", "status", "fill_date", "exit_date", "exit_reason", "net", "R", "quality_pct")
        def clean(x):
            return "nan" if isinstance(x, float) and x != x else x
        return [tuple(clean(t[k]) for k in keys) for t in trades]

    def test_every_signal_carries_the_research_columns(self):
        self.assertGreater(len(self.trades), 30)
        need = {"run_up", "z_run_up", "ret5", "gain1d", "sma200_chg", "ret252", "breadth50",
                "pb1_status", "pb1_net", "pb1_R", "pb2_status", "pb2_net", "pb2_R"}
        need |= {f"{name}_{col}" for name, _, _ in self.engine.STOP_VARIANTS for col in ("status", "exit", "net", "R")}
        need |= {"path_R"}
        for t in self.trades:
            self.assertFalse(need - set(t), f"missing {need - set(t)} in {t}")
            if t["status"] in ("UNFILLED", "PENDING", "VOID"):
                self.assertEqual(t["path_R"], "", t)                      # a trade that never held a position has no path

    def test_the_path_marks_follow_the_trade_every_five_sessions(self):
        """Buy at 100 with a 5% risk. Marks are in R after the 0.35% cost - (close / 100 - 1 - 0.0035) / 0.05 - at the fill session and every 5th one
        after it, up to but not including the session the trade was sold; the sale itself is the trade's own R."""
        def bars(closes):
            idx = pd.date_range("2026-01-01", periods=len(closes) + 1, freq="B")
            opens = [100.0] + list(closes[:-1])                                                      # each session opens at the last close (the first at the trigger)
            rows = [(99.0, 99.5, 98.5, 99.0)] + [(o, c + 0.4, c - 0.3, c) for o, c in zip(opens, closes)]     # the signal bar, then one bar per session
            return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"])
        plan = ts.TradePlan("stop", 100.0, 0.02)
        self.assertAlmostEqual(plan.risk_pct, 0.05)
        rising =[100.5, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111]                   # sessions 1..12; the buy-stop fills at 100 in session 1
        d = bars(rising)
        st, _, _, _ = self.engine._simulate(plan, d, 0)
        self.assertEqual((st.status, st.fill_n, st.sessions), (ts.OPEN, 1, 12))
        self.assertEqual(self.engine._path_marks(plan, st, d, 0), "0.03;0.93;1.93")               # sessions 1, 6 and 11 (closes 100.5, 105, 110)
        # sold in session 8 by the trail (highest close 106, trail 95.4; the low of 90 touches it): only the marks before the sale
        d2 = bars(rising[:7] + [100.0])
        d2.iloc[8, d2.columns.get_loc("low")] = 90.0
        st2, oc2, exit_pos, _ = self.engine._simulate(plan, d2, 0)
        self.assertEqual((st2.status, st2.exit_reason, st2.exit_n), (ts.CLOSED, "trail", 8))
        self.assertEqual(self.engine._path_marks(plan, st2, d2, 0), "0.03;0.93")                  # sessions 1 and 6 (8 is the sale)
        # bought and stopped out the same session: no time between the two for a mark
        d3 = bars([100.5])
        d3.iloc[1, d3.columns.get_loc("low")] = 90.0
        st3, _, _, _ = self.engine._simulate(plan, d3, 0)
        self.assertEqual((st3.status, st3.exit_reason), (ts.CLOSED, "same_day_stop"))
        self.assertEqual(self.engine._path_marks(plan, st3, d3, 0), "")
        # an order that never fills holds nothing
        st4, _, _, _ = self.engine._simulate(ts.TradePlan("stop", 150.0, 0.02), d, 0)
        self.assertEqual(self.engine._path_marks(ts.TradePlan("stop", 150.0, 0.02), st4, d, 0), "")

    def test_the_extra_columns_do_not_change_the_real_trades(self):
        with mock.patch.object(self.engine, "PULLBACKS", ()), mock.patch.object(self.engine, "STOP_VARIANTS", ()):
            plain = self.engine.run_backtest(self.bars, "swing", test_days=120)
        self.assertFalse(any("pb1_status" in t or "tr3_status" in t for t in plain))
        self.assertEqual(self._core(plain), self._core(self.trades))

    def test_the_stop_variants_are_scored_on_the_same_risk_as_the_real_trade(self):
        """Same entry, same first stop, same risk: R * risk_pct gives the net return back, and an order that never filled is unfilled in every variant."""
        held = 0
        for t in self.trades:
            for name, _, _ in self.engine.STOP_VARIANTS:
                self.assertEqual(t[f"{name}_status"] == "UNFILLED", t["status"] == "UNFILLED", (name, t))
                if t[f"{name}_status"] in ("CLOSED", "OPEN"):
                    held += 1
                    self.assertAlmostEqual(t[f"{name}_R"] * t["risk_pct"], t[f"{name}_net"], delta=0.0005)
                else:
                    self.assertFalse(np.isfinite(t[f"{name}_net"]), t)
        self.assertGreater(held, 30)

    def test_the_break_even_and_lock_steps_lift_the_stop_after_a_close_reaches_the_mark(self):
        """Buy 100 with a 5% risk (first stop 95, trail 5 ATR = 10% below the highest close). A close of 106 is +1.2R: the shipped plan's trail
        is then only 95.4, but the break-even step has lifted the stop to 100 - so a dip to 99 stops that variant out at 100 and not the plan."""
        def bars(rows):
            idx = pd.date_range("2026-01-01", periods=len(rows) + 1, freq="B")
            first = [(99.0, 99.5, 98.5, 99.0)]                                   # the signal bar
            return pd.DataFrame(first + rows, index=idx, columns=["open", "high", "low", "close"])
        plan = ts.TradePlan("stop", 100.0, 0.02)
        self.assertAlmostEqual(plan.risk_pct, 0.05)
        path = [(100.0, 101.0, 99.5, 100.5),        # the buy-stop fills at 100; stop 95
                (101.0, 106.5, 100.5, 106.0),       # a close of 106 = +1.2R: plan's trail 95.4, break-even step 100
                (104.0, 104.5, 99.0, 100.0)]        # dips to 99
        plain, _, _, _ = self.engine._simulate(plan, bars(path), 0)
        self.assertEqual(plain.status, ts.OPEN)
        self.assertAlmostEqual(plain.trail, 95.4, places=6)
        step, oc, _, _ = self.engine._simulate(plan, bars(path), 0, ((1.0, 0.0),))
        self.assertEqual((step.status, step.exit_reason), (ts.CLOSED, "trail"))
        self.assertAlmostEqual(step.exit_px, 100.0, places=6)
        self.assertAlmostEqual(oc["net"], -config.ROUND_TRIP_COST, places=6)              # out at the buy price: only the cost is lost
        # +2R: a close of 110.5 lifts the second step to +1R (105); a dip to 104 then exits there, while the one-step variant is still in
        path2 = path[:2] + [(107.0, 111.0, 106.0, 110.5), (108.0, 109.0, 104.0, 105.0)]
        one, _, _, _ = self.engine._simulate(plan, bars(path2), 0, ((1.0, 0.0),))
        two, oc2, _, _ = self.engine._simulate(plan, bars(path2), 0, ((1.0, 0.0), (2.0, 1.0)))
        self.assertEqual(one.status, ts.OPEN)
        self.assertEqual((two.status, two.exit_reason), (ts.CLOSED, "trail"))
        self.assertAlmostEqual(two.exit_px, 105.0, places=6)
        self.assertAlmostEqual(oc2["net"], 0.05 - config.ROUND_TRIP_COST, places=6)
        # a close that never reaches the mark changes nothing
        calm = [(100.0, 101.0, 99.5, 100.5), (100.5, 103.0, 100.0, 103.0), (102.0, 102.5, 96.0, 97.0)]
        a, _, _, _ = self.engine._simulate(plan, bars(calm), 0)
        b, _, _, _ = self.engine._simulate(plan, bars(calm), 0, ((1.0, 0.0), (2.0, 1.0)))
        self.assertEqual((a.status, a.exit_px, a.exit_reason), (b.status, b.exit_px, b.exit_reason))

    def test_the_stop_table_compares_the_same_finished_signals(self):
        def row(*results):
            """results = (status, net) for the shipped plan and then for each STOP_VARIANTS entry, in order."""
            out = {}
            for prefix, (status, net) in zip([""] + [n for n, _, _ in self.engine.STOP_VARIANTS], results):
                key = f"{prefix}_" if prefix else ""
                out[f"{key}status"], out[f"{key}net"], out[f"{key}R"] = status, net, (net * 10 if net == net else np.nan)
            return out
        n_var, nan = len(self.engine.STOP_VARIANTS), float("nan")
        df = pd.DataFrame([
            row(*[("CLOSED", 0.10)] + [("CLOSED", 0.05)] * n_var),                       # filled and finished in every variant
            row(*[("CLOSED", -0.04)] + [("CLOSED", -0.02)] * n_var),
            row(*[("UNFILLED", nan)] * (n_var + 1)),                                     # never filled: counts as 0
            row(*[("OPEN", nan)] + [("CLOSED", 0.30)] * n_var),                          # unfinished in the shipped plan: left out of every row
        ])
        lines = self.engine._stop_table(df)
        rows = [[c.strip() for c in l.split("|")[2:-1]] for l in lines if l.startswith("| ") and ":--" not in l and "Stop plan" not in l]
        self.assertEqual(len(rows), n_var + 1, lines)
        shipped, first = rows[0], rows[1]
        self.assertEqual((shipped[0], shipped[1], shipped[3]), ("3", "67%", "+3.00%"))      # 3 signals, 2 filled, (10% - 4%) / 2 per filled trade
        self.assertEqual(shipped[-1], "+2.00%")                                              # 6% over 3 signals, the unfilled one counting as 0
        self.assertEqual((first[0], first[1], first[3], first[-1]), ("3", "67%", "+1.50%", "+1.00%"))
        self.assertEqual(self.engine._stop_table(df.iloc[[3]]), [])                         # nothing finished in every variant -> no table
        self.assertEqual(self.engine._stop_table(pd.DataFrame()), [])

    def test_run_up_is_what_the_card_measures(self):
        """A fresh breakout leaves its own breakout day out of the run-up; a coiled stock uses its full 5-day gain."""
        seen = set()
        for t in self.trades:
            if not (np.isfinite(t["ret5"]) and np.isfinite(t["gain1d"])):
                continue
            want = fp.gain_before_latest_session(t["ret5"], t["gain1d"]) if t["setup"] == "fresh" else t["ret5"]
            self.assertAlmostEqual(t["run_up"], want, places=4)
            seen.add(t["setup"])
        self.assertTrue(seen, "no signal with a measurable run-up")

    def test_the_replay_starts_only_when_a_year_of_history_exists(self):
        """A long history's first year has no 12-month look-back; those sessions must not produce signals."""
        everything = self.engine.run_backtest(self.bars, "swing", test_days=10_000)
        first_full = pd.Timestamp(next(iter(self.bars.values())).index[252]).tz_localize(None).date().isoformat()
        self.assertTrue(everything)
        self.assertGreaterEqual(min(t["signal_date"] for t in everything), first_full)

    def test_the_pullback_variants_are_scored_on_the_same_risk_as_the_real_trade(self):
        """R = net return / initial risk, and the buy-limit plans use the signal's own ATR - so R * risk_pct must give the net return back."""
        held = 0
        for t in self.trades:
            for name, _, _ in self.engine.PULLBACKS:
                if t[f"{name}_status"] in ("CLOSED", "OPEN"):                      # an open trade is marked at the latest close
                    held += 1
                    self.assertAlmostEqual(t[f"{name}_R"] * t["risk_pct"], t[f"{name}_net"], delta=0.0005)
                else:
                    self.assertFalse(np.isfinite(t[f"{name}_net"]), t)          # an order that never held a position has no result
        self.assertGreater(held, 10)

    def test_tables_render_in_a_readable_order(self):
        lines = self.engine.research_tables(self.trades)
        text = "\n".join(lines)
        for head in ("Run-up before the signal (z)", "The card's warning", "200-day trend", "Spike and trend together"):
            self.assertIn(head, text)
        title = next(i for i, l in enumerate(lines) if "Run-up before the signal (z)" in l)
        labels = []
        for l in lines[title + 4:]:                       # skip: title, blank, header, divider
            if not l.startswith("| "):
                break
            labels.append(l.split("|")[1].strip()[:2])
        self.assertEqual(labels, sorted(labels))
        self.assertGreaterEqual(len(labels), 3)

    def test_tables_stay_quiet_when_there_is_too_little_to_say(self):
        self.assertEqual(self.engine.research_tables([]), [])
        self.assertEqual(self.engine.research_tables(self.trades[:5]), [])
        plain = [{k: v for k, v in t.items() if k != "z_run_up"} for t in self.trades]               # an old-style report without the columns
        self.assertEqual(self.engine.research_tables(plain), [])

    def test_the_study_arithmetic_on_a_tiny_known_example(self):
        """Six identical months of 4 signals: group 1 = +10% and -5% (both filled), group 0 = +2% and one order that never filled."""
        from backtest import spike_study as sp
        rows = []
        for m in range(1, 7):
            for status, net, R, grp in (("CLOSED", 0.10, 1.0, 1), ("CLOSED", -0.05, -0.5, 1), ("UNFILLED", np.nan, np.nan, 0), ("CLOSED", 0.02, 0.2, 0)):
                rows.append({"signal_date": f"2026-{m:02d}-10", "status": status, "net": net, "R": R, "grp": grp})
        df = pd.DataFrame(rows)
        for c in ("z_run_up", "run_up", "sma200_chg", "breadth50", "atr_pct"):
            df[c] = 0.0
        df["setup"], df["regime"] = "primed", "mixed"
        df["signal_date"] = pd.to_datetime(df["signal_date"])
        df["year"] = df["signal_date"].dt.year
        months = df["signal_date"].dt.to_period("M")
        df["mi"] = months.map({m: i for i, m in enumerate(sorted(months.unique()))}).astype(int)
        st = sp.Study(df, reps=300)
        g1, g0 = (df["grp"] == 1).to_numpy(), (df["grp"] == 0).to_numpy()
        point, lo, hi, n = st.mean(g1, "filled")
        self.assertEqual(n, 12)
        self.assertAlmostEqual(point, 0.025)                                             # (0.10 - 0.05) / 2 per filled trade
        self.assertAlmostEqual(st.mean(g1, "signal")[0], 0.025)                          # every signal of group 1 filled
        self.assertAlmostEqual(st.mean(g0, "filled")[0], 0.02)
        self.assertAlmostEqual(st.mean(g0, "signal")[0], 0.01)                           # the order that never filled counts as 0
        self.assertEqual(st.mean(g0, "signal")[3], 12)
        d, lo, hi, p = st.diff(g1, g0, "signal")
        self.assertAlmostEqual(d, 0.015)
        self.assertGreater(p, 0.99)                                                      # identical months: no resample can flip the sign
        self.assertAlmostEqual(lo, hi)                                                   # ... and the range collapses onto the point
        self.assertAlmostEqual(st.paired(st.net, 0 * st.net, g1)[0], 0.025)
        self.assertAlmostEqual(st.paired(st.net, 0.5 * st.net, g1)[0], 0.0125)

    def test_the_study_script_runs_and_writes_its_report(self):
        from backtest import spike_study as sp
        with tempfile.TemporaryDirectory() as tmp:
            csv, out = os.path.join(tmp, "trades.csv"), os.path.join(tmp, "study.md")
            pd.DataFrame(self.trades).to_csv(csv, index=False)
            text = "\n".join(sp.analyse(sp.load(csv), reps=200))
            for head in ("== 1. THE QUESTION", "== 2. Gradient", "== 3. Swings", "== 4. 200-day trend", "== 5. Spike and trend",
                         "== 6. WAITING FOR A PULL-BACK", "== 7. Level and lumpiness"):
                self.assertIn(head, text)
            self.assertIn(f"Signals {len(self.trades)}", text)
            with mock.patch("builtins.print"):
                self.assertEqual(sp.main([csv, "--write", out]), 0)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("How to read it", fh.read())

    def test_the_study_script_refuses_a_trades_file_without_the_research_columns(self):
        from backtest import spike_study as sp
        with tempfile.TemporaryDirectory() as tmp:
            csv = os.path.join(tmp, "old.csv")
            pd.DataFrame([{k: v for k, v in t.items() if k != "z_run_up"} for t in self.trades]).to_csv(csv, index=False)
            with self.assertRaises(SystemExit):
                sp.load(csv)

    def test_the_gap_study_groups_and_arithmetic_on_a_tiny_known_example(self):
        """Six identical months of 6 signals: the groups must hold exactly the signals they claim and the means must come out by hand."""
        from backtest import gap_study as gs
        nan = float("nan")
        spec = (                                                    # setup, close vs the old 50-day high (+ = above), 200-day trend, status, net
            ("primed", -0.005, 0.06, "CLOSED", 0.04),               # nearer half (0.5% below), strong uptrend
            ("primed", -0.025, 0.06, "CLOSED", 0.10),               # further half, strong uptrend
            ("primed", -0.025, 0.00, "UNFILLED", nan),              # further half, trend not rising, the order never filled
            ("primed", -0.025, 0.00, "CLOSED", -0.02),              # further half, trend not rising
            ("fresh", 0.010, 0.06, "CLOSED", 0.50),                 # a fresh breakout: in none of the groups
            ("primed", 0.004, 0.02, "CLOSED", 0.01),                # closed above the old high, rising but not strong: nearer half
        )
        rows = []
        for m in range(1, 7):
            for setup, ext, trend, status, net in spec:
                rows.append({"signal_date": f"2026-{m:02d}-10", "setup": setup, "ext_pct": ext, "sma200_chg": trend, "status": status, "net": net,
                             "R": net * 10, "fill_date": f"2026-{m:02d}-12" if status == "CLOSED" else ""})
        df = pd.DataFrame(rows)
        for c in ("z_run_up", "run_up", "breadth50", "atr_pct", "pb1_net", "pb2_net"):
            df[c] = 0.0
        df["regime"], df["pb1_status"], df["pb2_status"] = "mixed", "UNFILLED", "UNFILLED"
        with tempfile.TemporaryDirectory() as tmp:
            csv = os.path.join(tmp, "tiny.csv")
            df.to_csv(csv, index=False)
            frame = gs.load(csv)
        g = gs.groups(frame)
        self.assertEqual({k: int(v.sum()) for k, v in g.items()},
                         {"primed": 30, "near": 12, "far": 18, "near_strong": 6, "near_rising": 6, "near_flat": 0,
                          "far_strong": 6, "far_rising": 0, "far_flat": 12, "band0": 6, "band1": 6, "band2": 0, "band3": 18})
        st = gs.ss.Study(frame, reps=200)
        self.assertAlmostEqual(st.mean(g["far_strong"], "filled")[0], 0.10)
        self.assertAlmostEqual(st.mean(g["far_flat"], "filled")[0], -0.02)
        self.assertAlmostEqual(st.mean(g["far_flat"], "signal")[0], -0.01)                  # the order that never filled counts as 0
        self.assertAlmostEqual(st.mean(g["far"], "signal")[0], 0.08 / 3)
        text = "\n".join(gs.analyse(frame, reps=200))
        for head in ("== 1.", "== 2.", "== 3.", "== 4."):
            self.assertIn(head, text)
        self.assertIn("close 0%-1% below the 50-day high", text)
        self.assertNotIn("1%-2% below the 50-day high", text)                               # a band with no signals is not printed

    def test_the_gap_study_script_runs_and_refuses_an_old_file(self):
        from backtest import gap_study as gs
        with tempfile.TemporaryDirectory() as tmp:
            csv, out, old = (os.path.join(tmp, n) for n in ("trades.csv", "gap.md", "old.csv"))
            pd.DataFrame(self.trades).to_csv(csv, index=False)
            text = "\n".join(gs.analyse(gs.load(csv), reps=200))
            for head in ("== 1.", "== 2.", "== 3.", "== 4."):
                self.assertIn(head, text)
            self.assertIn(f"Signals {len(self.trades)}", text)
            with mock.patch("builtins.print"):
                self.assertEqual(gs.main([csv, "--write", out]), 0)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("How to read it", fh.read())
            pd.DataFrame([{k: v for k, v in t.items() if k != "ext_pct"} for t in self.trades]).to_csv(old, index=False)
            with self.assertRaises(SystemExit):
                gs.load(old)

    def test_the_stop_study_arithmetic_on_a_tiny_known_example(self):
        """Six identical months of 4 signals; the plans differ only in how the two filled trades ended."""
        from backtest import stop_study as ssd
        nan = float("nan")
        variants = [n for n, _, _ in self.engine.STOP_VARIANTS]
        rows = []
        for m in range(1, 7):
            for status, base, var, peak in (("CLOSED", 0.10, 0.04, 3.5), ("CLOSED", -0.05, -0.02, 0.2), ("UNFILLED", nan, nan, nan), ("OPEN", nan, 0.30, 0.5)):
                row = {"signal_date": f"2026-{m:02d}-10", "status": status, "net": base, "R": base * 10, "exit_reason": "time" if base == 0.10 else "stop",
                       "peak_R": peak}
                for n in variants:
                    v_status = "CLOSED" if status in ("CLOSED", "OPEN") else status         # the shipped plan's open trade has closed in the variants
                    row.update({f"{n}_status": v_status, f"{n}_net": var, f"{n}_R": var * 10, f"{n}_exit": "trail" if var == 0.04 else "stop"})
                rows.append(row)
        df = pd.DataFrame(rows)
        for c in ("z_run_up", "run_up", "sma200_chg", "breadth50", "atr_pct", "pb1_net", "pb2_net"):
            df[c] = 0.0
        df["setup"], df["regime"], df["pb1_status"], df["pb2_status"] = "primed", "mixed", "UNFILLED", "UNFILLED"
        with tempfile.TemporaryDirectory() as tmp:
            csv = os.path.join(tmp, "tiny.csv")
            df.to_csv(csv, index=False)
            frame = ssd.load(csv)
        fin = ssd.finished(frame)
        self.assertEqual(int(fin.sum()), 18)                                              # the open trade is unfinished under the shipped plan: left out
        self.assertAlmostEqual(float(ssd.per_signal(frame, "", "net")[fin].mean()), 0.05 / 3)           # (10% - 5% + 0 for the unfilled order) / 3
        self.assertAlmostEqual(float(ssd.per_signal(frame, variants[0], "net")[fin].mean()), 0.02 / 3)  # (4% - 2% + 0) / 3
        text = "\n".join(ssd.analyse(frame, reps=200))
        for head in ("== 1.", "== 2.", "== 3.", "== 4."):
            self.assertIn(head, text)
        line = next(l for l in text.splitlines() if l.startswith(f"{variants[0]:<10}") and " n=" in l and "%" in l)
        self.assertIn("-1.00%", line)                                                     # (0.02 - 0.05) / 3 per signal, in every month alike

    def test_the_stop_study_script_runs_and_refuses_an_old_file(self):
        from backtest import stop_study as ssd
        with tempfile.TemporaryDirectory() as tmp:
            csv, out, old = (os.path.join(tmp, n) for n in ("trades.csv", "stop.md", "old.csv"))
            pd.DataFrame(self.trades).to_csv(csv, index=False)
            text = "\n".join(ssd.analyse(ssd.load(csv), reps=200))
            for head in ("== 1.", "== 2.", "== 3.", "== 4.", "shipped", "tr3"):
                self.assertIn(head, text)
            with mock.patch("builtins.print"):
                self.assertEqual(ssd.main([csv, "--write", out]), 0)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("How to read it", fh.read())
            pd.DataFrame([{k: v for k, v in t.items() if not k.startswith("tr3_")} for t in self.trades]).to_csv(old, index=False)
            with self.assertRaises(SystemExit):
                ssd.load(old)

    def test_the_portfolio_study_runs_on_the_replay_output_and_refuses_an_old_file(self):
        from backtest import portfolio_study as ps
        with tempfile.TemporaryDirectory() as tmp:
            csv, out, old = (os.path.join(tmp, n) for n in ("trades.csv", "portfolio.md", "old.csv"))
            pd.DataFrame(self.trades).to_csv(csv, index=False)
            frame = ps.load(csv)
            self.assertTrue(ps.Book(frame).has_path)
            text = "\n".join(ps.analyse(frame, seeds=3))
            for head in ("== 1.", "== 2.", "== 3.", "== 4.", "== 5.", "If every signal were bought"):
                self.assertIn(head, text)
            self.assertNotIn("no weekly path column", text)
            with mock.patch("builtins.print"):
                self.assertEqual(ps.main([csv, "--write", out]), 0)
            with open(out, encoding="utf-8") as fh:
                self.assertIn("How to read it", fh.read())
            # a file from before the weekly path column still works, and says its drawdowns leave out open losses
            no_path = os.path.join(tmp, "no_path.csv")
            pd.DataFrame([{k: v for k, v in t.items() if k != "path_R"} for t in self.trades]).to_csv(no_path, index=False)
            self.assertIn("no weekly path column", "\n".join(ps.analyse(ps.load(no_path), seeds=2)))
            pd.DataFrame([{k: v for k, v in t.items() if k != "risk_pct"} for t in self.trades]).to_csv(old, index=False)
            with self.assertRaises(SystemExit):
                ps.load(old)

    def test_the_pullback_table_compares_the_same_finished_signals(self):
        def row(a, b, c):
            """a, b, c = (status, net) for the buy-stop, pull-back 1 and pull-back 2."""
            out = {}
            for prefix, (status, net) in zip(("", "pb1_", "pb2_"), (a, b, c)):
                out[f"{prefix}status"], out[f"{prefix}net"], out[f"{prefix}R"] = status, net, (net * 10 if net == net else np.nan)
            return out
        nan = float("nan")
        df = pd.DataFrame([
            row(("CLOSED", 0.10), ("CLOSED", 0.20), ("UNFILLED", nan)),
            row(("UNFILLED", nan), ("UNFILLED", nan), ("CLOSED", -0.05)),
            row(("OPEN", nan), ("CLOSED", 0.50), ("CLOSED", 0.50)),                    # not finished in every style: left out of all three
            row(("VOID", nan), ("CLOSED", 0.50), ("CLOSED", 0.50)),                    # a suspected split gap: left out too
        ])
        lines = self.engine._pullback_table(df)
        rows = {l.split("|")[1].strip(): [c.strip() for c in l.split("|")[2:-1]] for l in lines if l.startswith("| ") and "Signals" not in l and ":--" not in l}
        self.assertEqual(len(rows), 3, lines)
        stop, pb1, pb2 = rows["Buy-stop at the trigger (what the cards say)"], rows["Buy-limit 1 ATR below the trigger, live 5 sessions"], \
            rows["Buy-limit 2 ATR below the trigger, live 10 sessions"]
        self.assertEqual((stop[0], stop[1], stop[2], stop[5]), ("2", "50%", "+10.00%", "+5.00%"))     # 2 signals, 1 filled, 10% per filled, 5% per signal
        self.assertEqual((pb1[0], pb1[1], pb1[2], pb1[5]), ("2", "50%", "+20.00%", "+10.00%"))
        self.assertEqual((pb2[0], pb2[1], pb2[2], pb2[5]), ("2", "50%", "-5.00%", "-2.50%"))
        self.assertEqual(self.engine._pullback_table(df.iloc[2:]), [])                   # nothing finished in every style -> no table
        self.assertEqual(self.engine._pullback_table(pd.DataFrame()), [])


class PortfolioStudy(unittest.TestCase):
    """The account simulation of backtest/portfolio_study.py on tiny, hand-worked books. Every trade here risks a 5% stop distance, so with 1% of an
    account of 100 at risk the money in a stock is 1 / 0.05 = 20, and a trade of +2R adds 2.0 to the account."""

    @staticmethod
    def frame(rows, with_path=True):
        """The trades file of hand-written rows. A row without an exit is an order that never filled, unless it names another `status` (OPEN, say)."""
        out = []
        for r in rows:
            row = {"symbol": r["symbol"], "signal_date": r["signal"], "fill_date": r.get("fill", ""), "exit_date": r.get("exit", ""),
                   "status": r.get("status", "CLOSED" if r.get("exit") else "UNFILLED"), "R": r.get("R", float("nan")), "net": r.get("R", 0.0) * 0.05,
                   "risk_pct": r.get("rf", 0.05), "regime": r.get("regime", "healthy"), "setup": r.get("setup", "primed"),
                   "quality_pct": r.get("q", 70.0), "sma200_chg": r.get("trend", 0.0), "path_R": r.get("path", "")}
            if "breadth" in r:
                row["breadth50"] = r["breadth"]                                                 # a column only when a row has one; the others get no number
            out.append(row)
        df = pd.DataFrame(out)
        if not with_path:
            df = df.drop(columns=["path_R"])
        for c in ("signal_date", "fill_date", "exit_date"):
            df[c] = pd.to_datetime(df[c], errors="coerce")                                      # a blank (never filled / not sold) becomes NaT
        return df

    @staticmethod
    def book(rows, with_path=True):
        from backtest import portfolio_study as ps
        return ps.Book(PortfolioStudy.frame(rows, with_path))

    @staticmethod
    def filled(symbol, day, R, **extra):
        """A trade signalled on `day`, bought the next business day and sold five business days after that, for R."""
        d = pd.Timestamp(day)
        return {"symbol": symbol, "signal": day, "fill": (d + pd.offsets.BDay(1)).strftime("%Y-%m-%d"), "exit": (d + pd.offsets.BDay(6)).strftime("%Y-%m-%d"),
                "R": R, **extra}

    def jan5(self):
        """Monday 5 Jan 2026: A (strong uptrend, quality 60) is bought on the 6th and sold on the 20th for +2R; B (quality 90) is bought on the 7th and
        sold on the 12th for -1R; C (quality 95) never fills (its order lapses on the 12th). Then D on the 8th: bought on the 9th, sold on 10 Feb for +1R."""
        return self.book([
            {"symbol": "A", "signal": "2026-01-05", "fill": "2026-01-06", "exit": "2026-01-20", "R": 2.0, "q": 60.0, "trend": 0.06, "path": "0.5;1.0;1.5"},
            {"symbol": "B", "signal": "2026-01-05", "fill": "2026-01-07", "exit": "2026-01-12", "R": -1.0, "q": 90.0, "path": "-0.4"},
            {"symbol": "C", "signal": "2026-01-05", "q": 95.0},
            {"symbol": "D", "signal": "2026-01-08", "fill": "2026-01-09", "exit": "2026-02-10", "R": 1.0, "q": 80.0, "path": "0.0;0.2;0.4;0.6;0.8;1.0"},
        ])

    def test_rooms_decide_which_signals_become_orders_and_the_order_of_preference_decides_who_gets_them(self):
        from backtest import portfolio_study as ps
        book = self.jan5()
        self.assertEqual((book.n, int(book.closed.sum())), (4, 3))
        # two rooms, scanner's order: the strong uptrend A first, then C (quality 95) before B (90); B finds no room -> A +2 on 100 at 1% = +2.0
        r = ps.simulate(book, "shipped", max_stocks=2, risk=0.01)
        self.assertEqual((r["placed"], r["closed"], r["unfilled"], r["full"], r["cash"]), (2, 1, 1, 2, 0))      # B on the 5th, and D on the 8th (A and C still hold both rooms)
        self.assertAlmostEqual(r["realised"], 102.0)
        # the same two rooms worked through by quality alone: C and B; B is sold at -1R for -1.0
        q = ps.simulate(book, "quality", max_stocks=2, risk=0.01)
        self.assertEqual((q["placed"], q["closed"]), (2, 1))
        self.assertAlmostEqual(q["realised"], 99.0)
        # three rooms take A, B and C; D (8th) finds them all taken (C's order lapses only on the 12th): 100 + 2 - 1 = 101
        three = ps.simulate(book, "shipped", max_stocks=3, risk=0.01)
        self.assertEqual((three["placed"], three["full"]), (3, 1))
        self.assertAlmostEqual(three["realised"], 101.0)
        # four rooms also take D, sized on the account of 100 it saw on the 8th: +1.0 more
        four = ps.simulate(book, "shipped", max_stocks=4, risk=0.01)
        self.assertEqual(four["placed"], 4)
        self.assertAlmostEqual(four["realised"], 102.0)

    def test_an_order_that_does_not_fit_the_money_is_skipped_even_when_a_room_is_free(self):
        from backtest import portfolio_study as ps
        book = self.book([
            {"symbol": "X", "signal": "2026-02-02", "fill": "2026-02-03", "exit": "2026-02-10", "R": 1.0, "q": 90.0},
            {"symbol": "Y", "signal": "2026-02-02", "fill": "2026-02-03", "exit": "2026-02-10", "R": 1.0, "q": 80.0},
        ])
        # 5% of the account at risk with a 5% stop distance = all of the account in one stock: the second cannot be paid for
        r = ps.simulate(book, "shipped", max_stocks=5, risk=0.05)
        self.assertEqual((r["placed"], r["cash"], r["full"]), (1, 1, 0))
        self.assertAlmostEqual(r["realised"], 100.0 + 5.0 * 1.0)                              # 5 at risk x +1R
        ok = ps.simulate(book, "shipped", max_stocks=5, risk=0.01)                           # 1%: 20 of 100 in each, both fit
        self.assertEqual((ok["placed"], ok["cash"]), (2, 0))
        self.assertAlmostEqual(ok["realised"], 102.0)

    def test_a_weak_market_halves_the_size_and_money_comes_back_when_a_stock_is_sold(self):
        from backtest import portfolio_study as ps
        book = self.book([
            {"symbol": "W", "signal": "2026-03-02", "fill": "2026-03-03", "exit": "2026-03-06", "R": 2.0, "regime": "weak"},
            {"symbol": "H", "signal": "2026-03-09", "fill": "2026-03-10", "exit": "2026-03-13", "R": 2.0, "regime": "healthy"},
        ])
        r = ps.simulate(book, "shipped", max_stocks=1, risk=0.01)
        # W: half size = 0.5 at risk -> +1.0 (equity 101); H sized on 101: 1.01 at risk -> +2.02
        self.assertEqual((r["placed"], r["full"]), (2, 0))
        self.assertAlmostEqual(r["realised"], 101.0 + 2.02)
        full = ps.simulate(book, "shipped", max_stocks=1, risk=0.01, weak_half=False)
        self.assertAlmostEqual(full["realised"], 102.0 + 2.0 * 1.02)

    def test_the_account_is_marked_to_market_every_friday_from_the_weekly_marks(self):
        from backtest import portfolio_study as ps
        book = self.book([{"symbol": "A", "signal": "2026-01-05", "fill": "2026-01-06", "exit": "2026-01-20", "R": 2.0, "path": "0.5;1.0;1.5"}])
        r = ps.simulate(book, "shipped", max_stocks=1, risk=0.01)
        eq = r["equity"]
        at = lambda d: float(eq[pd.Timestamp(d)])                                              # noqa: E731
        self.assertAlmostEqual(at("2026-01-05"), 100.0)                                        # the start
        self.assertAlmostEqual(at("2026-01-09"), 100.5)                                        # Friday, 3 sessions after the fill: the first mark, +0.5R of 1.0 at risk
        self.assertAlmostEqual(at("2026-01-16"), 101.0)                                        # 8 sessions after the fill: the second mark, +1.0R
        self.assertAlmostEqual(at("2026-01-20"), 102.0)                                        # sold: the trade's own +2R
        self.assertAlmostEqual(r["held"], 2 / 3)                                               # held on two of the three marked dates after the start (not the first)
        # the same account without the marks is only marked at the sale
        flat = ps.simulate(self.book([{"symbol": "A", "signal": "2026-01-05", "fill": "2026-01-06", "exit": "2026-01-20", "R": 2.0}], with_path=False),
                           "shipped", max_stocks=1, risk=0.01)["equity"]
        self.assertAlmostEqual(float(flat[pd.Timestamp("2026-01-09")]), 100.0)
        self.assertAlmostEqual(float(flat[pd.Timestamp("2026-01-20")]), 102.0)

    def test_the_curve_metrics_on_a_steady_climb_and_on_a_dip(self):
        from backtest import portfolio_study as ps
        idx = pd.date_range("2024-01-05", periods=120, freq="W-FRI")
        steady = pd.Series(100.0 * 1.005 ** np.arange(120), index=idx)
        m = ps.metrics(steady)
        self.assertAlmostEqual(m["max_dd"], 0.0)
        self.assertAlmostEqual(m["w12_worst"], 1.005 ** 52 - 1, places=6)
        self.assertEqual((m["w12_neg"], m["longest_under_weeks"]), (0.0, 0))
        self.assertAlmostEqual(m["cagr"], (1.005 ** 119) ** (365.25 / (119 * 7)) - 1, places=3)
        dip = steady.copy()
        dip.iloc[60:70] = dip.iloc[59] * 0.80                                                  # a 20% drop that lasts ten weeks, then the climb resumes
        d = ps.metrics(dip)
        self.assertAlmostEqual(d["max_dd"], -0.20, places=6)
        self.assertGreaterEqual(d["longest_under_weeks"], 10)
        self.assertGreater(d["w12_neg"], 0.0)
        years = ps.by_year(pd.Series([100.0, 110.0, 99.0, 120.0], index=pd.to_datetime(["2025-06-06", "2025-12-26", "2026-03-06", "2026-12-25"])))
        self.assertEqual(sorted(years), [2025, 2026])
        self.assertAlmostEqual(years[2025], 0.10)                                              # the first year counts from its first mark
        self.assertAlmostEqual(years[2026], 120.0 / 110.0 - 1.0)                               # later years from the last mark of the year before

    def test_if_every_signal_were_bought_the_stocks_open_at_once_and_the_money_they_need(self):
        from backtest import portfolio_study as ps
        book = self.book([
            {"symbol": "X", "signal": "2026-01-05", "fill": "2026-01-06", "exit": "2026-01-08", "R": 1.0},
            {"symbol": "Y", "signal": "2026-01-06", "fill": "2026-01-07", "exit": "2026-01-09", "R": 1.0},
        ])
        c = ps.concurrency(book)
        # open on the 6th: X; on the 7th and 8th: X and Y; on the 9th: Y   -> 1, 2, 2, 1; each is 1% / 5% = 20% of the capital
        self.assertAlmostEqual(c["avg"], 1.5)
        self.assertEqual(c["peak"], 2)
        self.assertAlmostEqual(c["money_avg"], 0.30, places=6)

    # ---- the daily cap on new picks (backtest/pick_cap_study.py) ---------------------------------------------------------------------------------
    def test_a_cap_keeps_the_first_signals_of_each_day_in_the_scanners_order(self):
        from backtest import portfolio_study as ps
        book = self.jan5()                    # rows A, B, C (5 Jan) and D (8 Jan); the scanner's order on the 5th: A (strong uptrend), C (quality 95), B (90)
        self.assertEqual(list(ps.day_ranks(book)), [1, 3, 2, 1])
        self.assertEqual(list(ps.issued_mask(book, None)), [True, True, True, True])
        self.assertEqual(list(ps.issued_mask(book, 1)), [True, False, False, True])           # the first of each day: A and D
        self.assertEqual(list(ps.issued_mask(book, 2)), [True, False, True, True])            # A and C on the 5th
        self.assertEqual(list(ps.issued_mask(book, 0)), [False] * 4)
        one = ps.issued_mask(book, 1, "random", seed=3)
        self.assertEqual((int(one[:3].sum()), bool(one[3])), (1, True))                       # one of the three of the 5th, whichever the draw gives; D is alone on its day

    def test_a_capped_account_never_sees_the_dropped_signals(self):
        from backtest import portfolio_study as ps
        book = self.jan5()
        # three rooms, no cap: A, C and B are taken, D finds them all taken; 100 + 2 (A) - 1 (B) = 101
        none = ps.simulate(book, "shipped", max_stocks=3, risk=0.01)
        self.assertEqual((none["placed"], none["capped"], none["full"]), (3, 0, 1))
        self.assertAlmostEqual(none["realised"], 101.0)
        # a cap of 1: only A is listed on the 5th (B and C never reach the account), so D finds a free room on the 8th: +2.0 (A) and +1.0 (D)
        one = ps.simulate(book, "shipped", max_stocks=3, risk=0.01, cap=1)
        self.assertEqual((one["placed"], one["closed"], one["unfilled"], one["capped"], one["full"]), (2, 2, 0, 2, 0))
        self.assertAlmostEqual(one["realised"], 103.0)
        # a cap of 2: A and C (C's order waits until the 12th), B is dropped; D still finds a room (A, C and D make three)
        two = ps.simulate(book, "shipped", max_stocks=3, risk=0.01, cap=2)
        self.assertEqual((two["placed"], two["closed"], two["unfilled"], two["capped"]), (3, 2, 1, 1))
        self.assertAlmostEqual(two["realised"], 103.0)
        # a cap above the size of every day changes nothing; a random cap still lists exactly the cap's worth of each day
        self.assertEqual(ps.simulate(book, "shipped", max_stocks=3, risk=0.01, cap=9)["capped"], 0)
        rnd = ps.simulate(book, "random", max_stocks=9, risk=0.01, seed=1, cap=1)
        self.assertEqual((rnd["placed"], rnd["capped"]), (2, 2))

    def test_the_stocks_open_at_once_can_be_counted_for_a_capped_list_too(self):
        from backtest import portfolio_study as ps
        book = self.book([
            {"symbol": "X", "signal": "2026-01-05", "fill": "2026-01-06", "exit": "2026-01-08", "R": 1.0, "q": 90.0},
            {"symbol": "Y", "signal": "2026-01-05", "fill": "2026-01-07", "exit": "2026-01-09", "R": 1.0, "q": 80.0},
        ])
        self.assertAlmostEqual(ps.concurrency(book)["avg"], 1.5)                              # 1, 2, 2, 1 as in the test above
        capped = ps.concurrency(book, ps.issued_mask(book, 1))                                # only X (the better of the two) is listed: open on the 6th, 7th and 8th
        self.assertAlmostEqual(capped["avg"], 1.0)
        self.assertEqual(capped["peak"], 1)
        self.assertAlmostEqual(capped["money_avg"], 0.20, places=6)                           # 1% / 5% = 20% of the capital
        self.assertEqual(ps.concurrency(book, np.zeros(book.n, dtype=bool)), {})              # nothing listed, nothing open

    def test_the_picks_a_cap_drops_are_compared_with_the_ones_it_keeps(self):
        from backtest import pick_cap_study as pc
        from backtest import portfolio_study as ps

        def four_days(first, second):
            rows = []
            for d, (a, b) in enumerate(zip(first, second)):
                day, nxt, late = f"2026-01-{5 + d:02d}", f"2026-01-{6 + d:02d}", f"2026-01-{12 + d:02d}"       # signal Mon-Thu, fill the next day, sold the week after
                rows.append({"symbol": f"K{d}", "signal": day, "fill": nxt, "exit": late, "R": a, "q": 90.0})  # the day's first pick (the higher quality)
                rows.append({"symbol": f"L{d}", "signal": day, "fill": nxt, "exit": late, "R": b, "q": 80.0})  # its second
            return self.book(rows)

        book = four_days([1.0, 2.0, 1.0, 2.0], [0.0, 1.0, 0.0, 1.0])
        ranks = ps.day_ranks(book)
        self.assertEqual(list(ranks), [1, 2] * 4)
        kd = pc.kept_vs_dropped(book, ranks, 1)
        # kept: 1, 2, 1, 2 (average 1.5, variance 1/3); dropped: 0, 1, 0, 1 (average 0.5, variance 1/3): standard error sqrt(1/6) = 0.408, so 1.0 / 0.408 = 2.449
        self.assertEqual((kd["n_kept"], kd["n_dropped"]), (4, 4))
        self.assertAlmostEqual(kd["mean_kept"], 1.5)
        self.assertAlmostEqual(kd["mean_dropped"], 0.5)
        self.assertAlmostEqual(kd["z"], 1.0 / np.sqrt(1.0 / 6.0), places=6)
        self.assertTrue(kd["ok"])
        self.assertIsNone(pc.kept_vs_dropped(book, ranks, 2))                                  # a cap of 2 drops nothing: nothing to compare
        # the other way round (the dropped picks did better): the same distance, the opposite sign, and the rule is not met
        worse = pc.kept_vs_dropped(four_days([0.0, 1.0, 0.0, 1.0], [1.0, 2.0, 1.0, 2.0]), ranks, 1)
        self.assertAlmostEqual(worse["z"], -kd["z"], places=6)
        self.assertFalse(worse["ok"])

    def test_the_pick_cap_rule_judges_growth_against_luck_and_drawdown_against_an_allowance(self):
        from backtest import pick_cap_study as pc
        self.assertAlmostEqual(pc.luck_band(np.linspace(0.0, 1.0, 101)), 0.4)                  # (90th - 10th percentile) / 2 = (0.9 - 0.1) / 2
        self.assertAlmostEqual(pc.luck_band([0.1, 0.1, 0.1]), pc.LUCK_FLOOR)                   # never narrower than half a point
        self.assertAlmostEqual(pc.luck_band([float("nan"), 0.1]), pc.LUCK_FLOOR)               # fewer than two real numbers
        base = {"cagr": 0.10, "max_dd": -0.20}
        up = pc.judge(base, {"cagr": 0.14, "max_dd": -0.215}, 0.03)
        self.assertEqual((up["growth"], up["better"], up["safe"]), ("better", True, True))     # +4 points beats the 3 points of luck; 1.5 points deeper is within the 2 allowed
        self.assertAlmostEqual(up["d_growth"], 0.04)
        self.assertAlmostEqual(up["d_dd"], -0.015)
        same = pc.judge(base, {"cagr": 0.12, "max_dd": -0.20}, 0.03)
        self.assertEqual((same["growth"], same["better"]), ("same", False))                    # +2 points is inside the luck band
        worse = pc.judge(base, {"cagr": 0.06, "max_dd": -0.25}, 0.03)
        self.assertEqual((worse["growth"], worse["better"], worse["safe"]), ("worse", False, False))
        good, off = {"better": True, "safe": True}, {"better": False, "safe": True}
        self.assertTrue(pc.passes([good, good], {"ok": True}))
        self.assertFalse(pc.passes([good, {"better": True, "safe": False}], {"ok": True}))     # one account draws down too deeply
        self.assertFalse(pc.passes([good, off], {"ok": True}))                                 # one account is not clearly better
        self.assertFalse(pc.passes([good, good], {"ok": False}))                               # the dropped picks were not clearly worse
        self.assertFalse(pc.passes([good, good], None))
        self.assertFalse(pc.passes([], {"ok": True}))
        self.assertEqual(pc.decide({1: True, 3: True, 5: False, 8: False}), 3)                 # the largest cap that passes: the mildest change
        self.assertIsNone(pc.decide({1: False, 2: False}))

    def test_the_signals_are_tabled_by_their_place_in_the_day_and_by_the_size_of_the_day(self):
        from backtest import pick_cap_study as pc
        from backtest import portfolio_study as ps
        book = self.jan5()                    # places [1, 3, 2, 1]; A +2R and D +1R are first of their day, C never filled (2nd), B -1R is 3rd
        places = {g["label"]: g for g in pc.rank_table(book, ps.day_ranks(book))}
        self.assertEqual(list(places), ["1st", "2nd", "3rd"])                                   # a place nobody had is not listed
        first = places["1st"]
        self.assertEqual((first["signals"], first["filled"], first["share"]), (2, 2, 1.0))
        self.assertAlmostEqual(first["mean_R"], 1.5)
        self.assertAlmostEqual(first["net"], 0.075)                                            # (0.10 + 0.05) / 2 in the fixture (net = 5% of R)
        self.assertEqual((places["2nd"]["signals"], places["2nd"]["filled"], places["2nd"]["share"]), (1, 0, 0.0))
        self.assertTrue(np.isnan(places["2nd"]["mean_R"]))
        self.assertAlmostEqual(places["3rd"]["mean_R"], -1.0)
        self.assertTrue(np.isnan(places["3rd"]["se"]))                                         # one trade has no spread
        sizes = {g["label"]: g for g in pc.day_size_table(book)}
        self.assertEqual(list(sizes), ["1 signal", "3 signals"])                               # the 8th had one signal, the 5th had three
        self.assertEqual((sizes["1 signal"]["days"], sizes["1 signal"]["signals"], sizes["1 signal"]["filled"]), (1, 1, 1))
        self.assertAlmostEqual(sizes["1 signal"]["mean_R"], 1.0)
        self.assertEqual((sizes["3 signals"]["days"], sizes["3 signals"]["signals"], sizes["3 signals"]["filled"]), (1, 3, 2))
        self.assertAlmostEqual(sizes["3 signals"]["mean_R"], 0.5)                              # A +2 and B -1
        np.testing.assert_array_equal(pc.per_day(book), [3, 1])

    def test_the_pick_cap_study_runs_end_to_end_and_ends_with_its_verdict(self):
        from backtest import pick_cap_study as pc
        rng = np.random.default_rng(5)
        days = pd.bdate_range("2024-01-01", "2024-12-31")
        rows = []
        for k, d in enumerate(days[:-45]):
            for j in range(int(rng.integers(1, 6))):
                filled = bool(rng.random() < 0.6)
                r = float(rng.normal(0.3, 1.5))
                rows.append({"symbol": f"S{k}_{j}", "signal_date": d, "status": "CLOSED" if filled else "UNFILLED",
                             "fill_date": days[k + 1] if filled else pd.NaT, "exit_date": days[k + 1 + int(rng.integers(3, 40))] if filled else pd.NaT,
                             "R": r if filled else float("nan"), "net": 0.04 * r if filled else float("nan"), "risk_pct": 0.04, "regime": "healthy",
                             "setup": "primed", "quality_pct": float(rng.uniform(50, 100)), "sma200_chg": float(rng.uniform(-0.1, 0.1)),
                             "path_R": "0.1;0.2" if filled else ""})
        lines = pc.analyse(pd.DataFrame(rows), seeds=2)
        text = "\n".join(lines)
        for section in ("== 1.", "== 2.", "== 3.", "== 4.", "== 5.", "== 6."):
            self.assertIn(section, text)
        self.assertIn("as shipped", text)
        self.assertEqual(sum(1 for line in lines if line.startswith("RESULT:")), 1)
        self.assertEqual(sum(1 for line in lines if line.startswith("\ncap ") and line.endswith(" a day")), len(pc.CAPS))     # one verdict block per cap tried
        self.assertEqual(pc.analyse(pd.DataFrame(rows).iloc[0:0]), ["No finished signals in the file."])

    # ---- the busy-day study (backtest/busy_day_study.py) ------------------------------------------------------------------------------------------
    def sized_rows(self):
        """Ten signals on four days of 1, 2, 3 and 4 signals (January 2026). The tenth, on the 8th, is still open: it counts for the size of its day but is not
        a finished signal, so the book holds nine. S1 +1R, S2 -1R (a weak market), S3 never fills, S4 +2R, S5 0R, S6 +1R, S7 +3R, S8 never fills, S9 +1R."""
        f = self.filled
        return [
            f("S1", "2026-01-05", 1.0, breadth=0.8),
            f("S2", "2026-01-06", -1.0, regime="weak", breadth=0.3), {"symbol": "S3", "signal": "2026-01-06", "breadth": 0.4},
            f("S4", "2026-01-07", 2.0), f("S5", "2026-01-07", 0.0), f("S6", "2026-01-07", 1.0),
            f("S7", "2026-01-08", 3.0), {"symbol": "S8", "signal": "2026-01-08"}, f("S9", "2026-01-08", 1.0),
            {"symbol": "S10", "signal": "2026-01-08", "fill": "2026-01-09", "status": "OPEN"},
        ]

    def two_months(self):
        """January: a quiet day with one filled trade (+1R) and a busy day with two (+2R, 0R). February: a busy day with three (+1R each) and a quiet day with two
        (-1R, 0R). Book order: A1 | A2 A3 | B1 B2 B3 | B4 B5, so the busy ones are [0 1 1 1 1 1 0 0]."""
        f = self.filled
        return self.book([
            f("A1", "2026-01-05", 1.0),
            f("A2", "2026-01-06", 2.0), f("A3", "2026-01-06", 0.0),
            f("B1", "2026-02-02", 1.0), f("B2", "2026-02-02", 1.0), f("B3", "2026-02-02", 1.0),
            f("B4", "2026-02-03", -1.0), f("B5", "2026-02-03", 0.0),
        ])

    @staticmethod
    def day_frame(premium, seed=7):
        """A made-up trades file, 2023 to mid-2025: 1 to 6 signals a day, each month with a mood of its own (common to all its trades), and the filled trades of
        busy days (4 or more signals) averaging `premium` R more than the rest."""
        rng = np.random.default_rng(seed)
        days = pd.bdate_range("2023-01-02", "2025-06-30")
        mood = {m: float(rng.normal(0.0, 0.3)) for m in sorted({(d.year, d.month) for d in days})}
        rows = []
        for k, d in enumerate(days[:-45]):
            n = int(rng.integers(1, 7))
            for j in range(n):
                filled = bool(rng.random() < 0.6)
                r = float(rng.normal(mood[(d.year, d.month)] + (premium if n >= 4 else 0.0), 1.5))
                rows.append({"symbol": f"S{k}_{j}", "signal_date": d, "status": "CLOSED" if filled else "UNFILLED",
                             "fill_date": days[k + 1] if filled else pd.NaT, "exit_date": days[k + 1 + int(rng.integers(3, 40))] if filled else pd.NaT,
                             "R": r if filled else float("nan"), "net": 0.04 * r if filled else float("nan"), "risk_pct": 0.04,
                             "regime": str(rng.choice(["healthy", "mixed", "weak"])), "setup": "primed", "quality_pct": float(rng.uniform(50, 100)),
                             "sma200_chg": float(rng.uniform(-0.1, 0.1)), "breadth50": float(rng.uniform(0.1, 0.9))})
        return pd.DataFrame(rows)

    def test_the_book_keeps_each_signals_regime_and_breadth(self):
        book = self.book([self.filled("A", "2026-01-05", 1.0, regime="weak", breadth=0.31), {"symbol": "B", "signal": "2026-01-06", "regime": "mixed"}])
        self.assertEqual(list(book.regime), ["weak", "mixed"])
        self.assertAlmostEqual(float(book.breadth[0]), 0.31)
        self.assertTrue(np.isnan(book.breadth[1]))                                             # no number given for this one
        self.assertTrue(np.isnan(self.book([self.filled("A", "2026-01-05", 1.0)]).breadth).all())     # a file without the column at all

    def test_a_signal_can_be_taken_at_half_the_risk_or_left_out_of_the_account(self):
        from backtest import portfolio_study as ps
        book = self.jan5()                    # A +2R, B -1R, C never fills (the 5th, the scanner's order A, C, B), D +1R on the 8th
        plain = ps.simulate(book, "shipped", max_stocks=3, risk=0.01)
        self.assertEqual(plain["skipped"], 0)
        self.assertAlmostEqual(plain["realised"], 101.0)                                       # three rooms take A, C and B: +2.0 -1.0
        # A at half the risk: +1.0 instead of +2.0; B at half: -0.5 instead of -1.0
        self.assertAlmostEqual(ps.simulate(book, "shipped", max_stocks=3, risk=0.01, mult=np.array([0.5, 1.0, 1.0, 1.0]))["realised"], 100.0)
        self.assertAlmostEqual(ps.simulate(book, "shipped", max_stocks=3, risk=0.01, mult=np.array([1.0, 0.5, 1.0, 1.0]))["realised"], 101.5)
        # four rooms also take D (+1.0 on the account of 100 it saw on the 8th), or +0.5 at half the risk
        self.assertAlmostEqual(ps.simulate(book, "shipped", max_stocks=4, risk=0.01)["realised"], 102.0)
        self.assertAlmostEqual(ps.simulate(book, "shipped", max_stocks=4, risk=0.01, mult=np.array([1.0, 1.0, 1.0, 0.5]))["realised"], 101.5)
        # a signal left out never reaches the account: without C the two rooms go to A and B (+2.0 -1.0) and D finds both still taken
        no_c = ps.simulate(book, "shipped", max_stocks=2, risk=0.01, skip=np.array([False, False, True, False]))
        self.assertEqual((no_c["placed"], no_c["closed"], no_c["unfilled"], no_c["full"], no_c["skipped"], no_c["capped"]), (2, 2, 0, 1, 1, 0))
        self.assertAlmostEqual(no_c["realised"], 101.0)
        # the signals left out go first and the cap counts what is left: without A a cap of 1 lists C (quality 95) and drops B; D then finds a room
        both = ps.simulate(book, "shipped", max_stocks=3, risk=0.01, cap=1, skip=np.array([True, False, False, False]))
        self.assertEqual((both["placed"], both["closed"], both["unfilled"], both["skipped"], both["capped"]), (2, 1, 1, 1, 1))
        self.assertAlmostEqual(both["realised"], 101.0)                                        # D's +1.0 only
        # the half stacks with the weak market's half: 1% x 0.5 x 0.5 = 0.25 at risk, +2R = +0.5
        weak = self.book([self.filled("W", "2026-03-02", 2.0, regime="weak")])
        self.assertAlmostEqual(ps.simulate(weak, "shipped", max_stocks=1, risk=0.01, mult=np.array([0.5]))["realised"], 100.5)
        for bad in ({"mult": np.ones(3)}, {"skip": np.zeros(5, dtype=bool)}):                 # one entry per signal of the book, or nothing
            with self.assertRaises(ValueError):
                ps.simulate(book, "shipped", max_stocks=3, risk=0.01, **bad)

    def test_the_size_of_a_day_counts_every_signal_the_file_has_for_it_and_the_three_kinds_of_day_are_tabled(self):
        from backtest import busy_day_study as bd
        from backtest import portfolio_study as ps
        df = self.frame(self.sized_rows())
        book = ps.Book(df)
        self.assertEqual(book.n, 9)                                                            # the open one is not a finished signal
        size = bd.day_sizes(df, book)
        np.testing.assert_array_equal(size, [1, 2, 2, 3, 3, 3, 4, 4, 4])                       # ... but it counts for the size of the 8th
        quiet, busy = bd.groups(size)
        np.testing.assert_array_equal(quiet, [True] * 3 + [False] * 6)                         # 1 or 2 signals
        np.testing.assert_array_equal(busy, [False] * 6 + [True] * 3)                          # 4 or more; the day of 3 is neither
        q3, b3 = bd.groups(size, 3, 4)
        self.assertEqual((int(q3.sum()), int(b3.sum())), (6, 3))
        ids, n = bd.month_blocks(book.sig)
        self.assertEqual((n, list(ids)), (1, [0] * 9))
        rows = {g["label"].split(" ")[0]: g for g in bd.kinds(book, size, ids, n, bd.resamples(n, 10))}
        self.assertEqual(list(rows), ["quiet", "typical", "busy"])
        self.assertEqual([g["label"] for g in rows.values()], ["quiet (1-2)", "typical (3)", "busy (4+)"])
        q, t, b = rows["quiet"], rows["typical"], rows["busy"]
        self.assertEqual((q["days"], q["signals"], q["filled"]), (2, 3, 2))
        self.assertAlmostEqual(q["share"], 2 / 3)
        self.assertAlmostEqual(q["mean_R"], 0.0)                                               # +1 and -1
        self.assertAlmostEqual(q["net"], 0.0)
        np.testing.assert_allclose(q["mix"], [2 / 3, 0.0, 1 / 3])                              # S2 was made in a weak market
        self.assertAlmostEqual(q["breadth"], 0.5)                                              # (0.8 + 0.3 + 0.4) / 3
        self.assertAlmostEqual(q["stop"], 0.05)
        self.assertEqual((t["days"], t["signals"], t["filled"], t["share"]), (1, 3, 3, 1.0))
        self.assertAlmostEqual(t["mean_R"], 1.0)                                               # +2, 0, +1
        self.assertTrue(np.isnan(t["breadth"]))                                                # no breadth on that day: not a number, not a crash
        self.assertEqual((b["days"], b["signals"], b["filled"]), (1, 3, 2))
        self.assertAlmostEqual(b["mean_R"], 2.0)                                               # +3 and +1 (S8 never filled, S10 is not finished)

    def test_busy_minus_quiet_is_taken_over_all_trades_and_inside_each_month(self):
        from backtest import busy_day_study as bd
        book = self.two_months()
        busy = np.array([0, 1, 1, 1, 1, 1, 0, 0], dtype=bool)
        quiet = ~busy
        ids, n = bd.month_blocks(book.sig)
        self.assertEqual((n, list(ids)), (2, [0, 0, 0, 1, 1, 1, 1, 1]))
        cells = bd.Cells(book, busy, quiet, ids, n)
        ones = np.ones((1, 2))
        # busy: January +2R and 0R, February three times +1R = 5R in 5 trades; quiet: January +1R, February -1R and 0R = 0R in 3 trades
        self.assertAlmostEqual(cells.mean_a(ones)[0], 1.0)
        self.assertAlmostEqual(cells.mean_b(ones)[0], 0.0)
        self.assertAlmostEqual(cells.pooled(ones)[0], 1.0)
        # inside each month: January 1.0 - 1.0 = 0 (weight 2 x 1 / 3), February 1.0 - (-0.5) = 1.5 (weight 3 x 2 / 5)
        self.assertAlmostEqual(cells.within(ones, min_cell=1)[0], (1.2 * 1.5) / (2 / 3 + 1.2))
        np.testing.assert_array_equal(cells.eligible(2), [False, True])                         # January has one quiet trade only
        self.assertAlmostEqual(cells.within(ones, min_cell=2)[0], 1.5)                          # so only February is used
        self.assertEqual(int(cells.eligible().sum()), 0)                                       # the rule asks for 3 of each kind in a month: none has
        self.assertTrue(np.isnan(cells.within(ones)[0]))                                       # nothing to average
        # a resample that draws February twice and January never; and one that draws January twice
        self.assertAlmostEqual(cells.pooled(np.array([[0.0, 2.0]]))[0], 1.0 - (-0.5))
        self.assertAlmostEqual(cells.within(np.array([[0.0, 2.0]]), min_cell=1)[0], 1.5)
        self.assertAlmostEqual(cells.pooled(np.array([[2.0, 0.0]]))[0], 0.0)                   # January alone: busy 1.0, quiet 1.0

    def test_the_range_comes_from_drawing_whole_months_and_the_edge_must_clear_both_bars(self):
        from backtest import busy_day_study as bd
        c = bd.resamples(4, 20, seed=1)
        self.assertEqual(c.shape, (20, 4))
        np.testing.assert_array_equal(c.sum(axis=1), np.full(20, 4.0))                         # each resample draws as many months as there are
        np.testing.assert_array_equal(c, bd.resamples(4, 20, seed=1))                          # the same seed, the same draw
        self.assertFalse(np.array_equal(c, bd.resamples(4, 20, seed=2)))
        sure = bd.estimate(lambda m: np.full(len(m), 0.3), bd.resamples(4, 50))
        self.assertAlmostEqual(sure["point"], 0.3)
        self.assertAlmostEqual(sure["se"], 0.0)
        self.assertAlmostEqual(sure["lo"], 0.3)
        self.assertAlmostEqual(sure["hi"], 0.3)
        self.assertTrue(bd.edge_ok(sure))
        # five months at +2 and three at -1: +0.875 on average, but a resample of eight months has a 3.6% chance of averaging 0 or less - above the 1% allowed
        d = np.array([2.0, 2.0, 2.0, 2.0, 2.0, -1.0, -1.0, -1.0])
        mixed = bd.estimate(lambda m: (m @ d) / m.sum(axis=1), bd.resamples(8, 5000))
        self.assertAlmostEqual(mixed["point"], 0.875)
        self.assertLess(mixed["lo"], 0.0)
        self.assertFalse(bd.edge_ok(mixed))
        self.assertIsNone(bd.estimate(lambda m: np.full(len(m), np.nan), bd.resamples(4, 50)))      # nothing to calculate
        self.assertIsNone(bd.estimate(lambda m: np.where(np.arange(len(m)) == 0, np.nan, 1.0), bd.resamples(4, 50)))   # no number for the data as it is
        # both bars: at least +0.10R, and the lower end of the range above 0
        self.assertTrue(bd.edge_ok({"point": 0.24, "lo": 0.05}))
        self.assertTrue(bd.edge_ok({"point": 0.10, "lo": 0.0001}))
        self.assertFalse(bd.edge_ok({"point": 0.09, "lo": 0.05}))                              # too small to matter
        self.assertFalse(bd.edge_ok({"point": 0.30, "lo": -0.01}))                             # luck cannot be ruled out
        self.assertFalse(bd.edge_ok({"point": 0.30, "lo": 0.0}))
        self.assertFalse(bd.edge_ok(None))

    def test_every_part_of_the_history_has_to_agree_and_the_regimes_and_thirds_are_split_out(self):
        from backtest import busy_day_study as bd
        from backtest import portfolio_study as ps
        f = self.filled
        book = self.book([f("P1", "2025-12-15", 0.0), f("P2", "2025-12-16", 1.0), f("P3", "2026-01-12", 1.0), f("P4", "2026-01-13", 0.0)])
        quiet, busy = np.array([1, 0, 1, 0], dtype=bool), np.array([0, 1, 0, 1], dtype=bool)
        rows = bd.period_rows(book, quiet, busy, periods=((2025, 2025), (2026, 2026), (2019, 2019)), min_cell=1)
        self.assertEqual([r["label"] for r in rows], ["2025-25", "2026-26", "2019-19"])
        self.assertAlmostEqual(rows[0]["diff"], 1.0)                                           # busy +1R, quiet 0R
        self.assertTrue(rows[0]["ok"])
        self.assertAlmostEqual(rows[1]["diff"], -1.0)                                          # the other way round the year after
        self.assertFalse(rows[1]["ok"])
        self.assertEqual((rows[2]["n_quiet"], rows[2]["n_busy"]), (0, 0))                      # nobody traded then: nothing to judge
        self.assertTrue(np.isnan(rows[2]["diff"]))
        self.assertFalse(rows[2]["ok"])
        self.assertFalse(bd.period_rows(book, quiet, busy, periods=((2025, 2025),), min_cell=2)[0]["ok"])   # one trade of each kind is too few to judge
        # by regime: S1 +1R (quiet) against S7 +3R and S9 +1R (busy) in a healthy market; S2 -1R (quiet) against nobody in a weak one
        book = ps.Book(self.frame(self.sized_rows()))
        q9, b9 = np.array([True] * 3 + [False] * 6), np.array([False] * 6 + [True] * 3)
        by_regime = {r["label"]: r for r in bd.split_rows(book, book.regime, ("healthy", "weak"), q9, b9)}
        h = by_regime["healthy"]
        self.assertEqual((h["n_quiet"], h["n_busy"]), (1, 2))
        self.assertAlmostEqual(h["mean_quiet"], 1.0)
        self.assertAlmostEqual(h["mean_busy"], 2.0)
        self.assertAlmostEqual(h["diff"], 1.0)
        self.assertEqual((by_regime["weak"]["n_quiet"], by_regime["weak"]["n_busy"]), (1, 0))
        self.assertTrue(np.isnan(by_regime["weak"]["diff"]))
        # thirds of breadth: 30 filled trades with breadth 0.01 ... 0.30 split into 10, 10 and 10
        days = pd.bdate_range("2026-01-05", periods=30)
        book = self.book([f(f"T{i:02d}", d.strftime("%Y-%m-%d"), 1.0, breadth=i / 100) for i, d in enumerate(days, start=1)])
        lab, names, edges = bd.breadth_thirds(book)
        self.assertEqual([int((lab == n).sum()) for n in names], [10, 10, 10])
        self.assertAlmostEqual(edges[0], 0.10 + 0.01 * 2 / 3)
        self.assertAlmostEqual(edges[1], 0.20 + 0.01 * 1 / 3)
        self.assertEqual(lab[0], "lowest third")
        self.assertEqual(lab[-1], "highest third")
        self.assertIsNone(bd.breadth_thirds(self.two_months()))                                # no breadth in the file, or too few trades to cut thirds

    def test_the_account_check_runs_the_quiet_days_at_half_the_risk_and_without_them(self):
        from backtest import busy_day_study as bd
        book = self.jan5()                                                                     # D is alone on the 8th: the only quiet day
        quiet = np.array([False, False, False, True])
        rows = bd.account_test(book, quiet, seeds=2)
        self.assertEqual([(a["risk"], a["stocks"]) for a in rows], [(0.01, 8), (0.005, 12)])
        one, two = rows
        # 1% of 100 at risk, 5% stops: A +2.0, B -1.0 and D +1.0; at half the risk D adds 0.5; left out it adds nothing
        self.assertAlmostEqual(one["runs"]["base"]["realised"], 102.0)
        self.assertAlmostEqual(one["runs"]["half"]["realised"], 101.5)
        self.assertAlmostEqual(one["runs"]["out"]["realised"], 101.0)
        self.assertEqual(one["runs"]["out"]["skipped"], 1)
        # 0.5% at risk: half of each
        self.assertAlmostEqual(two["runs"]["base"]["realised"], 101.0)
        self.assertAlmostEqual(two["runs"]["half"]["realised"], 100.75)
        self.assertAlmostEqual(two["runs"]["out"]["realised"], 100.5)
        for a in rows:
            self.assertGreaterEqual(a["luck"], 0.005)
            self.assertEqual(len(a["rand"]), 2)                                                # the two random draws of one signal at half the risk
            self.assertIsInstance(a["ok"], bool)
            self.assertIsNotNone(a["harm"])
            self.assertAlmostEqual(a["change"]["d_growth"], a["m"]["half"]["cagr"] - a["m"]["base"]["cagr"])

    def test_halving_the_quiet_days_must_not_cost_clearly_more_than_halving_arbitrary_signals(self):
        from backtest import busy_day_study as bd
        # 101 random-halving accounts with growth 0.00 ... 1.00 and worst drawdown -0.50 ... 0.00: the 10th percentile is 0.10 and -0.45
        rand = [{"cagr": g, "max_dd": -0.50 + 0.5 * g} for g in np.linspace(0.0, 1.0, 101)]
        fine = bd.no_harm({"cagr": 0.11, "max_dd": -0.44}, rand)
        self.assertAlmostEqual(fine["floor_growth"], 0.10)
        self.assertAlmostEqual(fine["floor_dd"], -0.45)
        self.assertTrue(fine["ok"])
        self.assertFalse(bd.no_harm({"cagr": 0.09, "max_dd": -0.44}, rand)["ok_growth"])      # earned less than 9 in 10 arbitrary halvings
        self.assertFalse(bd.no_harm({"cagr": 0.11, "max_dd": -0.46}, rand)["ok_dd"])          # drew down deeper than 9 in 10 of them
        self.assertTrue(bd.no_harm({"cagr": 0.10, "max_dd": -0.45}, rand)["ok"])              # exactly on the floor is still no worse
        self.assertIsNone(bd.no_harm({}, rand))                                                # no account to judge
        self.assertIsNone(bd.no_harm({"cagr": 0.11, "max_dd": -0.44}, rand[:1]))               # nothing to compare it with

    def test_the_busy_day_rule_needs_every_part_and_says_what_changes(self):
        from backtest import busy_day_study as bd
        edge = {"point": 0.24, "lo": 0.05}
        weak_edge = {"point": 0.04, "lo": -0.05}
        yes = [{"ok": True}] * 3
        fine = [{"ok": True}, {"ok": True}]
        v = bd.verdict(edge, edge, yes, fine)
        self.assertEqual((v["supported"], v["adopt"]), (True, True))
        self.assertTrue(bd.result_text(v).startswith("RESULT: every rule is met"))
        harm = bd.verdict(edge, edge, yes, [{"ok": True}, {"ok": False}])                      # the pattern holds but an account came to harm
        self.assertEqual((harm["supported"], harm["adopt"]), (True, False))
        self.assertIn("rule 4 is not", bd.result_text(harm))
        self.assertIn("nothing changes", bd.result_text(harm))
        mood = bd.verdict(edge, weak_edge, yes, fine)                                          # the raw gap is there, the same-month gap is not: the market's mood
        self.assertEqual((mood["rule1"], mood["rule2"], mood["supported"], mood["adopt"]), (True, False, False, False))
        self.assertIn("(rule 2 not met)", bd.result_text(mood))
        self.assertIn("market's mood", bd.result_text(mood))                                   # and it says why: the month explains the gap, not the day's count
        part = bd.verdict(edge, edge, [{"ok": True}, {"ok": False}, {"ok": True}], fine)      # one part of the history disagrees
        self.assertEqual((part["rule3"], part["adopt"]), (False, False))
        self.assertIn("(rule 3 not met)", bd.result_text(part))
        self.assertIn("not in every part of the history", bd.result_text(part))
        none = bd.verdict(None, None, [], [])
        self.assertEqual((none["supported"], none["adopt"]), (False, False))
        self.assertIn("(rules 1, 2 and 3 not met)", bd.result_text(none))
        self.assertIn("too small, or too uncertain", bd.result_text(none))
        self.assertIn("(rules 1 and 2 not met)", bd.result_text(bd.verdict(weak_edge, weak_edge, yes, fine)))
        for v_ in (v, harm, mood, part, none):                                                 # one line each, starting the way the evidence file expects
            self.assertTrue(bd.result_text(v_).startswith("RESULT: "))
            self.assertNotIn("\n", bd.result_text(v_))

    def test_the_machinery_finds_a_busy_day_premium_that_is_there_and_not_one_that_is_not(self):
        from backtest import busy_day_study as bd
        from backtest import portfolio_study as ps

        def edges(df):
            book = ps.Book(df)
            quiet, busy = bd.groups(bd.day_sizes(df, book))
            ids, n = bd.month_blocks(book.sig)
            cells = bd.Cells(book, busy, quiet, ids, n)
            c = bd.resamples(n, 2000)
            return bd.estimate(cells.pooled, c), bd.estimate(cells.within, c)
        raw, same_month = edges(self.day_frame(0.5))                                           # busy days 0.5R better, month moods of 0.3R
        self.assertTrue(bd.edge_ok(raw), raw)
        self.assertTrue(bd.edge_ok(same_month), same_month)
        self.assertGreater(same_month["point"], 0.3)
        raw0, same_month0 = edges(self.day_frame(0.0))                                         # no premium at all
        self.assertFalse(bd.edge_ok(raw0), raw0)
        self.assertFalse(bd.edge_ok(same_month0), same_month0)
        self.assertLess(abs(same_month0["point"]), 0.2)

    def test_the_busy_day_study_runs_end_to_end_and_ends_with_its_verdict(self):
        from backtest import busy_day_study as bd
        df = self.day_frame(0.5)
        lines = bd.analyse(df, seeds=2, boot=300)
        text = "\n".join(lines)
        for section in ("== 1.", "== 2.", "== 3.", "== 4.", "== 5.", "== 6.", "== 7.", "== 8.", "== 9."):
            self.assertIn(section, text)
        for kind in ("quiet (1-2)", "typical (3)", "busy (4+)"):
            self.assertIn(kind, text)
        self.assertEqual(sum(1 for line in lines if line.startswith("RESULT:")), 1)
        # the file only covers 2023-25, so two of the three parts of the history have nobody to judge: rule 3 cannot be met, whatever rules 1 and 2 say
        self.assertIn("rule 3", [line for line in lines if line.startswith("RESULT:")][0])
        self.assertIn("   <- the rule", text)                                                  # the cut-off table marks the one the rule uses
        self.assertEqual(bd.analyse(df.iloc[0:0]), ["No finished signals in the file."])


class CapitalPlan(unittest.TestCase):
    """capital_plan.py (the dashboard's calculator of how much of the list one account can hold) on a hand-worked list. Capital 1,000,000 and risk 1% =
    10,000 at risk in a full-size trade; shares = money at risk / (buy price - first stop)."""

    @staticmethod
    def ledger(extra=()):
        base = {c: "" for c in outcomes.COLUMNS}
        rows = [
            # bought at 100; first stop 2.5 x 2% = 5% below it: 10,000 / 5 = 2,000 shares = 200,000 (20% of the capital)
            {**base, "trade_id": "S1", "symbol": "S1", "scan_mode": "swing", "status": "OPEN", "setup": "primed", "regime": "healthy", "fill_px": 100.0,
             "atr_pct": 0.02, "stop_mult": 2.5, "strong_trend": 1, "quality_pct": 70},
            # waiting at 200; stop 2.5 x 4% = 10% below it: 10,000 / 20 = 500 shares = 100,000 (10%)
            {**base, "trade_id": "S2", "symbol": "S2", "scan_mode": "swing", "status": "PENDING", "setup": "fresh", "regime": "healthy", "trigger": 200.0,
             "atr_pct": 0.04, "stop_mult": 2.5, "strong_trend": 0, "quality_pct": 90},
            # a BTST pick made in a weak market: half the risk, 5,000 / 2.5 = 2,000 shares at 50 = 100,000 (10%); no 200-day-trend mark (logged before 31 Oct)
            {**base, "trade_id": "S3", "symbol": "S3", "scan_mode": "btst", "status": "PENDING", "setup": "primed", "regime": "weak", "trigger": 50.0,
             "atr_pct": 0.02, "stop_mult": 2.5, "quality_pct": 80},
            # sold: not part of the list
            {**base, "trade_id": "S4", "symbol": "S4", "scan_mode": "swing", "status": "CLOSED", "setup": "primed", "regime": "healthy", "fill_px": 10.0,
             "atr_pct": 0.02, "stop_mult": 2.5},
        ] + list(extra)
        return pd.DataFrame(rows, columns=outcomes.COLUMNS)

    def row(self, symbol, status="OPEN", price=100.0, atr=0.02, setup="primed", q=70, strong=0, regime="healthy"):
        base = {c: "" for c in outcomes.COLUMNS}
        return {**base, "trade_id": symbol, "symbol": symbol, "scan_mode": "swing", "status": status, "setup": setup, "regime": regime,
                "fill_px" if status == "OPEN" else "trigger": price, "atr_pct": atr, "stop_mult": 2.5, "strong_trend": strong, "quality_pct": q}

    def test_each_stock_is_sized_by_the_messages_own_rule_and_listed_in_the_scanners_order(self):
        import capital_plan as cp
        s = cp.sized_list(self.ledger(), 1_000_000, 1.0)
        self.assertEqual(list(s["symbol"]), ["S1", "S2", "S3"])                                   # strong uptrend, then fresh, then the primed one
        self.assertEqual([int(x) for x in s["shares"]], [2000, 500, 2000])
        np.testing.assert_allclose(s["money"], [200_000, 100_000, 100_000])
        np.testing.assert_allclose(s["share_of_capital"], [0.20, 0.10, 0.10])
        np.testing.assert_allclose(s["stop"], [95.0, 180.0, 47.5])
        np.testing.assert_allclose(s["risk_share"], [0.01, 0.01, 0.005])                          # S3: weak market on the pick day -> half
        self.assertEqual(list(s["status"]), ["OPEN", "PENDING", "PENDING"])
        self.assertEqual(cp.listed(self.ledger()), 3)                                             # the sold trade is not part of the list
        # the order: 💪 first, then fresh before primed, then quality, then the name
        order = cp.sized_list(pd.DataFrame([self.row("A", q=99), self.row("B", setup="fresh", q=50), self.row("C", q=60, strong=1),
                                            self.row("D", setup="fresh", q=50)], columns=outcomes.COLUMNS), 1_000_000, 1.0)
        self.assertEqual(list(order["symbol"]), ["C", "B", "D", "A"])

    def test_rooms_and_money_decide_which_stocks_to_take(self):
        import capital_plan as cp
        rows, tot = cp.plan(self.ledger(), 1_000_000, 1.0, 5)
        self.assertEqual(list(rows["verdict"]), [cp.TAKE, cp.TAKE, cp.TAKE])
        np.testing.assert_allclose(rows["running"], [0.2, 0.3, 0.4])
        self.assertAlmostEqual(tot["all_money"], 0.40)
        self.assertAlmostEqual(tot["all_risk"], 0.025)                                            # 1% + 1% + half of 1%
        self.assertEqual((tot["n"], tot["take"], tot["rooms"], tot["left_out"]), (3, 3, 3, 0))
        # two rooms: the third stock finds none, however small it is
        rows, tot = cp.plan(self.ledger(), 1_000_000, 1.0, 2)
        self.assertEqual(list(rows["verdict"]), [cp.TAKE, cp.TAKE, cp.NO_ROOM])
        self.assertEqual((tot["take"], tot["rooms"]), (2, 2))
        self.assertAlmostEqual(tot["money"], 0.30)
        self.assertEqual(cp.NO_ROOM, "Skip - every room is taken")
        # at 3% risk the three need 60% + 30% + 30%: the third no longer fits in the money although a room is free
        rows, tot = cp.plan(self.ledger(), 1_000_000, 3.0, 5)
        self.assertEqual(list(rows["verdict"]), [cp.TAKE, cp.TAKE, cp.NO_MONEY])
        self.assertAlmostEqual(tot["money"], 0.90)
        self.assertAlmostEqual(tot["all_money"], 1.20)                                            # the whole list would need more than the whole account
        self.assertEqual(cp.NO_MONEY, "Skip - the money does not fit")

    def test_stocks_you_hold_come_first_and_use_up_rooms_and_money(self):
        import capital_plan as cp
        rows, tot = cp.plan(self.ledger(), 1_000_000, 1.0, 2, held={"S3"})
        self.assertEqual(list(rows["symbol"]), ["S3", "S1", "S2"])                                # held first, the rest in the usual order
        self.assertEqual(list(rows["verdict"]), [cp.HELD, cp.TAKE, cp.NO_ROOM])
        np.testing.assert_allclose(rows["running"], [0.1, 0.3, 0.3])
        self.assertEqual((tot["take"], tot["rooms"]), (1, 2))
        self.assertAlmostEqual(tot["held_money"], 0.10)
        # holding more than the money allows is reported as the money held, for the page to warn about
        _, tot = cp.plan(self.ledger(), 1_000_000, 3.0, 5, held={"S1", "S2", "S3"})
        self.assertAlmostEqual(tot["held_money"], 1.20)
        # a name that is not on the list changes nothing
        rows, tot = cp.plan(self.ledger(), 1_000_000, 1.0, 5, held={"NOT_THERE"})
        self.assertEqual(list(rows["verdict"]), [cp.TAKE] * 3)
        self.assertEqual(tot["held_money"], 0.0)

    def test_nothing_is_ever_bought_with_money_the_account_does_not_have(self):
        import capital_plan as cp
        # a stop only 0.25% below the price would need 40,000 shares (4,000,000) for 10,000 at risk: the account can pay for 10,000 shares at most
        tight = cp.sized_list(pd.DataFrame([self.row("TIGHT", atr=0.001)], columns=outcomes.COLUMNS), 1_000_000, 1.0)
        self.assertEqual(int(tight["shares"].iloc[0]), 10_000)
        self.assertAlmostEqual(float(tight["share_of_capital"].iloc[0]), 1.0)

    def test_a_stock_that_cannot_be_sized_is_left_out_and_counted(self):
        import capital_plan as cp
        extra = [self.row("NOATR", atr=float("nan")),                                           # no average daily range recorded
                 self.row("HUGE", status="PENDING", price=1_000_000.0)]                          # one share risks 50,000: more than the 10,000 accepted
        ledger = self.ledger(extra)
        rows, tot = cp.plan(ledger, 1_000_000, 1.0, 10)
        self.assertEqual(list(rows["symbol"]), ["S1", "S2", "S3"])
        self.assertEqual((tot["n"], tot["left_out"], cp.listed(ledger)), (3, 2, 5))

    def test_the_load_of_the_whole_list_is_the_sum_of_the_stock_sizes(self):
        import capital_plan as cp
        self.assertAlmostEqual(cp.list_load(self.ledger(), 1.0), 0.40, places=6)               # 20% + 10% + 10%
        self.assertAlmostEqual(cp.list_load(self.ledger(), 3.0), 1.20, places=6)
        self.assertEqual(cp.list_load(None, 1.0), 0.0)
        self.assertEqual(cp.list_load(self.ledger().iloc[3:], 1.0), 0.0)                       # only a sold trade
        _, tot = cp.plan(self.ledger(), 1_000_000, 1.0, 10)
        self.assertAlmostEqual(cp.list_load(self.ledger(), 1.0), tot["all_money"], places=6)  # the page and the message give the same number

    def test_an_empty_list_gives_an_empty_plan(self):
        import capital_plan as cp
        sold_only = self.ledger().iloc[3:]
        for ledger in (None, pd.DataFrame(columns=outcomes.COLUMNS), sold_only, pd.DataFrame({"x": [1]})):
            rows, tot = cp.plan(ledger, 1_000_000, 1.0, 10)
            self.assertTrue(rows.empty)
            self.assertEqual((tot["n"], tot["take"], tot["money"], tot["left_out"]), (0, 0, 0.0, 0))
        rows, tot = cp.plan(self.ledger(), 0, 1.0, 10)                                            # no capital: nothing can be sized
        self.assertTrue(rows.empty)
        self.assertEqual(tot["left_out"], 3)


class SnapshotLog(unittest.TestCase):
    """data/snapshots.py: the research log of what the afternoon BTST scan saw and how those days ended. Nothing reads it back into a scan."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "snap.csv.gz")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def bars(days, closes, vols=None):
        """Daily bars on the given dates: open = close - 1, high = close + 1, low = close - 2."""
        idx = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("Asia/Kolkata")
        c = np.array(closes, float)
        v = np.array(vols if vols is not None else [1000.0] * len(days), float)
        return pd.DataFrame({"open": c - 1, "high": c + 1, "low": c - 2, "close": c, "volume": v}, index=idx)

    def test_one_row_per_stock_whose_newest_bar_is_todays_forming_bar(self):
        from data import snapshots as sn
        bars = {
            "AAA": self.bars(["2026-10-13", "2026-10-14"], [100.0, 103.0], [5000, 700]),            # today's bar = the quote the scan saw
            "BBB": self.bars(["2026-10-12", "2026-10-13"], [50.0, 51.0]),                           # newest bar is yesterday's: no quote came -> no snapshot
            "CCC": self.bars(["2026-10-14"], [20.0]),                                               # no previous close to compare with
            "DDD": self.bars(["2026-10-13", "2026-10-14"], [10.0, float("nan")]),                   # an unusable quote
        }
        picks = [types.SimpleNamespace(symbol="AAA", setup="fresh")]
        self.assertEqual(sn.record(bars, at(2026, 10, 14, 14, 46), 0.7651, picks, via="quotes", path=self.path), 1)
        df = sn.load(self.path)
        self.assertEqual(list(df["symbol"]), ["AAA"])
        row = df.iloc[0]
        self.assertEqual((row["date"], row["time_ist"], row["via"], row["setup"], int(row["pick"])), ("2026-10-14", "14:46", "quotes", "fresh", 1))
        self.assertEqual((row["open"], row["high"], row["low"], row["last"], row["volume"], row["prev_close"]), (102.0, 104.0, 101.0, 103.0, 700, 100.0))
        self.assertAlmostEqual(row["assumed_frac"], 0.7651)
        self.assertTrue(np.isnan(row["final_close"]) and np.isnan(row["next_close"]) and row["next_date"] == "")       # the day has not ended
        self.assertEqual(list(df.columns), list(sn.COLUMNS))
        self.assertEqual(sn.record({}, at(2026, 10, 14, 14, 46), 0.76, path=os.path.join(self.tmp, "none.csv.gz")), 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "none.csv.gz")))                                      # nothing to write: no empty file

    def test_a_stock_that_is_not_a_pick_is_logged_too_and_says_so(self):
        from data import snapshots as sn
        bars = {s: self.bars(["2026-10-13", "2026-10-14"], [100.0, 101.0]) for s in ("AAA", "BBB")}
        sn.record(bars, at(2026, 10, 14, 14, 46), 0.76, [types.SimpleNamespace(symbol="BBB", setup="primed")], path=self.path)
        df = sn.load(self.path).set_index("symbol")
        self.assertEqual((int(df.loc["AAA", "pick"]), df.loc["AAA", "setup"], int(df.loc["BBB", "pick"]), df.loc["BBB", "setup"]), (0, "", 1, "primed"))

    def test_another_minute_adds_rows_the_same_minute_replaces_them_and_old_days_roll_off(self):
        from data import snapshots as sn

        def run(day, close, hh, mm):
            sn.record({"AAA": self.bars([f"2026-10-{day - 1:02d}", f"2026-10-{day:02d}"], [100.0, close])}, at(2026, 10, day, hh, mm), 0.76, path=self.path)

        run(14, 103.0, 14, 46)
        run(14, 104.0, 14, 46)                                                                     # the same minute again: replaced
        self.assertEqual(list(sn.load(self.path)["last"]), [104.0])
        run(14, 105.0, 15, 2)                                                                      # another minute: added
        self.assertEqual(list(sn.load(self.path)["time_ist"]), ["14:46", "15:02"])
        with mock.patch.object(sn, "KEEP_SESSIONS", 2):
            for day in (15, 16, 19):
                run(day, 101.0, 14, 46)
        self.assertEqual(sorted(set(sn.load(self.path)["date"])), ["2026-10-16", "2026-10-19"])    # only the newest two sessions are kept

    def test_the_evening_run_fills_in_how_the_day_ended_and_the_next_evening_the_next_session(self):
        from data import snapshots as sn
        live = {"AAA": self.bars(["2026-10-13", "2026-10-14"], [100.0, 103.0], [5000, 700]),
                "BBB": self.bars(["2026-10-13", "2026-10-14"], [50.0, 49.0], [900, 90])}
        sn.record(live, at(2026, 10, 14, 14, 46), 0.75, path=self.path)
        # that evening: the finished bar of the 14th for AAA only (BBB was not loaded)
        evening = {"AAA": self.bars(["2026-10-13", "2026-10-14"], [100.0, 104.0], [5000, 1000])}
        self.assertEqual(sn.complete(evening, date(2026, 10, 14), path=self.path), 1)
        df = sn.load(self.path).set_index("symbol")
        self.assertEqual([df.loc["AAA", c] for c in sn.FINAL], [103.0, 105.0, 102.0, 104.0, 1000.0])
        self.assertTrue(np.isnan(df.loc["AAA", "next_close"]) and np.isnan(df.loc["BBB", "final_close"]))
        self.assertEqual(df.loc["AAA", "last"], 103.0)                                              # what the scan saw is never touched
        self.assertEqual(sn.complete(evening, date(2026, 10, 14), path=self.path), 0)               # nothing new to add
        # the next evening: the 15th completes AAA's next session; BBB is loaded now and gets both
        later = {"AAA": self.bars(["2026-10-14", "2026-10-15"], [999.0, 106.0], [1, 1200]),          # a different 14th: the filled values are not overwritten
                 "BBB": self.bars(["2026-10-13", "2026-10-14", "2026-10-15"], [50.0, 48.0, 47.0], [900, 400, 300])}
        self.assertEqual(sn.complete(later, date(2026, 10, 15), path=self.path), 2)
        df = sn.load(self.path).set_index("symbol")
        self.assertEqual((df.loc["AAA", "final_close"], df.loc["AAA", "final_volume"]), (104.0, 1000.0))
        self.assertEqual((df.loc["AAA", "next_date"], df.loc["AAA", "next_open"], df.loc["AAA", "next_high"], df.loc["AAA", "next_low"],
                          df.loc["AAA", "next_close"]), ("2026-10-15", 105.0, 107.0, 104.0, 106.0))
        self.assertEqual((df.loc["BBB", "final_close"], df.loc["BBB", "final_volume"], df.loc["BBB", "next_date"], df.loc["BBB", "next_close"]),
                         (48.0, 400.0, "2026-10-15", 47.0))

    def test_a_day_after_the_newest_completed_session_is_never_filled(self):
        from data import snapshots as sn
        sn.record({"AAA": self.bars(["2026-10-13", "2026-10-14"], [100.0, 103.0])}, at(2026, 10, 14, 14, 46), 0.75, path=self.path)
        bars = {"AAA": self.bars(["2026-10-13", "2026-10-14", "2026-10-15"], [100.0, 104.0, 106.0])}
        self.assertEqual(sn.complete(bars, date(2026, 10, 13), path=self.path), 0)                  # the 14th is not complete in a run whose newest session is the 13th
        self.assertEqual(sn.complete(bars, date(2026, 10, 14), path=self.path), 1)                  # the 14th is: its close is filled, the 15th (a bar that may be forming) is not
        df = sn.load(self.path)
        self.assertEqual(df.loc[0, "final_close"], 104.0)
        self.assertTrue(np.isnan(df.loc[0, "next_close"]))

    def test_a_missing_or_unreadable_file_is_an_empty_log_and_costs_nothing(self):
        from data import snapshots as sn
        self.assertTrue(sn.load(self.path).empty)
        self.assertEqual(sn.complete({"AAA": self.bars(["2026-10-14"], [100.0])}, date(2026, 10, 14), path=self.path), 0)
        with open(self.path, "wb") as fh:
            fh.write(b"this is not a gzip file")
        self.assertTrue(sn.load(self.path).empty)
        self.assertEqual(sn.complete({}, date(2026, 10, 14), path=self.path), 0)
        sn.record({"AAA": self.bars(["2026-10-13", "2026-10-14"], [100.0, 103.0])}, at(2026, 10, 14, 14, 46), 0.75, path=self.path)   # and it is written afresh
        self.assertEqual(list(sn.load(self.path)["symbol"]), ["AAA"])


class SnapshotStudy(unittest.TestCase):
    """backtest/snapshot_study.py on hand-worked logs."""

    @staticmethod
    def log(rows):
        from data import snapshots as sn
        base = {"date": "2026-10-14", "time_ist": "14:45", "symbol": "S", "open": 99.0, "high": 102.0, "low": 98.0, "last": 100.0, "volume": 700.0,
                "prev_close": 99.0, "assumed_frac": 0.75, "pick": 0, "setup": "", "via": "quotes", "final_open": np.nan, "final_high": np.nan,
                "final_low": np.nan, "final_close": np.nan, "final_volume": np.nan, "next_date": "", "next_open": np.nan, "next_high": np.nan,
                "next_low": np.nan, "next_close": np.nan}
        df = pd.DataFrame([{**base, **r} for r in rows])
        assert list(df.columns) == list(sn.COLUMNS)
        return df

    def volumes(self, assumed, shares=(0.60, 0.70, 0.80, 0.90), days=("2026-10-14", "2026-10-15", "2026-10-16")):
        """Four stocks on each of three days; the day's final volume is 1000 for every stock and the volume so far is share x 1000."""
        return self.log([{"date": d, "symbol": f"S{i}", "volume": 1000.0 * s, "final_volume": 1000.0, "assumed_frac": assumed}
                         for d in days for i, s in enumerate(shares)])

    def test_a_projection_that_is_right_is_called_right(self):
        from backtest import snapshot_study as ss
        text = "\n".join(ss.volume_section(self.volumes(0.75), min_rows=4, min_days=2))
        line = next(ln for ln in text.splitlines() if ln.startswith("14:45"))
        self.assertIn("   3      12", line)                                                           # three days, twelve stocks
        self.assertIn("75%", line)                                                                    # assumed 75% and the median real share 75%
        self.assertIn("1.00", line)                                                                   # projected / actual: median of 0.80, 0.93, 1.07, 1.20 over the days
        self.assertIn("within 10%", text)
        self.assertNotIn("PROJECTS", text.upper().replace("UNDER-PROJECTS", "").replace("OVER-PROJECTS", ""))

    def test_a_projection_that_is_off_says_which_way_and_by_how_much(self):
        from backtest import snapshot_study as ss
        under = "\n".join(ss.volume_section(self.volumes(0.85), min_rows=4, min_days=2))             # assumes 85%, the median stock had 75%: projected / actual 0.88
        self.assertIn("UNDER-projects", under)
        self.assertIn("0.88", under)
        self.assertIn("assumes 85%", under)
        self.assertIn("the median stock had 75%", under)
        over = "\n".join(ss.volume_section(self.volumes(0.65), min_rows=4, min_days=2))              # assumes 65%: projected / actual 1.15
        self.assertIn("OVER-projects", over)
        self.assertIn("1.15", over)

    def test_few_days_or_few_stocks_are_never_judged(self):
        from backtest import snapshot_study as ss
        text = "\n".join(ss.volume_section(self.volumes(0.85, days=("2026-10-14", "2026-10-15")), min_rows=4, min_days=5))
        self.assertIn("too few to judge the profile", text)
        self.assertNotIn("UNDER-projects", text)
        text = "\n".join(ss.volume_section(self.volumes(0.85), min_rows=40, min_days=2))
        self.assertIn("too few to judge the profile", text)

    def test_volume_that_exceeds_the_final_volume_is_flagged_as_a_data_problem(self):
        from backtest import snapshot_study as ss
        text = "\n".join(ss.volume_section(self.volumes(0.75, shares=(1.3, 1.4, 1.5, 0.7)), min_rows=4, min_days=2))
        self.assertIn("CHECK THE DATA", text)
        self.assertIn("75%", text)                                                                    # 3 of the 4 stocks (75%) have more volume at the scan than at the end of the day

    def test_nothing_finished_yet_says_so_in_each_section(self):
        from backtest import snapshot_study as ss
        df = self.log([{"symbol": "A"}, {"symbol": "B"}])
        self.assertIn("No finished day yet", "\n".join(ss.volume_section(df)))
        self.assertIn("No finished day yet", "\n".join(ss.move_section(df)))
        self.assertIn("empty", ss.analyse(pd.DataFrame())[0])

    def test_the_moves_from_the_snapshot_price_and_the_costs(self):
        from backtest import snapshot_study as ss
        picks = [{"symbol": f"P{i}", "pick": 1, "last": 100.0, "final_close": 101.0, "next_open": 102.35, "next_close": 99.0, "next_date": "2026-10-15"} for i in range(4)]
        rest = [{"symbol": f"R{i}", "pick": 0, "last": 100.0, "final_close": 99.0, "next_open": 100.0, "next_close": 101.0, "next_date": "2026-10-15"} for i in range(4)]
        df = self.log(picks + rest)
        moves, dropped = ss._moves(df)
        self.assertEqual(dropped, 0)
        np.testing.assert_allclose(moves["to_close"][:4], 0.01)
        np.testing.assert_allclose(moves["to_next_open"][:4], 0.0235)
        np.testing.assert_allclose(moves["to_next_close"][4:], 0.01)
        lines = ss.move_section(df, min_rows=4, min_days=5)
        row = next(ln for ln in lines if ln.startswith("the picks") and "next open" in ln)
        self.assertIn("+2.0%", row)                                                                   # +2.35% before costs, +2.00% after the 0.35%
        self.assertIn("100%", row)                                                                    # every pick was up
        row = next(ln for ln in lines if ln.startswith("the rest of the watchlist") and "the close" in ln)
        self.assertIn("-1.0%", row)
        self.assertTrue(next(ln for ln in lines if "the close" in ln and ln.startswith("the picks")).rstrip().endswith("-"))      # the close has no cost column
        self.assertTrue(any("Only 1 day(s) have a next session" in ln for ln in lines))
        # a group under min_rows is not summarised
        few = ss.move_section(self.log(picks[:2] + rest), min_rows=4)
        self.assertTrue(any(ln.startswith("the picks") and "too few" in ln for ln in few))

    def test_a_move_no_real_stock_makes_is_dropped_and_counted(self):
        from backtest import snapshot_study as ss
        rows = [{"symbol": f"S{i}", "last": 100.0, "final_close": 101.0, "next_open": 100.0, "next_close": 101.0, "next_date": "2026-10-15"} for i in range(4)]
        rows.append({"symbol": "SPLIT", "last": 1000.0, "final_close": 1005.0, "next_open": 500.0, "next_close": 505.0, "next_date": "2026-10-15"})     # a 1:2 split overnight
        moves, dropped = ss._moves(self.log(rows))
        self.assertEqual(dropped, 2)                                                                  # the -50% open and the -49.5% close
        self.assertTrue(np.isnan(moves["to_next_open"].iloc[-1]))
        self.assertAlmostEqual(float(moves["to_close"].iloc[-1]), 0.005)                              # its same-day move is fine and stays
        self.assertTrue(any("dropped as a split" in ln for ln in ss.move_section(self.log(rows), min_rows=4)))

    def test_the_script_runs_and_writes_its_report(self):
        from backtest import snapshot_study as ss
        from data import snapshots as sn
        with tempfile.TemporaryDirectory() as tmp:
            path, out = os.path.join(tmp, "snap.csv.gz"), os.path.join(tmp, "study.md")
            sn._save(self.volumes(0.75), path)
            with mock.patch("sys.stdout", new=types.SimpleNamespace(write=lambda s: len(s), flush=lambda: None)):
                self.assertEqual(ss.main([path, "--write", out]), 0)
            text = open(out, encoding="utf-8").read()
            self.assertIn("# BTST snapshot study", text)
            self.assertIn("== 1. How much of the day's volume had traded", text)
            self.assertIn("== 3. Read this before relying on any of it ==", text)
            with mock.patch("sys.stdout", new=types.SimpleNamespace(write=lambda s: len(s), flush=lambda: None)):
                self.assertEqual(ss.main([os.path.join(tmp, "missing.csv.gz")]), 0)                  # no log yet: a plain message, not a crash


class Session(unittest.TestCase):
    def test_states_and_volume_fraction(self):
        self.assertEqual(session.session_state(at(2026, 10, 13, 8, 0)), "pre_open")
        self.assertEqual(session.session_state(at(2026, 10, 13, 14, 0)), "open")
        self.assertEqual(session.session_state(at(2026, 10, 13, 16, 0)), "closed")
        self.assertEqual(session.session_state(at(2026, 10, 17, 11, 0)), "closed")          # Saturday
        self.assertAlmostEqual(session.volume_fraction(at(2026, 10, 13, 14, 0)), 0.64, places=2)
        self.assertAlmostEqual(session.volume_fraction(at(2026, 10, 13, 14, 45)), 0.765, places=3)       # the BTST run: halfway from 0.72 (2:30) to 0.81 (3:00)
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
        runguard.record_run("btst", at(2026, 10, 13, 14, 45), "manual", 3, path=self.path)
        self.assertFalse(runguard.should_skip("btst", "manual", at(2026, 10, 13, 15, 30), self.path)[0])

    def test_late_scheduled_btst_is_skipped_but_on_time_one_runs(self):
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 16, 40), self.path)[0])     # after the close
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 15, 10), self.path)[0])     # market open, but past the 3:00 PM cut-off
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 15, 1), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 15, 0), self.path)[0])     # exactly at the cut-off still runs
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 13, 14, 35), self.path)[0])    # GitHub's 2:30 PM backup, a little late
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 13, 15, 25), self.path)[0])       # the timer has no cut-off of its own

    def test_duplicates_are_skipped_in_both_directions(self):
        runguard.record_run("btst", at(2026, 10, 13, 14, 32), "schedule", 4, path=self.path, live=True)
        self.assertTrue(runguard.should_skip("btst", "timer", at(2026, 10, 13, 14, 45), self.path)[0])
        runguard.record_run("swing", at(2026, 10, 13, 16, 5), "timer", 4, path=self.path)
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 13, 22, 30), self.path)[0])
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 1, 10), self.path)[0])   # after midnight, same session
        self.assertFalse(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 16, 5), self.path)[0])  # next session

    def test_a_morning_or_trial_run_never_uses_up_the_afternoon_slot(self):
        # 8:42 AM timer test: market closed, so it is the last-close view and not live
        runguard.record_run("btst", at(2026, 10, 22, 8, 42), "timer", 0, path=self.path, live=False)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 45), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 22, 14, 30), self.path)[0])
        # an old marker written before the flag existed behaves the same
        with open(self.path, "w") as f:
            json.dump({"btst": {"at": "2026-10-22 08:42 IST", "key": "2026-10-22", "ok": True, "picks": 0, "source": "timer"}}, f)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 45), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 22, 14, 30), self.path)[0])
        # a live trial run at 2:14 PM does not block the 2:45 PM timer run (31 minutes later), nor does one at 2:20 ...
        for hh, mm in ((14, 14), (14, 20)):
            runguard.record_run("btst", at(2026, 10, 22, hh, mm), "timer", 2, path=self.path, live=True)
            self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 45), self.path)[0])
        # ... but a live run 10 minutes earlier does (the 2:30 PM GitHub fallback and the 2:45 PM timer arriving together)
        runguard.record_run("btst", at(2026, 10, 22, 14, 35), "schedule", 2, path=self.path, live=True)
        self.assertTrue(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 45), self.path)[0])
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 22, 14, 50), self.path)[0])
        # a starved run (the broker refused most stocks) never blocks a retry, however recent
        runguard.record_run("btst", at(2026, 10, 22, 14, 40), "timer", 0, ok=False, path=self.path, live=True)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 45), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 10, 22, 14, 50), self.path)[0])
        # and a manual run always goes ahead
        runguard.record_run("btst", at(2026, 10, 22, 14, 43), "timer", 2, path=self.path, live=True)
        self.assertFalse(runguard.should_skip("btst", "manual", at(2026, 10, 22, 14, 45), self.path)[0])

    def test_the_github_backup_never_repeats_a_live_scan_made_earlier_today(self):
        """While the timer is moved from 2:00 PM to 2:45 PM (or if it is ever set far from the backup's time) the 2:30 PM backup must not
        send a second message just because the first one is more than 20 minutes old."""
        runguard.record_run("btst", at(2026, 11, 3, 14, 0), "timer", 3, path=self.path, live=True)
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 11, 3, 14, 30), self.path)[0])      # 30 minutes later: still skipped
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 11, 3, 14, 58), self.path)[0])
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 11, 3, 14, 45), self.path)[0])        # a timer run keeps the 20-minute rule
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 11, 4, 14, 30), self.path)[0])     # and it is only "today"
        runguard.record_run("btst", at(2026, 11, 3, 14, 0), "timer", 0, ok=False, path=self.path, live=True)  # a starved run does not count
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 11, 3, 14, 30), self.path)[0])
        runguard.record_run("btst", at(2026, 11, 3, 9, 5), "timer", 0, path=self.path, live=False)           # nor does a morning (last-close) run
        self.assertFalse(runguard.should_skip("btst", "schedule", at(2026, 11, 3, 14, 30), self.path)[0])

    def test_failed_run_does_not_block_a_retry(self):
        runguard.record_run("swing", at(2026, 10, 13, 16, 5), "timer", 0, ok=False, path=self.path)
        self.assertFalse(runguard.should_skip("swing", "schedule", at(2026, 10, 13, 20, 0), self.path)[0])


class BtstSchedule(unittest.TestCase):
    """The afternoon BTST time lives in several places (config, GitHub's backup cron, the setup guide, the README); they must say the same thing."""

    @staticmethod
    def _minutes(hm):
        return hm[0] * 60 + hm[1]

    @staticmethod
    def _read(*parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
            return fh.read()

    def test_the_times_are_in_a_sensible_order(self):
        self.assertEqual(config.BTST_TIMER_IST, (14, 45))                       # 2 Nov 2026: moved from 2:00 PM at the user's request
        timer, cutoff, deadline = (self._minutes(t) for t in (config.BTST_TIMER_IST, config.BTST_LATE_CUTOFF_IST, config.BTST_FETCH_DEADLINE_IST))
        close = session.MARKET_CLOSE.hour * 60 + session.MARKET_CLOSE.minute
        self.assertLess(timer, cutoff)                                          # GitHub's backup may start a little after the timer's own time ...
        self.assertGreaterEqual(deadline, cutoff + 5)                           # ... and its fetch still gets a few minutes after the last start
        self.assertGreaterEqual(close - deadline, 20)                           # the message lands at least 20 minutes before the close

    def test_the_github_backup_cron_is_15_minutes_before_the_timer(self):
        import re
        m = re.search(r"cron:\s*'(\d+) (\d+) \* \* 1-5'", self._read(".github", "workflows", "btst-scan.yml"))
        self.assertIsNotNone(m, "no weekday cron line in btst-scan.yml")
        ist = (int(m.group(2)) * 60 + int(m.group(1)) + 5 * 60 + 30) % (24 * 60)    # the cron is in UTC
        self.assertEqual(ist, self._minutes(config.BTST_TIMER_IST) - 15)

    def test_the_docs_say_the_same_time(self):
        h, mi = config.BTST_TIMER_IST
        clock = f"{h - 12}:{mi:02d} PM"
        guide = self._read("docs", "TIMER_SETUP.md")
        self.assertIn(f"`BTST {clock} IST`", guide)
        self.assertIn(f"hour **{h}**, minute **{mi}**", guide)
        self.assertNotIn("`BTST 2:00 PM IST`", guide)
        self.assertIn(f"about {clock} IST", self._read("README.md"))
        self.assertIn(f"{clock} IST", self._read(".github", "workflows", "btst-scan.yml"))

    def test_the_fetch_deadline_follows_the_config(self):
        import main as M
        self.assertEqual(M._btst_deadline(at(2026, 11, 3, 14, 45), "timer").astimezone(IST).strftime("%H:%M"), "15:05")
        self.assertEqual(M._btst_deadline(at(2026, 11, 3, 14, 58), "schedule").astimezone(IST).strftime("%H:%M"), "15:05")
        late = at(2026, 11, 3, 15, 4)                                           # a hand-started run past the deadline still gets a fair chance
        self.assertGreater(M._btst_deadline(late, "manual"), late)


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
        self.assertTrue(p.why)                                                     # the few-words reasons the Telegram card shows
        for gone in ("qty", "position_value", "size_note"):                         # share counts depend on money no scan can know
            self.assertFalse(hasattr(p, gone), gone)
            self.assertNotIn(gone, p.to_row())
        order = [(0 if q.strong_trend else 1, 0 if q.setup == "fresh" else 1) for q in res.picks]
        self.assertEqual(order, sorted(order))                                     # strong uptrends first, then fresh before primed

    def test_live_fresh_is_buy_now_and_partial_volume_is_projected(self):
        live = {s: d.copy() for s, d in self.bars.items()}
        d = live["PLANT"]
        now = at(2026, 10, 13, 14, 45)                                              # the afternoon BTST run starts at 2:45 PM
        self.assertAlmostEqual(session.volume_fraction(now), 0.765, places=3)       # about 77% of the day's volume has traded by then
        d.iloc[-1, d.columns.get_loc("volume")] *= session.volume_fraction(now)
        res = pl.select_picks("btst", live, now=now)
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


class SpikeWarning(unittest.TestCase):
    """A stock that has ALREADY run up sharply is better bought in continuation than on a dip - the card says so (the 10-year replay: waiting for a dip did not pay)."""
    PHRASE = "buy in continuation, not on a dip"
    SU = {"ext_pct": -0.02, "above_pivot": False}
    MEAN, STD = 0.008, 0.046                                                   # the average stock's 5-day gain that day, and its spread
    STATS = {"mean": {"ret5": MEAN}, "std": {"ret5": STD}}
    # Gains placed clearly on either side of the shipped cut-offs (config.SPIKE_5D_Z / SPIKE_5D_MIN), so that these tests keep
    # passing when the cut-offs are re-tuned; the exact boundaries are checked separately in test_the_cut_offs.
    SPIKY = max(config.SPIKE_5D_MIN, MEAN + config.SPIKE_5D_Z * STD) + 0.03    # clearly a spike
    CALM = max(config.SPIKE_5D_MIN - 0.01, 0.0)                                # clearly not one (under the minimum gain)
    RISING = config.TREND_200D_RISING_MIN + 0.02                               # clearly rising, but not strong (the strong bar is higher)
    STRONG = config.STRONG_TREND_200D_MIN + 0.02                               # clearly a strong uptrend

    @staticmethod
    def _f(ret5=SPIKY, sma200_chg=RISING, **kw):
        return {"ret5": ret5, "sma200_chg": sma200_chg, "gain1d": 0.01, "atr_pct": 0.03, **kw}

    @staticmethod
    def _row(c_sma200=2.0):
        """A scored row as the pipeline sees it: contributions to the quality score."""
        return pd.Series({"c_ret5": -1.5, "c_sma200_chg": c_sma200, "c_dist_hi": 1.5, "c_turn20": 0.9, "c_dlv20": 0.6})

    def _watch(self, setup="primed", f=None, stats=STATS, su=None, row=None, dlv=None):
        return pl._short_why_and_watch(setup, f or self._f(), su or self.SU, self._row() if row is None else row, dlv, stats)

    def test_a_spike_in_a_rising_long_term_trend_says_both_and_what_to_do(self):
        why, watch = self._watch(dlv=54.0)
        self.assertEqual(watch, [f"200-day trend rising, but already spiked {self.SPIKY:.0%} in 5 days - {self.PHRASE}"])
        self.assertNotIn("200-day trend rising", why)                              # said once, in the warning line
        self.assertEqual(why, ["near 52-wk high", "very liquid", "delivery ~54%"])

    def test_a_spike_without_a_rising_trend_does_not_claim_one(self):
        why, watch = self._watch(f=self._f(sma200_chg=-0.003))
        self.assertEqual(watch, [f"Already spiked {self.SPIKY:.0%} in 5 days - {self.PHRASE}"])
        self.assertNotIn("200-day trend rising", why)                              # even if the score liked its slope relative to other stocks

    def test_a_spike_in_a_strong_uptrend_leaves_the_trend_to_the_tag(self):
        # the card's "💪 strong uptrend" tag already says it, so neither the warning line nor the why line repeats it
        why, watch = self._watch(f=self._f(sma200_chg=self.STRONG), dlv=54.0)
        self.assertEqual(watch, [f"Already spiked {self.SPIKY:.0%} in 5 days - {self.PHRASE}"])
        self.assertEqual(why, ["near 52-wk high", "very liquid", "delivery ~54%"])

    def test_a_strong_uptrend_without_a_spike_does_not_repeat_itself_in_the_reasons(self):
        why, watch = self._watch(f=self._f(ret5=self.CALM, sma200_chg=self.STRONG), dlv=54.0)
        self.assertEqual(watch, [])
        self.assertNotIn("200-day trend rising", why)                              # the tag carries it; the line keeps three other reasons
        self.assertEqual(why, ["near 52-wk high", "very liquid", "delivery ~54%"])

    def test_a_rising_trend_without_a_spike_keeps_its_plain_reason(self):
        why, watch = self._watch(f=self._f(ret5=self.CALM))
        self.assertEqual(watch, [])
        self.assertIn("200-day trend rising", why)
        why, _ = self._watch(f=self._f(ret5=self.CALM, sma200_chg=0.0))             # a flat 200-day average is not "rising"
        self.assertNotIn("200-day trend rising", why)
        barely = config.TREND_200D_RISING_MIN - 0.001                               # ... and neither is one that is only just creeping up
        why, _ = self._watch(f=self._f(ret5=self.CALM, sma200_chg=barely))
        self.assertNotIn("200-day trend rising", why)

    def test_no_warning_unless_the_gain_is_large_in_money_terms_and_against_other_stocks(self):
        floor, z = config.SPIKE_5D_MIN, config.SPIKE_5D_Z
        tight = {"mean": {"ret5": 0.0}, "std": {"ret5": 0.01}}                      # a very quiet day: a gain just under the floor is many standard deviations ...
        gain = floor + 0.02                                                         # ... and a gain over the floor, in a week when the average stock was nearly as strong:
        ordinary = {"mean": {"ret5": gain - (z - 0.4) * 0.05}, "std": {"ret5": 0.05}}   # only z - 0.4 standard deviations above its peers
        for label, kw in (("under the minimum gain", dict(f=self._f(ret5=floor - 0.005), stats=tight)),   # ... but still not a spike in money terms
                          ("ordinary that day", dict(f=self._f(ret5=gain), stats=ordinary)),
                          ("no yardstick", dict(stats=None)),
                          ("no 5-day return", dict(f=self._f(ret5=float("nan"))))):
            self.assertEqual(self._watch(**kw)[1], [], label)

    def test_a_fresh_breakouts_own_day_is_the_signal_not_a_spike(self):
        # +7% in 5 sessions, of which +6.3% is today's breakout day: nothing had run before it
        fresh = self._f(ret5=0.07, gain1d=0.063)
        _, watch = self._watch("fresh", f=fresh, su={"ext_pct": 0.02, "above_pivot": True})
        self.assertEqual(watch, [])
        # +16% in 5 sessions with a +5% breakout day: it had already run +10.5% before it
        ran = self._f(ret5=0.16, gain1d=0.05)
        _, watch = self._watch("fresh", f=ran, su={"ext_pct": 0.02, "above_pivot": True})
        self.assertEqual(watch, [f"200-day trend rising, but already spiked 10% before today - {self.PHRASE}"])

    def test_the_cut_offs(self):
        floor, z, rising, strong = config.SPIKE_5D_MIN, config.SPIKE_5D_Z, config.TREND_200D_RISING_MIN, config.STRONG_TREND_200D_MIN
        # a spike = a gain of at least `floor` AND at least `z` standard deviations above the average stock's 5-day gain that day
        self.assertTrue(fp.recent_spike(floor + 0.01, z + 0.02))
        self.assertFalse(fp.recent_spike(floor + 0.01, z - 0.02))
        self.assertTrue(fp.recent_spike(floor, z + 3.0))                            # the minimum gain itself counts
        self.assertFalse(fp.recent_spike(floor - 0.0001, z + 3.0))
        self.assertFalse(fp.recent_spike(None, z + 3.0))
        self.assertFalse(fp.recent_spike(floor + 0.05, float("nan")))
        self.assertAlmostEqual(fp.z_score(0.08, self.STATS, "ret5"), (0.08 - self.MEAN) / self.STD)
        for bad in (None, {}, {"mean": {}, "std": {}}, {"mean": {"ret5": 0.0}, "std": {"ret5": 0.0}}):
            self.assertTrue(np.isnan(fp.z_score(0.08, bad, "ret5")))
        self.assertAlmostEqual(fp.gain_before_latest_session(0.1445, 0.05), 1.1445 / 1.05 - 1)
        self.assertTrue(np.isnan(fp.gain_before_latest_session(float("nan"), 0.05)))
        # "rising" = the 200-day average is at least `rising` above its level 20 sessions ago
        self.assertTrue(fp.trend_rising(rising))
        self.assertTrue(fp.trend_rising(rising + 0.02))
        self.assertFalse(fp.trend_rising(rising - 0.001))
        self.assertFalse(fp.trend_rising(-0.02))
        self.assertFalse(fp.trend_rising(float("nan")))
        # "strong" = at least `strong` above it; always also "rising", and a missing number is never strong
        self.assertTrue(fp.trend_strong(strong))
        self.assertTrue(fp.trend_strong(strong + 0.03))
        self.assertFalse(fp.trend_strong(strong - 0.001))
        self.assertFalse(fp.trend_strong(rising + 0.001))                           # rising is not strong
        self.assertFalse(fp.trend_strong(None))
        self.assertFalse(fp.trend_strong(float("nan")))
        for v in (-0.05, 0.0, rising, strong - 0.001, strong, 0.2):
            self.assertTrue(fp.trend_rising(v) or not fp.trend_strong(v), v)

    def test_the_shipped_cut_offs_are_sane(self):
        # guards against a slip when they are edited by hand (4 instead of 0.04, a minus sign, ...)
        self.assertTrue(0.5 <= config.SPIKE_5D_Z <= 2.5, config.SPIKE_5D_Z)                 # standard deviations above the average stock
        self.assertTrue(0.01 <= config.SPIKE_5D_MIN <= 0.15, config.SPIKE_5D_MIN)          # 1% - 15% in five sessions
        self.assertTrue(0.0 <= config.TREND_200D_RISING_MIN <= 0.05, config.TREND_200D_RISING_MIN)   # 200-day average vs 20 sessions ago
        self.assertTrue(config.TREND_200D_RISING_MIN <= config.STRONG_TREND_200D_MIN <= 0.15, config.STRONG_TREND_200D_MIN)   # strong sits above rising

    def test_the_warning_shares_the_cards_room_with_other_cautions_and_is_listed_first(self):
        f = self._f(atr_pct=0.06, gain1d=0.09, ret5=0.20)
        _, watch = self._watch("fresh", f=f, su={"ext_pct": 0.04, "above_pivot": True})
        self.assertEqual(len(watch), 2)
        self.assertIn(self.PHRASE, watch[0])
        self.assertEqual(watch[1], "Already 4.0% above the breakout level")        # the one-day-move note is dropped: the spike line covers it

    @staticmethod
    def _reshape_plant(growth, last_volume_x=None):
        """The planted stock with its last bars replaced by the given day-by-day gains (a steady 12-month up-trend before them)."""
        bars = make_market()
        d = bars["PLANT"].copy()
        prev = float(d["close"].iloc[-len(growth) - 1])
        for i, g in zip(range(-len(growth), 0), growth):
            c = prev * (1 + g)
            o = prev * 1.001
            d.iloc[i, d.columns.get_loc("open")] = o
            d.iloc[i, d.columns.get_loc("close")] = c
            d.iloc[i, d.columns.get_loc("high")] = max(o, c) * (1.004 if i == -1 else 1.002)
            d.iloc[i, d.columns.get_loc("low")] = min(o, c) * 0.998
            prev = c
        if last_volume_x is not None:
            d.iloc[-1, d.columns.get_loc("volume")] = float(d["volume"].iloc[-2]) * last_volume_x
        bars["PLANT"] = d
        return bars

    def test_a_planted_run_up_before_a_breakout_reaches_the_card_and_the_long_write_up(self):
        # +3%, +3%, +2%, +1% (a +9.3% run), then the +4.5% breakout day on 3x volume
        bars = self._reshape_plant((0.03, 0.03, 0.02, 0.01, 0.045))
        res = pl.select_picks("swing", bars, now=at(2026, 10, 13, 17, 0))
        p = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertEqual(p.setup, "fresh")
        self.assertEqual(p.watch[0], f"200-day trend rising, but already spiked 9% before today - {self.PHRASE}")
        card = notify.format_pick(p, 1)
        self.assertIn(f"\n⚠️ 200-day trend rising, but already spiked 9% before today - {self.PHRASE}", card)
        self.assertLessEqual(len(card.split("\n")), 7)
        spike_cautions = [c for c in p.cautions if self.PHRASE in c]
        self.assertEqual(len(spike_cautions), 1, p.cautions)
        self.assertTrue(spike_cautions[0].startswith("The long-term (200-day) trend is rising, but the stock has already spiked 9.3% "
                                                     "in the sessions before the breakout day"), spike_cautions)
        self.assertTrue(spike_cautions[0].endswith("(in the 10-year replay, waiting for a dip did not beat buying at the Buy-at price)"), spike_cautions)
        self.assertFalse(any("pull-back" in c for c in p.cautions), p.cautions)       # the old advice is gone from the write-up too
        self.assertNotIn("Already spiked in the last 5 days", p.cautions)           # the specific line replaces the generic one

    def test_a_planted_run_up_below_its_high_reaches_the_card(self):
        # +3%, +3%, +2%, then two quiet days: coiled just under the high it just made, +8% in 5 sessions, no breakout volume
        bars = self._reshape_plant((0.03, 0.03, 0.02, -0.004, 0.002), last_volume_x=1.0)
        res = pl.select_picks("swing", bars, now=at(2026, 10, 13, 17, 0))
        p = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertEqual(p.setup, "primed")
        self.assertEqual(p.watch[0], f"200-day trend rising, but already spiked 8% in 5 days - {self.PHRASE}")
        self.assertIn("Buy at <b>", notify.format_pick(p, 1))

    def test_an_ordinary_planted_breakout_has_no_spike_warning(self):
        res = pl.select_picks("swing", make_market(), now=at(2026, 10, 13, 17, 0))
        p = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertFalse(any("already spiked" in w.lower() or "continuation" in w for w in p.watch), p.watch)
        self.assertFalse(p.spiked)


class StrongUptrend(unittest.TestCase):
    """
    A stock whose 200-day average is up 5%+ in a month is tagged "💪 strong uptrend" and listed first, and the live scoreboard records it so real trades
    can confirm the 10-year replay (those stocks did better). It labels and orders; it never adds a stock, and it drops one only when more qualify than fit.
    """

    @classmethod
    def setUpClass(cls):
        cls.now = at(2026, 10, 13, 17, 0)
        cls.res = pl.select_picks("swing", make_market(), now=cls.now, max_picks=1000)
        cls.by = {p.symbol: p for p in cls.res.picks}

    def test_strong_uptrends_come_first_then_fresh_before_primed_then_quality(self):
        key = [(0 if p.strong_trend else 1, 0 if p.setup == "fresh" else 1, -p.quality_pct) for p in self.res.picks]
        self.assertEqual(key, sorted(key))
        # the premise, spelled out: a strong primed stock leads a fresh breakout of higher quality that is not strong
        lead, plant = self.by["S001"], self.by["PLANT"]
        self.assertTrue(lead.strong_trend and lead.setup == "primed")
        self.assertTrue(not plant.strong_trend and plant.setup == "fresh" and plant.quality_pct > lead.quality_pct)
        names = [p.symbol for p in self.res.picks]
        self.assertLess(names.index("S001"), names.index("PLANT"))

    def test_the_flags_follow_the_cut_offs_and_the_numbers_are_recorded(self):
        for p in self.res.picks:
            self.assertTrue(np.isfinite(p.trend200_pct) and np.isfinite(p.run_up_pct) and np.isfinite(p.z_run_up), p.symbol)
            self.assertEqual(p.strong_trend, p.trend200_pct >= config.STRONG_TREND_200D_MIN, p.symbol)
            self.assertEqual(p.spiked, fp.recent_spike(p.run_up_pct, p.z_run_up), p.symbol)

    def test_it_changes_the_order_only_never_which_stocks_qualify(self):
        with mock.patch.object(config, "STRONG_TREND_200D_MIN", 9.99):             # nobody counts as strong any more
            plain = pl.select_picks("swing", make_market(), now=self.now, max_picks=1000)
        self.assertFalse(any(p.strong_trend for p in plain.picks))
        self.assertEqual({p.symbol for p in plain.picks}, {p.symbol for p in self.res.picks})
        order = [(0 if p.setup == "fresh" else 1, -p.quality_pct) for p in plain.picks]
        self.assertEqual(order, sorted(order))                                      # the order is then the old one: fresh first, then quality

    def test_when_more_qualify_than_fit_the_strong_ones_are_kept(self):
        res = pl.select_picks("swing", make_market(), now=self.now, max_picks=1)
        self.assertEqual([p.symbol for p in res.picks], ["S001"])                  # not PLANT, the higher-quality fresh breakout that is not strong

    def test_the_card_starts_its_why_line_with_the_tag_and_keeps_every_other_reason(self):
        p = self.by["S001"]
        card = notify.format_pick(p, 1).split("\n")
        plain = notify.format_pick(dataclasses.replace(p, strong_trend=False), 1).split("\n")
        self.assertEqual(len(card), len(plain))                                     # no extra line
        self.assertTrue(card[3].startswith("Profit marks "), card[3])               # header, Buy at, Stop, Profit marks, then the why line
        self.assertTrue(card[4].startswith("💪 strong uptrend · "), card[4])
        self.assertEqual(card[4].split(" · ")[1:], plain[4].split(" · "))           # the tag takes its own slot: nothing else is dropped
        self.assertNotIn("💪", "\n".join(plain))
        # a fresh buy-stop card also shows "up x% on y volume": the tag + the usual four items
        line = notify.format_pick(dataclasses.replace(self.by["PLANT"], strong_trend=True), 1).split("\n")[4]
        self.assertTrue(line.startswith("💪 strong uptrend · up "), line)
        self.assertEqual(len(line.split(" · ")), 5, line)

    def test_the_footer_explains_the_tag_only_when_a_card_carries_it(self):
        legend = "strong uptrend = the 200-day average is up 5%+ in a month; these are listed first"
        msg = notify.format_scan_message(self.res, "swing", now=self.now)
        self.assertEqual(msg.count(legend), 1)
        self.assertLess(msg.index(legend), msg.index("Trail = keep the stop"))
        none = dataclasses.replace(self.res, picks=[dataclasses.replace(p, strong_trend=False) for p in self.res.picks])
        self.assertNotIn("💪", notify.format_scan_message(none, "swing", now=self.now))

    def test_the_scan_files_show_the_tag_and_carry_the_numbers(self):
        from reports import summary
        row = self.by["S001"].to_row()
        self.assertIs(row["strong_trend"], True)
        self.assertAlmostEqual(row["trend_200d_chg"], self.by["S001"].trend200_pct, places=4)
        df = summary.picks_frame(self.res.picks)
        self.assertLessEqual({"trend_200d_chg", "run_up_5d", "z_run_up", "strong_trend", "spiked"}, set(df.columns))
        self.assertEqual(int(df["strong_trend"].sum()), sum(p.strong_trend for p in self.res.picks))
        text = summary.render_summary("swing", self.res, self.now)
        self.assertIn("**S001** 💪", text)
        self.assertNotIn("**PLANT** 💪", text)
        self.assertEqual(text.count("strong uptrend = the 200-day average"), 1)
        self.assertIn("Strong uptrend: the 200-day average is up", summary.render_summary("swing", self.res, self.now, detail=True))


class StrongUptrendLedger(unittest.TestCase):
    """The live scoreboard records the trend and the run-up of every new pick; ledger files written before that stay valid ("not recorded", never "no")."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "trades.csv")
        self.now = at(2026, 10, 13, 17, 0)
        self.res = pl.select_picks("swing", make_market(), now=self.now, max_picks=1000)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, p):
        led = outcomes.load_ledger(self.path)
        return led[led["trade_id"] == f"{p.symbol}_{p.signal_date}_{p.scan_mode}"].iloc[0]

    def test_new_picks_record_the_numbers_and_the_flags(self):
        outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        seen = set()
        for p in self.res.picks:
            r = self._row(p)
            self.assertAlmostEqual(r["trend200"], p.trend200_pct, places=5)
            self.assertAlmostEqual(r["run_up"], p.run_up_pct, places=5)
            self.assertAlmostEqual(r["z_run_up"], p.z_run_up, places=3)
            self.assertEqual((r["strong_trend"], r["spiked"]), (float(p.strong_trend), float(p.spiked)))
            seen.add(bool(p.strong_trend))
        self.assertEqual(seen, {True, False})                                       # the market has both kinds, so both flags were really written

    def test_a_ledger_from_before_the_columns_still_loads_and_its_trades_stay_not_recorded(self):
        old_cols = [c for c in outcomes.COLUMNS if c not in outcomes.SIGNAL_COLS]
        old = pd.DataFrame([{**{c: "" for c in old_cols}, "trade_id": "OLD_2026-10-01_swing", "symbol": "OLD", "scan_mode": "swing",
                             "setup": "primed", "signal_date": "2026-10-01", "status": "CLOSED", "net": "0.05", "R": "1.2",
                             "quality_pct": "70"}], columns=old_cols)
        old.to_csv(self.path, index=False)
        led = outcomes.load_ledger(self.path)
        self.assertEqual(list(led.columns), outcomes.COLUMNS)
        self.assertTrue(led[outcomes.SIGNAL_COLS].isna().all().all())
        outcomes.log_new_picks(self.res.picks, self.now, path=self.path)
        led = outcomes.load_ledger(self.path)
        self.assertEqual(len(led), 1 + len(self.res.picks))
        self.assertTrue(led.loc[led["symbol"] == "OLD", outcomes.SIGNAL_COLS].isna().all().all())
        self.assertTrue(led.loc[led["symbol"] != "OLD", ["strong_trend", "spiked"]].notna().all().all())
        with open(self.path, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        self.assertEqual(header[:len(old_cols)], old_cols)                          # the old columns keep their place; the new ones are appended

    def test_a_pick_that_does_not_know_its_trend_is_logged_as_not_recorded_not_as_no(self):
        p = dataclasses.replace(self.res.picks[0], trend200_pct=None, run_up_pct=None, z_run_up=None, strong_trend=False, spiked=False)
        outcomes.log_new_picks([p], self.now, path=self.path)
        self.assertTrue(self._row(p)[outcomes.SIGNAL_COLS].isna().all())


class LiveScoreboardHints(unittest.TestCase):
    """The live scoreboard report puts the strong-uptrend tag and the spike warning to the test on real closed trades."""

    def setUp(self):
        from backtest import engine
        self.engine = engine
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _report(self, rows):
        base = {c: "" for c in outcomes.COLUMNS}
        frame = pd.DataFrame([{**base, "status": "CLOSED", "scan_mode": "swing", "setup": "primed", "regime": "healthy", "signal_date": "2026-11-03",
                               "quality_pct": 70, "fill_n": 1, "exit_n": 9, **r} for r in rows], columns=outcomes.COLUMNS)
        path = os.path.join(self.tmp, "ledger.csv")
        frame.to_csv(path, index=False)
        return self.engine.live_scoreboard_report(outcomes.load_ledger(path), out_dir=self.tmp)[1]

    def test_it_compares_strong_uptrends_with_the_rest_and_says_when_a_group_is_too_small(self):
        rows = [dict(net=0.10, R=2.0, strong_trend=1, spiked=0), dict(net=0.06, R=1.2, strong_trend=1, spiked=1), dict(net=-0.02, R=-0.4, strong_trend=1, spiked=0),
                dict(net=-0.03, R=-0.6, strong_trend=0, spiked=0), dict(net=0.01, R=0.2, strong_trend=0, spiked=1),
                dict(net=0.04, R=0.8)]                                              # logged before the flags existed: blank
        text = self._report(rows)
        self.assertIn("## Do the card's new hints hold up on real trades?", text)
        self.assertRegex(text, r"\| a\) 💪 strong uptrend[^|]*\| 3 \|")
        self.assertRegex(text, r"\| b\) all other picks \| 2 \|")
        self.assertRegex(text, r"\| a\) warned: already spiked \| 2 \|")
        self.assertRegex(text, r"\| b\) not warned \| 3 \|")
        self.assertIn("1 of the 6 closed trades were logged before these were recorded", text)
        self.assertIn("fewer than 30 closed trades is too small to judge", text)

    def test_an_old_ledger_with_nothing_recorded_adds_no_such_section(self):
        text = self._report([dict(net=0.04, R=0.8), dict(net=-0.02, R=-0.5)])
        self.assertNotIn("new hints", text)
        self.assertIn("All closed trades", text)

    def test_no_small_sample_note_when_every_group_has_30_closed_trades(self):
        rows = [dict(net=0.03, R=0.6, strong_trend=1, spiked=0)] * 30 + [dict(net=-0.01, R=-0.2, strong_trend=0, spiked=0)] * 30
        text = self._report(rows)
        self.assertRegex(text, r"\| a\) 💪 strong uptrend[^|]*\| 30 \|")
        self.assertRegex(text, r"\| b\) all other picks \| 30 \|")
        self.assertNotIn("too small to judge", text)


class EarlierPicks(unittest.TestCase):
    """
    A stock picked on an earlier day whose buy-stop order is still waiting, and which still passes today's tests, is listed ABOVE today's new picks when it
    ranks above every one of them (or when there is no new pick). It only reminds: nothing is re-picked or re-logged, so the scoreboard is untouched.
    """
    LEGEND = "strong uptrend = the 200-day average is up 5%+ in a month; these are listed first"

    @classmethod
    def setUpClass(cls):
        cls.now = at(2026, 10, 13, 17, 0)
        cls.bars = make_market()
        # day 1: 11 picks. S001 = primed + strong uptrend; PLANT = fresh breakout of the highest quality, not strong; S043 = primed, quality 75; S006 = weakest (52)
        cls.first = pl.select_picks("swing", cls.bars, now=cls.now, max_picks=1000)
        cls.by = {p.symbol: p for p in cls.first.picks}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "trades.csv")
        outcomes.log_new_picks(self.first.picks, self.now, path=self.path)         # all of them are now waiting buy-stops, 5 sessions left

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _today(self, on_list):
        """Today's scan when `on_list` are already on the list: they come back as repeats, not as new picks."""
        return pl.select_picks("swing", self.bars, now=self.now, max_picks=1000, exclude=set(on_list))

    def _ledger(self, **edits):
        """The ledger with some cells changed: _ledger(S001={"status": "OPEN"}, PLANT={"sessions": 2})."""
        led = outcomes.load_ledger(self.path)
        for sym, cols in edits.items():
            for col, val in cols.items():
                led.loc[led["symbol"] == sym, col] = val
        return led

    def _earlier(self, res, led=None):
        return pl.earlier_picks(res, outcomes.load_ledger(self.path) if led is None else led)

    def _msg(self, res, earlier, **kw):
        return notify.format_scan_message(res, "swing", now=self.now, earlier=earlier, **kw)

    # ---- which earlier picks are listed ---------------------------------------------------------------------------------
    def test_with_no_new_pick_every_waiting_order_that_still_qualifies_is_listed_best_first(self):
        res = self._today(self.by)                                                  # everything is already on the list
        self.assertEqual(res.picks, [])
        earlier = self._earlier(res)
        self.assertEqual([e.symbol for e in earlier], [p.symbol for p in self.first.picks])   # the list order: strong first, fresh first, then quality
        lead = earlier[0]
        self.assertEqual((lead.symbol, lead.setup, lead.strong_trend, lead.picked), ("S001", "primed", True, "2026-10-13"))
        self.assertAlmostEqual(lead.trigger, self.by["S001"].entry_price, places=2)
        self.assertAlmostEqual(lead.quality_pct, self.by["S001"].quality_pct, places=3)
        self.assertEqual(lead.sessions_left, config.ENTRY_VALID_SESSIONS)
        self.assertEqual([e.symbol for e in earlier if e.strong_trend], ["S001"])
        self.assertEqual(next(e for e in earlier if e.symbol == "PLANT").setup, "fresh")

    def test_an_earlier_pick_is_listed_only_if_it_ranks_above_every_new_pick(self):
        res = self._today({"S001", "S043", "S006"})
        self.assertEqual({p.symbol for p in res.picks}, set(self.by) - {"S001", "S043", "S006"})
        # the best new pick is PLANT, a fresh breakout. S001 (strong uptrend) is above it; S043 (primed, quality 75) and S006 are below it
        self.assertEqual([e.symbol for e in self._earlier(res)], ["S001"])
        # with PLANT on the list too, the best new pick is S025 (primed, quality 69): S001, PLANT and S043 (75) are above it, S006 (52) is not
        res2 = self._today({"S001", "PLANT", "S043", "S006"})
        self.assertEqual([e.symbol for e in self._earlier(res2)], ["S001", "PLANT", "S043"])

    def test_an_earlier_pick_weaker_than_any_new_pick_is_not_listed(self):
        self.assertEqual(self._earlier(self._today({"S006"})), [])
        self.assertEqual(self._earlier(self._today({"S025"})), [])                  # PLANT, a new fresh breakout, outranks a primed stock of quality 69

    def test_a_new_fresh_breakout_is_never_pushed_below_an_earlier_about_to_break_stock(self):
        res = self._today({"S043", "S025", "S061"})                                  # three earlier primed stocks (quality 75, 69, 66)
        self.assertIn("PLANT", [p.symbol for p in res.picks])                       # ...and a new fresh breakout of quality 99
        self.assertEqual(self._earlier(res), [])
        msg = self._msg(res, self._earlier(res))
        self.assertNotIn("EARLIER", msg)
        self.assertNotIn("NEW ·", msg)

    def test_a_strong_uptrend_stays_on_top_even_when_a_fresh_breakout_of_higher_quality_is_new(self):
        res = self._today({"S001"})
        plant = next(p for p in res.picks if p.symbol == "PLANT")
        self.assertTrue(plant.setup == "fresh" and not plant.strong_trend and plant.quality_pct > self.by["S001"].quality_pct)
        self.assertEqual([e.symbol for e in self._earlier(res)], ["S001"])

    def test_only_a_buy_stop_that_is_still_waiting_counts(self):
        gone = {"S001": "OPEN", "S043": "CLOSED", "S025": "UNFILLED", "S061": "VOID"}          # bought / finished / expired / dropped
        led = self._ledger(**{s: {"status": st} for s, st in gone.items()})
        names = [e.symbol for e in self._earlier(self._today(self.by), led)]
        self.assertEqual(names, [p.symbol for p in self.first.picks if p.symbol not in gone])

    def test_the_sessions_left_come_from_the_ledger_and_an_order_with_none_left_is_not_listed(self):
        led = self._ledger(S001={"sessions": 4}, S043={"sessions": 5}, PLANT={"sessions": 2})
        got = {e.symbol: e.sessions_left for e in self._earlier(self._today(self.by), led)}
        self.assertEqual((got["S001"], got["PLANT"], got["S025"]), (1, 3, 5))
        self.assertNotIn("S043", got)                                               # a 5-session order with 5 sessions gone has expired

    def test_an_order_that_probably_triggered_today_is_not_called_waiting(self):
        res = self._today(self.by)
        trigger = float(outcomes.load_ledger(self.path).set_index("symbol").loc["S001", "trigger"])
        for edit in ({"price": trigger + 1.0}, {"high": trigger}, {"high": trigger * 1.02},       # a live scan: the ledger has not seen today's bar yet
                     {"price": None, "high": None}, {"price": float("nan"), "high": float("nan")}):
            info = {**res.repeat_info, "S001": {**res.repeat_info["S001"], **edit}}
            names = [e.symbol for e in self._earlier(dataclasses.replace(res, repeat_info=info))]
            self.assertNotIn("S001", names, edit)
            self.assertIn("PLANT", names, edit)
        under = {**res.repeat_info, "S001": {**res.repeat_info["S001"], "price": trigger - 0.5, "high": trigger - 0.01}}
        self.assertIn("S001", [e.symbol for e in self._earlier(dataclasses.replace(res, repeat_info=under))])

    def test_a_stock_that_no_longer_passes_todays_tests_is_not_listed(self):
        res = self._today(self.by)
        info = {k: v for k, v in res.repeat_info.items() if k != "S001"}              # no longer in a setup / top half by quality
        names = [e.symbol for e in self._earlier(dataclasses.replace(res, repeat_info=info))]
        self.assertEqual(names, [p.symbol for p in self.first.picks if p.symbol != "S001"])

    def test_nothing_is_listed_without_a_ledger_or_a_waiting_order_or_a_readable_order(self):
        res = self._today(self.by)
        self.assertEqual(pl.earlier_picks(res, None), [])
        self.assertEqual(pl.earlier_picks(res, pd.DataFrame(columns=outcomes.COLUMNS)), [])
        self.assertEqual(pl.earlier_picks(dataclasses.replace(res, repeat_info={}), outcomes.load_ledger(self.path)), [])
        led = self._ledger(S001={"trigger": float("nan")}, PLANT={"valid_sessions": float("nan")}, S043={"sessions": float("nan")})
        names = [e.symbol for e in self._earlier(res, led)]
        self.assertTrue({"S001", "PLANT", "S043"}.isdisjoint(names))                 # a row it cannot read is skipped, never a crash
        self.assertIn("S025", names)

    def test_a_stock_with_two_waiting_orders_is_listed_once_with_the_later_one(self):
        led = outcomes.load_ledger(self.path)
        twin = led[led["symbol"] == "S001"].copy()
        twin["trade_id"], twin["signal_date"], twin["sessions"] = "S001_2026-10-14_btst", "2026-10-14", 1
        names = self._earlier(self._today(self.by), pd.concat([led, twin], ignore_index=True))
        self.assertEqual([e.symbol for e in names].count("S001"), 1)
        lead = next(e for e in names if e.symbol == "S001")
        self.assertEqual((lead.picked, lead.sessions_left), ("2026-10-14", config.ENTRY_VALID_SESSIONS - 1))

    def test_it_only_reads_nothing_is_picked_again_or_logged(self):
        res = self._today({"S001"})
        picks_before = [p.symbol for p in res.picks]
        with open(self.path, "rb") as fh:
            raw = fh.read()
        self.assertTrue(self._earlier(res))
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), raw)                                        # the ledger file is byte for byte what it was
        self.assertEqual([p.symbol for p in res.picks], picks_before)
        self.assertNotIn("S001", picks_before)
        self.assertEqual(len(outcomes.load_ledger(self.path)), len(self.first.picks))

    def test_a_maximum_of_zero_switches_the_block_off(self):
        res = self._today(self.by)
        self.assertTrue(self._earlier(res))
        with mock.patch.object(config, "EARLIER_PICKS_MAX", 0):
            self.assertEqual(self._earlier(res), [])
            full = [pl.EarlierPick("ZZZ", "primed", 88.0, True, "2026-10-12", 123.4, 2)]       # even a list handed in directly shows nothing
            self.assertEqual(notify.format_earlier_block(full), "")
            msg = self._msg(self._today({"S006"}), full)
        self.assertNotIn("EARLIER", msg)
        self.assertNotIn("NEW ·", msg)
        self.assertNotIn("ZZZ", msg)
        self.assertIn("+1 more qualify but are already on your list.", msg)                    # S006 is counted as before

    def test_new_and_earlier_picks_share_one_ranking(self):
        key = pl.rank_key
        self.assertLess(key(True, "primed", 60), key(False, "fresh", 99))            # strong uptrend first
        self.assertLess(key(False, "fresh", 60), key(False, "primed", 99))           # then fresh breakouts before about-to-break stocks
        self.assertLess(key(False, "primed", 80), key(False, "primed", 70))          # then quality
        ranked = sorted(self.first.picks, key=lambda p: key(p.strong_trend, p.setup, p.quality_pct))
        self.assertEqual([p.symbol for p in ranked], [p.symbol for p in self.first.picks])

    # ---- the message -----------------------------------------------------------------------------------------------------
    def test_the_block_sits_above_the_new_picks_and_those_say_new(self):
        res = self._today({"S001", "PLANT"})                                        # the best new pick is S043 (primed, 75): both are above it
        earlier = self._earlier(res)
        self.assertEqual([e.symbol for e in earlier], ["S001", "PLANT"])
        msg = self._msg(res, earlier)
        self.assertTrue(msg.startswith("📈 <b>SWING SCAN</b>"))
        self.assertLess(msg.index("EARLIER PICKS, STILL STRONG"), msg.index("NEW · PLACE THESE BUY ORDERS</b> (valid"))
        self.assertIn("<b>S001</b> 👀 💪 · Quality 97/100 · picked 13 Oct\nBuy at <b>1,969.25</b> · order valid 5 more sessions", msg)
        self.assertIn("<b>PLANT</b> 🔥 · Quality 99/100 · picked 13 Oct\nBuy at <b>103.25</b> · order valid 5 more sessions", msg)
        self.assertEqual(msg.count("NEW · "), 1)
        self.assertEqual(msg.count("<b>1. "), 1)                                    # the new cards are numbered from 1; the earlier block has no numbers
        self.assertEqual(msg.count(self.LEGEND), 1)                                # no new pick is strong: the 💪 is on the earlier block only
        self.assertLess(msg.index(self.LEGEND), msg.index("Trail = keep the stop"))
        chk = Messages._Check()
        chk.feed(msg)
        self.assertEqual((chk.err, chk.stack), ([], []))
        block = notify.format_earlier_block(earlier)
        self.assertNotIn("\n\n", block)                                             # no blank line inside: the splitter never cuts it
        self.assertEqual(sum(block in c for c in notify.split_message(msg)), 1)

    def test_on_a_day_with_nothing_new_the_block_follows_the_no_new_pick_line_and_nothing_says_new(self):
        res = self._today(self.by)
        msg = self._msg(res, self._earlier(res))
        self.assertNotIn("NEW ·", msg)
        self.assertLess(msg.index("NO NEW PICK."), msg.index("EARLIER PICKS, STILL STRONG"))
        self.assertEqual(msg.count("Buy at <b>"), config.EARLIER_PICKS_MAX)
        self.assertEqual(msg.count("💪 · Quality"), 1)

    def test_without_earlier_picks_the_message_is_unchanged(self):
        res = self._today({"S006"})
        plain = notify.format_scan_message(res, "swing", now=self.now)
        for earlier in (None, [], self._earlier(res)):
            self.assertEqual(self._msg(res, earlier), plain)
        self.assertNotIn("NEW ·", plain)
        self.assertNotIn("EARLIER", plain)

    def test_a_live_afternoon_message_says_new_on_its_buy_now_heading_too(self):
        live = Messages._live()
        self.assertTrue(any(p.entry_mode == "close" for p in live.picks))
        e = pl.EarlierPick("ZZZ", "primed", 88.0, True, "2026-10-12", 123.4, 2)
        msg = notify.format_scan_message(live, "btst", now=at(2026, 10, 13, 14, 45), earlier=[e])
        self.assertLess(msg.index("EARLIER PICKS, STILL STRONG"), msg.index("NEW · BUY NOW</b> (near the close"))
        self.assertIn("<b>ZZZ</b> 👀 💪 · Quality 88/100 · picked 12 Oct\nBuy at <b>123.40</b> · order valid 2 more sessions", msg)

    def test_the_block_is_capped_and_says_how_many_more_qualify(self):
        many = [pl.EarlierPick(f"E{i}", "primed", 90.0 - i, False, "2026-10-12", 100.0 + i, 3) for i in range(8)]
        cap = config.EARLIER_PICKS_MAX
        block = notify.format_earlier_block(many)
        self.assertEqual(block.count("Buy at <b>"), cap)
        self.assertEqual([i for i in range(8) if f"<b>E{i}</b>" in block], list(range(cap)))      # the best ones, in order
        self.assertIn(f"+{8 - cap} more earlier picks also still qualify (on your list)", block)
        with mock.patch.object(config, "EARLIER_PICKS_MAX", 2):
            block = notify.format_earlier_block(many)
            self.assertEqual(block.count("Buy at <b>"), 2)
            self.assertIn("+6 more earlier picks also still qualify (on your list)", block)
        with mock.patch.object(config, "EARLIER_PICKS_MAX", 7):
            self.assertIn("+1 more earlier pick also still qualifies (on your list)", notify.format_earlier_block(many))      # singular
        self.assertNotIn("more earlier pick", notify.format_earlier_block(many[:cap]))

    def test_an_earlier_pick_shows_the_same_profit_marks_its_original_card_had(self):
        res = self._today(self.by)                                                  # everything is already on the list
        earlier = self._earlier(res)
        self.assertEqual(len(earlier), len(self.first.picks))
        for e in earlier:
            p = self.by[e.symbol]
            self.assertEqual(e.marks, (p.r1, p.r2, p.r3), e.symbol)                 # worked out from the ledger row: identical to the card, to the paisa
        block = notify.format_earlier_block(earlier)
        shown = earlier[:config.EARLIER_PICKS_MAX]
        for e in shown:
            self.assertIn(f"Buy at <b>{pl.fmt_price(e.trigger)}</b>", block)
            self.assertIn("Profit marks " + " · ".join(pl.fmt_price(v) for v in e.marks), block)
        self.assertEqual(block.count("Profit marks"), len(shown))
        lines = block.split("\n")
        for n, ln in enumerate(lines):
            if ln.startswith("Buy at"):
                self.assertTrue(lines[n + 1].startswith("Profit marks "), lines[n + 1])    # directly under the buy line
        self.assertNotIn("\n\n", block)                                             # still one block for the splitter

    def test_an_older_ledger_without_the_plan_columns_gives_a_line_without_marks(self):
        res = self._today(self.by)
        led = outcomes.load_ledger(self.path).drop(columns=["atr_pct", "stop_mult"])
        earlier = self._earlier(res, led)
        self.assertTrue(earlier)
        self.assertTrue(all(e.marks is None for e in earlier))
        block = notify.format_earlier_block(earlier)
        self.assertIn("Buy at <b>", block)
        self.assertNotIn("Profit marks", block)
        led2 = outcomes.load_ledger(self.path)
        led2["atr_pct"] = float("nan")                                              # a blank plan cell
        self.assertTrue(all(e.marks is None for e in self._earlier(res, led2)))

    def test_small_details_of_a_line(self):
        one = notify.format_earlier_block([pl.EarlierPick("ZZZ", "fresh", 70.0, False, "2026-10-09", 55.5, 1)])
        self.assertIn("<b>ZZZ</b> 🔥 · Quality 70/100 · picked 9 Oct", one)
        self.assertIn("order valid 1 more session", one)
        self.assertNotIn("1 more sessions", one)
        self.assertNotIn("💪", one)
        self.assertEqual(notify.format_earlier_block([]), "")
        self.assertEqual((notify._short_iso("2026-10-03"), notify._short_iso("garbage"), notify._short_iso("")), ("3 Oct", "garbage", ""))

    def test_the_more_qualify_line_does_not_count_the_earlier_picks_again(self):
        res = self._today({"S001", "PLANT", "S006"})
        earlier = self._earlier(res)                                                # S001 and PLANT; S006 is not put on top
        self.assertEqual([e.symbol for e in earlier], ["S001", "PLANT"])
        self.assertIn("+1 more qualify but are already on your list.", self._msg(res, earlier))
        res2 = self._today({"S001", "PLANT"})
        self.assertNotIn("more qualify", self._msg(res2, self._earlier(res2)))      # both repeats are in the block
        with mock.patch.object(config, "EARLIER_PICKS_MAX", 1):                      # PLANT falls into the block's own "+1 more" line, not the other one
            msg = self._msg(res, earlier)
        self.assertIn("+1 more earlier pick also still qualifies", msg)
        self.assertIn("+1 more qualify but are already on your list.", msg)

    def test_the_legend_is_there_only_when_a_listed_card_carries_the_tag(self):
        res = self._today({"S043"})
        self.assertEqual(self._msg(res, []).count(self.LEGEND), 1)                  # S001 is a new pick in a strong uptrend: its card carries the tag
        quiet = dataclasses.replace(res, picks=[dataclasses.replace(p, strong_trend=False) for p in res.picks])
        plain = pl.EarlierPick("ZZZ", "primed", 80.0, False, "2026-10-12", 10.0, 3)
        strong = pl.EarlierPick("YYY", "primed", 90.0, True, "2026-10-12", 10.0, 3)
        self.assertNotIn(self.LEGEND, self._msg(quiet, [plain]))
        self.assertEqual(self._msg(quiet, [strong, plain]).count(self.LEGEND), 1)   # the tag is on an earlier line only
        many = [pl.EarlierPick(f"E{i}", "primed", 80.0 - i, False, "2026-10-12", 10.0, 3) for i in range(config.EARLIER_PICKS_MAX)]
        self.assertNotIn(self.LEGEND, self._msg(quiet, many + [strong]))            # a strong one that is not shown does not need explaining

    def test_the_scan_files_list_them_too_and_the_pick_table_holds_only_new_picks(self):
        from reports import summary
        res = self._today({"S001", "PLANT"})
        earlier = self._earlier(res)
        text = summary.render_summary("swing", res, self.now, earlier=earlier)
        self.assertIn("**Earlier picks, still strong**", text)
        self.assertIn("- **S001** 💪 - picked 2026-10-13, buy at 1,969.25 (order still waiting, valid 5 more sessions), quality 97/100", text)
        self.assertIn("- **PLANT** - picked 2026-10-13, buy at 103.25 (order still waiting, valid 5 more sessions), quality 99/100", text)
        self.assertIn(", profit marks " + " · ".join(pl.fmt_price(v) for v in earlier[0].marks), text)
        self.assertEqual(text.count("strong uptrend = the 200-day average"), 1)
        self.assertNotIn("Earlier picks", summary.render_summary("swing", res, self.now))
        self.assertEqual(len(summary.picks_frame(res.picks)), len(res.picks))
        self.assertNotIn("S001", list(summary.picks_frame(res.picks)["symbol"]))


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


class BuildingUpWatch(unittest.TestCase):
    """
    Strong stocks 3%-6% below their 50-day high are shown as a WATCH list (added 2026-11-02 at the user's request): on the swing message only, with no order,
    nothing logged and nothing scored. They join the picks only through the normal rule, once they get within 3% of that high.
    """
    NOW = at(2026, 10, 13, 17, 0)
    LEGEND = "strong uptrend = the 200-day average is up 5%+ in a month; these are listed first"

    @classmethod
    def setUpClass(cls):
        cls.bars = make_market()
        cls.res = pl.select_picks("swing", cls.bars, now=cls.NOW, max_picks=1000)
        cls.building = pl.building_up(cls.res)

    @staticmethod
    def _result(rows, picks=(), repeats=()):
        """A selection result whose feature table holds just what building_up reads. rows = {symbol: overrides of the defaults}; the default stock qualifies."""
        base = {"close": 95.0, "piv50": 100.0, "piv_incl": 100.0, "quality_pct": 80.0, "sma200_chg": 0.02, "c_ret252": 1.0, "ret252": 0.40}
        feats = pd.DataFrame({s: {**base, **r} for s, r in rows.items()}).T
        return pl.SelectionResult(list(picks), list(repeats), {}, {}, "healthy", None, feats, [], None, False, 1.0, True)

    @staticmethod
    def _names(res, **kw):
        return [b.symbol for b in pl.building_up(res, **kw)]

    @staticmethod
    def _bu(symbol="AAA", quality=95.4, strong=True, gap=0.034, high=1740.0, ret12m=0.53):
        return pl.BuildingUp(symbol, quality, strong, gap, high, high * (1 - gap), 0.032, ret12m)

    # ---- who is listed ------------------------------------------------------------------------------------------------
    def test_the_band_starts_exactly_where_about_to_break_out_ends_and_stops_at_six_percent(self):
        closes = [round(float(x), 2) for x in np.arange(90.0, 100.01, 0.25)]
        listed = set(self._names(self._result({f"C{i}": {"close": c} for i, c in enumerate(closes)})))
        for i, c in enumerate(closes):
            primed = fp.detect_setup({"close": c, "piv50": 100.0, "piv_incl": 100.0, "high": 100.0})["setup"] == "primed"
            in_band = (not primed) and c >= (1 - config.BUILDING_UP_NEAR_PCT) * 100.0
            self.assertEqual(f"C{i}" in listed, in_band, c)
            self.assertFalse(primed and f"C{i}" in listed, c)                         # never both a pick-to-be and a watch name
        self.assertTrue(listed and len(listed) < len(closes))                         # some are in the band, the ones nearer and further are not

    def test_each_test_can_keep_a_stock_out_and_the_edges_are_as_documented(self):
        gate, rising, bar = config.QUALITY_GATE_PCT, config.TREND_200D_RISING_MIN, config.BUILDING_UP_MIN_RETURN_Z
        res = self._result({
            "OK": {},
            "Q_AT_GATE": {"quality_pct": gate}, "Q_BELOW": {"quality_pct": gate - 0.1},
            "T_AT_RISING": {"sma200_chg": rising}, "T_BELOW": {"sma200_chg": rising - 0.0001}, "T_NAN": {"sma200_chg": np.nan},
            "R_AT_BAR": {"c_ret252": bar}, "R_ABOVE": {"c_ret252": bar + 0.01}, "R_NAN": {"c_ret252": np.nan},
            "ABOVE_OLD_HIGH": {"close": 101.0, "piv_incl": 106.0},                    # closed above its old 50-day high (it already broke out): not building up
            "NO_PRICE": {"close": np.nan},
        })
        self.assertEqual(set(self._names(res)), {"OK", "Q_AT_GATE", "T_AT_RISING", "R_ABOVE"})

    def test_a_pick_or_a_stock_already_on_the_list_is_never_listed_twice(self):
        res = self._result({"A": {}, "B": {}, "C": {}, "D": {}}, picks=[mock.Mock(symbol="B")], repeats=[("C", "primed")])
        self.assertEqual(self._names(res), ["A", "D"])
        self.assertEqual(self._names(res, exclude={"D"}), ["A"])                       # a waiting order, a bought stock or the cool-down

    def test_strong_uptrends_come_first_then_the_nearest_to_the_high_then_quality_then_the_name(self):
        res = self._result({
            "FAR_STRONG": {"close": 94.5, "sma200_chg": 0.08},
            "NEAR": {"close": 96.8, "quality_pct": 70}, "NEAR_BETTER": {"close": 96.8, "quality_pct": 90}, "NEAR_B": {"close": 96.8, "quality_pct": 90},
            "MID": {"close": 95.5},
        })
        self.assertEqual(self._names(res), ["FAR_STRONG", "NEAR_B", "NEAR_BETTER", "NEAR", "MID"])

    def test_switched_off_a_missing_column_and_an_empty_table_give_an_empty_list(self):
        res = self._result({"A": {}})
        self.assertEqual(self._names(res), ["A"])
        with mock.patch.object(config, "BUILDING_UP_MAX", 0):
            self.assertEqual(pl.building_up(res), [])
        self.assertEqual(pl.building_up(dataclasses.replace(res, feats=res.feats.drop(columns=["c_ret252"]))), [])
        self.assertEqual(pl.building_up(dataclasses.replace(res, feats=pd.DataFrame())), [])
        self.assertEqual(pl.building_up(dataclasses.replace(res, feats=None)), [])

    def test_a_listed_stock_carries_its_numbers_and_a_missing_12_month_return_stays_blank(self):
        (b,) = pl.building_up(self._result({"A": {"close": 96.0, "ret252": 0.53}}))
        self.assertEqual((b.symbol, b.strong_trend, b.high, b.price), ("A", False, 100.0, 96.0))
        self.assertAlmostEqual(b.gap_pct, 0.04)
        self.assertAlmostEqual(b.ret12m_pct, 0.53)
        self.assertAlmostEqual(b.trend200_pct, 0.02)
        (blank,) = pl.building_up(self._result({"A": {"ret252": np.nan}}))
        self.assertIsNone(blank.ret12m_pct)
        self.assertIsNone(blank.to_row()["ret_12m_pct"])
        self.assertEqual(list(b.to_row()), pl.BUILDING_UP_COLUMNS)

    # ---- on the synthetic market (real features, real scores) ---------------------------------------------------------------
    def test_on_the_synthetic_market_every_listed_stock_follows_every_rule_and_is_not_a_pick(self):
        self.assertTrue(self.building)
        f, picks = self.res.feats, {p.symbol for p in self.res.picks}
        for b in self.building:
            row = f.loc[b.symbol]
            gap = 1 - row["close"] / row["piv_incl"]
            self.assertTrue(config.PRIMED_NEAR_PCT < gap <= config.BUILDING_UP_NEAR_PCT + 1e-12, (b.symbol, gap))
            self.assertAlmostEqual(b.gap_pct, gap)
            self.assertGreaterEqual(row["quality_pct"], config.QUALITY_GATE_PCT)
            self.assertTrue(fp.trend_rising(row["sma200_chg"]))
            self.assertGreater(row["c_ret252"], config.BUILDING_UP_MIN_RETURN_Z)
            self.assertLessEqual(row["close"], row["piv50"])
            self.assertNotIn(b.symbol, picks)
            self.assertEqual(b.strong_trend, fp.trend_strong(row["sma200_chg"]))
        key = [(0 if b.strong_trend else 1, b.gap_pct, -b.quality_pct) for b in self.building]
        self.assertEqual(key, sorted(key))
        self.assertTrue(any(b.strong_trend for b in self.building) and not all(b.strong_trend for b in self.building))   # both kinds in this market

    def test_a_pick_that_slips_below_the_about_to_break_band_moves_to_the_watch_list(self):
        self.assertIn("S001", [p.symbol for p in self.res.picks])                       # a strong primed pick...
        self.assertNotIn("S001", [b.symbol for b in self.building])
        bars = {s: d.copy() for s, d in self.bars.items()}
        d = bars["S001"]
        px = float(self.res.feats.at["S001", "piv_incl"]) * 0.955                       # ...that closes 4.5% under its high instead
        d.iloc[-1, [d.columns.get_loc(c) for c in ("open", "close")]] = px
        d.iloc[-1, d.columns.get_loc("high")], d.iloc[-1, d.columns.get_loc("low")] = px * 1.002, px * 0.995
        res = pl.select_picks("swing", bars, now=self.NOW, max_picks=1000)
        self.assertNotIn("S001", [p.symbol for p in res.picks])
        mine = next(b for b in pl.building_up(res) if b.symbol == "S001")
        self.assertTrue(mine.strong_trend)
        self.assertAlmostEqual(mine.gap_pct, 0.045, delta=0.002)
        self.assertEqual(pl.building_up(res, exclude={"S001"}) and [b.symbol for b in pl.building_up(res, exclude={"S001"})].count("S001"), 0)

    # ---- the message ----------------------------------------------------------------------------------------------------------
    def test_the_swing_message_shows_the_block_after_the_picks_and_before_your_list(self):
        names = [self._bu("AAA"), self._bu("BBB&CO", 77.0, False, 0.052, 8949.5, None)]
        msg = notify.format_scan_message(self.res, "swing", now=self.NOW, building=names, tracked_block="📒 <b>YOUR LIST</b>\n<b>WAITING TO BUY (1)</b> - buy when the price rises to:")
        block = next(b for b in msg.split("\n\n") if b.startswith("🔭"))
        self.assertEqual(block.split("\n"), [
            "🔭 <b>BUILDING UP</b> (watch only - no order yet)",
            "Strong stocks 3%-6% below their 50-day high. One joins the picks when it gets within 3% of that high.",
            "<b>AAA</b> 💪 · Quality 95/100 · 12-mo +53%",
            "3.4% below its 50-day high (1,740.00)",
            "<b>BBB&amp;CO</b> · Quality 77/100",
            "5.2% below its 50-day high (8,949.50)",
        ])
        self.assertLess(msg.index("PLACE THESE BUY ORDERS"), msg.index("🔭"))
        self.assertLess(msg.index("🔭"), msg.index("📒 <b>YOUR LIST</b>"))
        self.assertLess(msg.index("🔭"), msg.index("Trail = keep the stop"))
        chk = Messages._Check()
        chk.feed(msg)
        self.assertEqual((chk.err, chk.stack), ([], []))

    def test_on_a_day_with_no_new_pick_the_block_still_shows_and_nothing_claims_profit_marks(self):
        none = dataclasses.replace(self.res, picks=[], repeats=[])
        msg = notify.format_scan_message(none, "swing", now=self.NOW, building=self.building)
        self.assertLess(msg.index("NO NEW PICK"), msg.index("🔭"))
        self.assertNotIn("Profit marks", msg)                                           # no card carries them, and a watch name has none
        self.assertEqual(len([b for b in msg.split("\n\n") if b.startswith("🔭")]), 1)   # one piece for the splitter
        self.assertTrue(all(len(c) <= 4096 for c in notify.split_message(msg)))
        chk = Messages._Check()
        chk.feed(msg)
        self.assertEqual((chk.err, chk.stack), ([], []))

    def test_the_strong_tag_is_explained_when_only_a_watch_name_carries_it(self):
        none = dataclasses.replace(self.res, picks=[], repeats=[])
        self.assertEqual(notify.format_scan_message(none, "swing", now=self.NOW, building=[self._bu(strong=True)]).count(self.LEGEND), 1)
        self.assertNotIn("💪", notify.format_scan_message(none, "swing", now=self.NOW, building=[self._bu(strong=False)]))

    def test_it_shows_only_the_best_few_and_says_how_many_more(self):
        many = [self._bu(f"S{i:02d}", strong=False, gap=0.031 + i * 0.001) for i in range(8)]
        block = notify.format_building_up_block(many)
        self.assertEqual(block.count("<b>S"), config.BUILDING_UP_MAX)
        self.assertTrue(block.endswith(f"+{8 - config.BUILDING_UP_MAX} more building up (all of them are in the dashboard)"), block)
        self.assertNotIn("\n\n", block)
        self.assertNotIn("more building up", notify.format_building_up_block(many[:config.BUILDING_UP_MAX]))
        self.assertEqual(notify.format_building_up_block([]), "")

    def test_the_afternoon_btst_message_and_a_switched_off_list_carry_no_block(self):
        names = [self._bu()]
        self.assertNotIn("BUILDING UP", notify.format_scan_message(self.res, "btst", now=at(2026, 10, 13, 14, 45), building=names))
        self.assertNotIn("BUILDING UP", notify.format_scan_message(self.res, "swing", now=self.NOW))                     # none given
        with mock.patch.object(config, "BUILDING_UP_MAX", 0):
            self.assertNotIn("BUILDING UP", notify.format_scan_message(self.res, "swing", now=self.NOW, building=names))

    # ---- the files ----------------------------------------------------------------------------------------------------------------
    def test_the_summary_lists_every_name_and_the_dashboard_file_is_written_for_swing_only(self):
        from reports import summary
        many = [self._bu(f"S{i:02d}", strong=bool(i % 2), gap=0.031 + i * 0.001, ret12m=None if i == 3 else 0.2) for i in range(8)]
        text = summary.render_summary("swing", self.res, self.NOW, building=many)
        self.assertIn("**Building up, near a breakout** (watch only - no order yet)", text)
        for b in many:
            self.assertIn(f"| **{b.symbol}**", text)                                       # all eight, not only the five the message shows
        self.assertIn("| n/a |", text)                                                      # a blank 12-month return is not a made-up number
        self.assertNotIn("Building up", summary.render_summary("btst", self.res, self.NOW, building=many))
        self.assertNotIn("Building up", summary.render_summary("swing", self.res, self.NOW))
        tmp, cwd = tempfile.mkdtemp(), os.getcwd()
        os.chdir(tmp)
        try:
            paths = summary.write_scan_outputs("swing", self.res, self.NOW, building=many)
            df = pd.read_csv(paths["building"])
            self.assertEqual(paths["building"], "reports/output/building_up_swing.csv")
            self.assertEqual(list(df.columns), pl.BUILDING_UP_COLUMNS)
            self.assertEqual(list(df["symbol"]), [b.symbol for b in many])
            paths = summary.write_scan_outputs("swing", self.res, self.NOW, building=[])
            self.assertEqual(len(pd.read_csv(paths["building"])), 0)                        # an empty file: yesterday's names never linger
            os.remove(paths["building"])
            paths = summary.write_scan_outputs("btst", self.res, self.NOW, building=many)
            self.assertNotIn("building", paths)
            self.assertFalse(os.path.exists("reports/output/building_up_swing.csv"))
        finally:
            os.chdir(cwd)
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
        msg = notify.format_scan_message(res, "swing", now=at(2026, 10, 13, 17, 0), market_line="🧭 <b>MARKET:</b> ⚪ <b>Unclear</b>")
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

    def test_prices_are_shown_exactly_as_the_plan_uses_them(self):
        self.assertEqual(pl.fmt_price(1626.65), "1,626.65")
        self.assertEqual(pl.fmt_price(99.05), "99.05")
        self.assertEqual(pl.fmt_price(float("nan")), "-")

    def test_scrub_masks_secrets(self):
        self.assertEqual(notify.scrub("url https://x/botABC123SECRET/send", "ABC123SECRET"), "url https://x/bot***/send")

    # ---- the compact message: short, to the point, and no money amounts the scan cannot know -------------------------
    @staticmethod
    def _swing():
        return pl.select_picks("swing", make_market(), now=at(2026, 10, 13, 17, 0), max_picks=15)

    @staticmethod
    def _live():
        live = {s: d.copy() for s, d in make_market().items()}
        d = live["PLANT"]
        now = at(2026, 10, 13, 14, 45)                                              # the afternoon BTST run starts at 2:45 PM
        d.iloc[-1, d.columns.get_loc("volume")] *= session.volume_fraction(now)    # about 77% of the day's volume so far
        return pl.select_picks("btst", live, now=now)

    def _all_messages(self):
        return [(self._swing(), "swing", at(2026, 10, 13, 17, 0)), (self._live(), "btst", at(2026, 10, 13, 14, 45))]

    def test_no_share_counts_or_money_amounts_anywhere(self):
        for res, mode, now in self._all_messages():
            self.assertTrue(res.picks)
            msg = notify.format_scan_message(res, mode, now=now, scoreboard_line="Record: nothing tracked yet")
            low = msg.lower()
            for bad in ("share", "lakh", "crore", "₹", "position cap", "reference level", "risks ≈", "capital of"):
                self.assertNotIn(bad, low, bad)

    def test_a_pick_card_is_a_few_short_lines_with_the_plan_numbers(self):
        for res, _, _ in self._all_messages():
            for i, p in enumerate(res.picks, 1):
                card = notify.format_pick(p, i)
                lines = card.split("\n")
                warnings = [ln for ln in lines if ln.startswith("⚠️")]
                self.assertTrue(3 <= len(lines) <= 7, card)
                self.assertLessEqual(len(card), 380 + 110 * len(warnings), card)
                self.assertIn(pl.fmt_price(p.entry_price), card)
                self.assertIn(pl.fmt_price(p.stop), card)
                self.assertIn(f"trail {p.trail_pct:.1%}", card)
                self.assertIn(p.stage_label, lines[0])                              # the full setup line, e.g. "About to break out (2.1% below the 50-day high)"
                if p.entry_mode == "close":
                    self.assertIn("Buy near", card)
                    self.assertIn("today on", card)                                  # the breakout facts sit on the buy line
                else:
                    self.assertIn(f"Buy at <b>{pl.fmt_price(p.entry_price)}</b> · skip if it opens above {pl.fmt_price(p.max_entry)}", card)
                    self.assertNotIn("Buy-stop", card)                               # the Stop line right below it is the stop-loss
                self.assertNotIn("\n\n", card)                                      # the splitter keeps a card whole

    def test_every_card_shows_its_profit_marks_under_the_stop(self):
        # added 2026-11-02 at the user's request: the price where a trade is up 1x, 2x, 3x the amount it risks. Reference points: there is no fixed target.
        for res, _, _ in self._all_messages():
            for i, p in enumerate(res.picks, 1):
                lines = notify.format_pick(p, i).split("\n")
                stop = next(n for n, ln in enumerate(lines) if ln.startswith("Stop "))
                self.assertEqual(lines[stop + 1], f"Profit marks {pl.fmt_price(p.r1)} · {pl.fmt_price(p.r2)} · {pl.fmt_price(p.r3)}")
                self.assertLess(p.entry_price, p.r1)
                self.assertLess(p.r1, p.r2)
                self.assertLess(p.r2, p.r3)
                self.assertEqual((p.r1, p.r2, p.r3), pl.profit_marks(p.entry_price, p.risk_pct))
                self.assertAlmostEqual(p.r1 - p.entry_price, p.entry_price - p.stop, delta=0.11)       # one "R" above the entry, to the tick

    def test_profit_marks_on_real_numbers_and_no_line_when_they_are_unknown(self):
        # the plan numbers stored in the ledger for two real waiting orders (GLAND picked 29 Oct, ZYDUSLIFE picked 30 Oct 2026)
        self.assertEqual(pl.profit_marks(3177.35, 2.5 * 0.034273201116984335), (3449.60, 3721.85, 3994.10))
        self.assertEqual(pl.profit_marks(1251.4, 2.5 * 0.02783225222507167), (1338.50, 1425.55, 1512.65))
        self.assertEqual(notify._marks_line((3449.6, 3721.85, 3994.1)), "Profit marks 3,449.60 · 3,721.85 · 3,994.10")
        for bad in (None, (), (1.0, 2.0), (float("nan"), 2.0, 3.0), (0.0, 1.0, 2.0), ("x", 1.0, 2.0)):
            self.assertEqual(notify._marks_line(bad), "")                               # unknown or unusable: no line, never a made-up number

    def test_the_footer_explains_profit_marks_only_when_a_card_carries_them(self):
        explain = "Profit marks = the price where you would be up 1x, 2x or 3x the amount you risk (reference points only)."
        res, now = self._swing(), at(2026, 10, 13, 17, 0)
        self.assertEqual(notify.format_scan_message(res, "swing", now=now).count(explain), 1)
        none = dataclasses.replace(res, picks=[], repeats=[])
        self.assertNotIn("Profit marks", notify.format_scan_message(none, "swing", now=now))                       # no card: nothing to explain
        e = pl.EarlierPick("ZZZ", "primed", 88.0, False, "2026-10-12", 123.4, 2, (130.0, 137.0, 144.0))
        msg = notify.format_scan_message(none, "swing", now=now, earlier=[e])
        self.assertEqual(msg.count(explain), 1)                                                                     # an earlier pick's marks need it too
        self.assertIn("Buy at <b>123.40</b> · order valid 2 more sessions\nProfit marks 130.00 · 137.00 · 144.00", msg)

    def test_the_buy_orders_heading_is_plain_and_still_says_how_to_place_them(self):
        # the cards say "Buy at"; this heading is what tells a reader to enter them as GTT / stop-limit orders that trigger on the way UP, not as
        # plain limit orders (those would fill at once, below the trigger). Reworded 2026-11-01: "BUY-STOP ORDERS" was jargon to the user.
        msg = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 13, 17, 0))
        heading = f"<b>PLACE THESE BUY ORDERS</b> (valid {config.ENTRY_VALID_SESSIONS} sessions)"
        self.assertIn(heading + '\nSet each as a GTT / stop-limit order: it buys only if the price rises to its "Buy at" price.', msg)
        block = next(b for b in msg.split("\n\n") if b.startswith(heading))
        self.assertEqual(len(block.split("\n")), 2)                                  # one block, no blank line inside: the splitter keeps it whole
        self.assertLess(msg.index(heading), msg.index("<b>1. "))                     # above the first card
        self.assertNotIn("BUY-STOP", msg)

    def test_the_buy_now_heading_is_plain(self):
        msg = notify.format_scan_message(self._live(), "btst", now=at(2026, 10, 13, 14, 45))
        self.assertIn("<b>BUY NOW</b> (near the close, only if the price is still above its breakout price)", msg)
        self.assertNotIn("holds the breakout level", msg)

    def test_no_message_uses_the_buy_stop_jargon(self):
        e = pl.EarlierPick("ZZZ", "primed", 88.0, True, "2026-10-12", 123.4, 2)
        tracked = notify.format_tracked_block([{"kind": "closed", "symbol": "CCC", "reason": "stop", "price": 90.0, "R": -1.0, "net": -0.06}],
                                              [{"symbol": "AAA", "status": "OPEN", "trail": 95.0}, {"symbol": "BBB", "status": "PENDING", "trigger": 50.0}])
        for res, mode, now in self._all_messages():
            msg = notify.format_scan_message(res, mode, now=now, earlier=[e], tracked_block=tracked)
            low = msg.lower()
            for jargon in ("buy-stop", "current stops", "stopped out", "raise stop "):
                self.assertNotIn(jargon, low, jargon)

    def test_the_written_summary_uses_the_same_plain_words(self):
        from reports import summary
        text = summary.render_summary("swing", self._swing(), at(2026, 10, 13, 17, 0), detail=True)
        self.assertIn("\n| # |", text)                                                # the dashboard cuts the file at the table's first row
        self.assertIn("| # | Stock | Setup | Quality | Buy | Stop-loss | Trailing stop-loss (% below highest close) |", text)
        self.assertIn("(GTT / stop-limit buy order; skip if it opens above", text)
        for plain in ("- **Buy:**", "- **Stop-loss:**", "- **Profit marks:**"):
            self.assertIn(plain, text)
        for jargon in ("buy-stop", "Trail below high close", "**Entry:**", "- **Stop:**", "Reference levels", "Reference prices", "stop-outs"):
            self.assertNotIn(jargon, text, jargon)

    def test_the_message_stays_short(self):
        for res, mode, now in self._all_messages():
            msg = notify.format_scan_message(res, mode, now=now)
            self.assertLessEqual(len(msg), 1250 + 350 * len(res.picks), len(msg))
            self.assertEqual(len(notify.split_message(msg)), 1)

    def test_no_new_pick_is_one_short_line_that_says_why(self):
        base = self._swing()
        none = dataclasses.replace(base, picks=[], repeats=[])
        msg = notify.format_scan_message(none, "swing", now=at(2026, 10, 13, 17, 0))
        self.assertIn("<b>NO NEW PICK.</b> ", msg)
        self.assertLessEqual(len(msg), 900, msg)
        again = dataclasses.replace(base, picks=[], repeats=[("A", "primed")] * 3,
                                    funnel={**base.funnel, "passed_quality": 3, "repeats": 3})
        self.assertIn("all already on your list", notify.format_scan_message(again, "swing", now=at(2026, 10, 13, 17, 0)))
        empty = dataclasses.replace(none, funnel={**base.funnel, "loaded": 0})
        self.assertIn("No price data was loaded", notify.format_scan_message(empty, "swing", now=at(2026, 10, 13, 17, 0)))

    def test_title_says_what_the_prices_are_based_on(self):
        swing = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 13, 17, 0)).split("\n")[0]
        self.assertEqual(swing, "📈 <b>SWING SCAN</b> · Tue 13 Oct · today's close")
        later = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 17, 11, 0)).split("\n")[0]
        self.assertEqual(later, "📈 <b>SWING SCAN</b> · Sat 17 Oct · close of Tue 13 Oct")
        live = notify.format_scan_message(self._live(), "btst", now=at(2026, 10, 13, 14, 1)).split("\n")[0]
        self.assertEqual(live, "🌙 <b>BTST SCAN</b> · Tue 13 Oct, 2:01 PM · live prices")

    # ---- every heading is bold AND in capitals (2026-11-06, at the user's request: they got lost among the bold stock names) -----------------------
    def _bold_in_ordinary_letters(self, text: str) -> list[str]:
        """Every bold span with a lower-case letter in it (entities such as &amp; unescaped first), except the values that are meant to read as words:
        the market's verdict and the weak-market warning. Prices, symbols and numbers have no lower-case letters, so they pass."""
        import html as html_lib
        import re
        from scanner import market_direction as md_mod
        values = set(md_mod.STANCES) | {"weak market: half size"}
        spans = (html_lib.unescape(s) for s in re.findall(r"<b>(.*?)</b>", text, flags=re.S))
        return [s for s in spans if re.search(r"[a-z]", s) and s not in values]

    def test_every_heading_is_bold_and_in_capitals(self):
        from scanner import market_direction as md_mod
        today, now = date(2026, 10, 13), at(2026, 10, 13, 17, 0)
        md = types.SimpleNamespace(emoji="🟢", label="Bullish", today=today, nifty=types.SimpleNamespace(day_change=0.017, as_of=today),
                                   factors=[types.SimpleNamespace(key="global", shown="hostile")], breadth=None)
        market = md_mod.format_direction_line(md)
        self.assertTrue(market.startswith("🧭 <b>MARKET:</b> 🟢 <b>Bullish</b> · Nifty +1.7% today"), market)
        res = dataclasses.replace(self._swing(), regime="weak", breadth={"breadth50": 0.18, "n": 400})
        earlier = [pl.EarlierPick("ZZZ", "primed", 88.0, True, "2026-10-12", 123.4, 2, (130.0, 137.0, 144.0))]
        building = [pl.BuildingUp("AAA", 95.4, True, 0.034, 1740.0, 1680.84, 0.032, 0.53), pl.BuildingUp("BBB&CO", 77.0, False, 0.052, 8949.5, 8484.0, 0.012, None)]
        tracked = notify.format_tracked_block(
            [{"kind": "filled", "symbol": "BBB", "price": 50.0}, {"kind": "closed", "symbol": "CCC", "reason": "stop", "price": 90.0, "R": -1.0, "net": -0.06}],
            [{"symbol": "AAA", "status": "OPEN", "trail": 105.0}, {"symbol": "EEE", "status": "PENDING", "trigger": 200.0}], snapshot=True)
        evening = notify.format_scan_message(res, "swing", now=now, market_line=market, earlier=earlier, building=building, tracked_block=tracked,
                                             scoreboard_line="Record since 21 Oct: 1 sold", list_load=2.0)
        no_pick = notify.format_scan_message(dataclasses.replace(self._swing(), picks=[], repeats=[]), "swing", now=now, earlier=earlier, building=building)
        afternoon = notify.format_scan_message(self._live(), "btst", now=at(2026, 10, 13, 14, 45), earlier=earlier)
        wanted = {
            "evening": (evening, ("SWING SCAN", "MARKET:", "BREADTH", "EARLIER PICKS, STILL STRONG", "NEW · PLACE THESE BUY ORDERS", "BUILDING UP", "YOUR LIST",
                                  "BOUGHT, NOT SOLD YET (1)", "WAITING TO BUY (1)")),
            "no pick": (no_pick, ("SWING SCAN", "NO NEW PICK.", "EARLIER PICKS, STILL STRONG", "BUILDING UP")),
            "afternoon": (afternoon, ("BTST SCAN", "EARLIER PICKS, STILL STRONG", "NEW · BUY NOW")),
        }
        for name, (msg, headings) in wanted.items():
            for heading in headings:
                self.assertIn(f"<b>{heading}</b>", msg, (name, heading))
            self.assertEqual(self._bold_in_ordinary_letters(msg), [], name)          # nothing bold is left in ordinary letters
            chk = self._Check()
            chk.feed(msg)
            self.assertEqual((chk.err, chk.stack), ([], []), name)
            self.assertTrue(all(len(c) <= 4096 for c in notify.split_message(msg)), name)
        # the old style is gone, and the words that explain a heading stay in ordinary letters after it
        for old in ("<b>Your list", "<b>Bought, not sold", "<b>Waiting to buy", "<b>Market:", "<b>Swing scan", "<b>BTST scan", "<b>EARLIER PICKS, still", "Breadth 18%"):
            self.assertNotIn(old, evening, old)
        self.assertIn("<b>BOUGHT, NOT SOLD YET (1)</b> - sell if the price falls to:\nAAA 105.00", evening)
        self.assertIn("<b>WAITING TO BUY (1)</b> - buy when the price rises to:\nEEE 200.00", evening)
        # a blank line sets each heading of the list apart from the lines above it (the splitter may then break the list between its parts, never inside one)
        self.assertIn("(buy order triggered)\n• <b>CCC</b>", tracked)
        self.assertIn("after costs)\n\n<b>BOUGHT, NOT SOLD YET (1)</b>", tracked)
        self.assertIn("AAA 105.00\n\n<b>WAITING TO BUY (1)</b>", tracked)
        self.assertEqual(notify.format_tracked_block([], [{"symbol": "EEE", "status": "PENDING", "trigger": 200.0}], snapshot=True),
                         "📒 <b>YOUR LIST</b>\n<b>WAITING TO BUY (1)</b> - buy when the price rises to:\nEEE 200.00")       # nothing above it: no blank line
        # a test that fails on a heading written the old way must itself see one
        self.assertEqual(self._bold_in_ordinary_letters("<b>Your list</b> and <b>AAA</b> and <b>1,969.25</b> and <b>BBB&amp;CO</b> and <b>weak market: half size</b>"),
                         ["Your list"])

    def test_the_market_and_options_messages_have_bold_capital_headings_too(self):
        from scanner import market_direction as md_mod
        today = date(2026, 10, 13)
        md = types.SimpleNamespace(emoji="🟡", label="Sideways / mixed", today=today, nifty=None, bank=None, factors=[], score=0, n_factors=0,
                                   stance=md_mod.STANCES["Sideways / mixed"], global_snapshot={},
                                   breadth={"pct_above_50": 0.46, "n": 500, "advancers": 200, "decliners": 300})
        msg = md_mod.format_direction_message(md)
        for heading in ("MARKET DIRECTION", "NIFTY 50", "GLOBAL CUES", "BREADTH"):
            self.assertIn(f"<b>{heading}</b>", msg)
        self.assertEqual(self._bold_in_ordinary_letters(msg), [])
        plan = types.SimpleNamespace(index="BANKNIFTY", direction="BUY CE", suggested_strike=52000, strike_type="ATM", spot_entry=51950, spot_stop=51700,
                                     spot_targets=[52100, 52250], execution=types.SimpleNamespace(entry_window_ist="9:30-10:30"))
        opts = notify.format_options_message([plan])
        self.assertIn("📈 <b>INDEX OPTIONS SCAN</b> — 1 setup(s)", opts)
        self.assertIn("\n<b>BANKNIFTY</b>\n", opts)
        self.assertEqual(self._bold_in_ordinary_letters(opts), [])
        self.assertEqual(notify.format_options_message([]), "📈 <b>INDEX OPTIONS SCAN COMPLETE</b> — no setups today.")

    def test_the_weak_market_rule_is_stated_once_in_relative_terms(self):
        res = dataclasses.replace(self._swing(), regime="weak", breadth={"breadth50": 0.18, "n": 400})
        msg = notify.format_scan_message(res, "swing", now=at(2026, 10, 13, 17, 0))
        self.assertEqual(msg.count("half size"), 1)
        self.assertIn("📊 <b>BREADTH</b> 18% (stocks above their 50-day average) - <b>weak market: half size</b>", msg)
        self.assertIn("half in a weak market", msg)                                  # the footer says it as a rule

    def test_the_footer_says_when_the_whole_list_needs_more_than_the_capital(self):
        # added 2026-11-05: 24 bought + 18 waiting stocks need about 300% of one account at the 1% rule the footer states, and nothing said so
        res, now = self._swing(), at(2026, 10, 13, 17, 0)
        note = "Your whole list now needs about"
        for quiet in (None, 0.0, 0.8, 1.0, float("nan"), float("inf")):
            self.assertNotIn(note, notify.format_scan_message(res, "swing", now=now, list_load=quiet), quiet)
        msg = notify.format_scan_message(res, "swing", now=now, list_load=3.04)
        self.assertIn("Risk at most 1% of your capital per trade, half in a weak market. Your whole list now needs about 300% of your capital at that rule, "
                      "so it cannot all be held - choose which to hold (dashboard: Capital plan). A ranked shortlist, not a guarantee.</i>", msg)
        self.assertEqual(msg.count(note), 1)
        self.assertIn("about 170% of your capital", notify.format_scan_message(res, "swing", now=now, list_load=1.66))     # rounded to the nearest 10
        low = msg.lower()
        for bad in ("share", "lakh", "crore", "₹", "position cap", "reference level", "risks ≈", "capital of"):             # still no money amounts
            self.assertNotIn(bad, low, bad)
        chk = self._Check()
        chk.feed(msg)
        self.assertEqual((chk.err, chk.stack), ([], []))
        self.assertLess(len(msg) - len(notify.format_scan_message(res, "swing", now=now)), 160)      # one short sentence, nothing more
        self.assertIn(note, notify.format_scan_message(self._live(), "btst", now=at(2026, 10, 13, 14, 45), list_load=2.0))   # the afternoon message too

    def test_tracked_block_lists_stops_only_in_the_evening_message(self):
        events = [{"kind": "trail_raised", "symbol": "AAA", "old": 100.0, "new": 105.0},
                  {"kind": "filled", "symbol": "BBB", "price": 50.0},
                  {"kind": "closed", "symbol": "CCC", "reason": "stop", "price": 90.0, "R": -1.0, "net": -0.06},
                  {"kind": "unfilled", "symbol": "DDD", "reason": "expired"}]
        rows = [{"symbol": "AAA", "status": "OPEN", "trail": 105.0}, {"symbol": "BBB", "status": "OPEN", "trail": 45.5},
                {"symbol": "EEE", "status": "PENDING", "trigger": 200.0}]
        evening = notify.format_tracked_block(events, rows, snapshot=True)
        self.assertIn("<b>BOUGHT, NOT SOLD YET (2)</b> - sell if the price falls to:", evening)
        self.assertIn("AAA 105.00↑", evening)
        self.assertIn("BBB 45.50", evening)
        self.assertNotIn("BBB 45.50↑", evening)
        self.assertIn("↑ = moved up today", evening)
        self.assertNotIn("raise your stop-loss", evening)                            # the arrow replaces the separate line
        self.assertIn("<b>WAITING TO BUY (1)</b> - buy when the price rises to:\nEEE 200.00", evening)     # each waiting stock with its buy price
        self.assertIn("BBB</b> - bought at 50.00 (buy order triggered)", evening)
        self.assertIn("CCC</b> - sold at 90.00: stop-loss hit (-1.0R, -6.0% after costs)", evening)
        self.assertIn("DDD</b> - buy order expired; no trade", evening)
        for old_words in ("Open - current stops", "Buy-stops still waiting", "buy-stop filled", "stopped out", "raise stop "):
            self.assertNotIn(old_words, evening)                                     # the jargon the user found confusing is gone
        afternoon = notify.format_tracked_block(events, rows, snapshot=False)
        self.assertNotIn("sell if the price falls to", afternoon)
        self.assertNotIn("WAITING TO BUY", afternoon)
        self.assertNotIn("moved up today", afternoon)
        self.assertIn("AAA</b> - raise your stop-loss 100.00 → <b>105.00</b>", afternoon)      # nothing is lost when there is no list to carry the arrow
        self.assertEqual(notify.format_tracked_block([], rows, snapshot=False), "")
        self.assertEqual(notify.format_tracked_block([], [], snapshot=True), "")
        for block in (evening, afternoon):
            chk = self._Check()
            chk.feed(block)
            self.assertEqual((chk.err, chk.stack), ([], []))

    def test_a_long_list_is_capped_not_dumped(self):
        rows = [{"symbol": f"S{i:02d}", "status": "OPEN", "trail": 10.0 + i} for i in range(30)]
        rows += [{"symbol": f"W{i:02d}", "status": "PENDING", "trigger": 5.0 + i} for i in range(28)]
        block = notify.format_tracked_block([], rows, snapshot=True)
        self.assertIn("BOUGHT, NOT SOLD YET (30)", block)
        self.assertIn("...and 6 more in the dashboard", block)
        self.assertIn("WAITING TO BUY (28)", block)
        self.assertIn("...and 4 more in the dashboard", block)
        self.assertIn("W00 5.00 · W01 6.00\nW02 7.00 · W03 8.00", block)                # two to a line, like the stop-loss list
        self.assertIn("W23 28.00", block)
        self.assertNotIn("W24", block)

    def test_the_waiting_list_shows_each_buy_price_and_never_a_made_up_one(self):
        rows = [{"symbol": "AAA", "status": "PENDING", "trigger": 1251.4}, {"symbol": "BBB", "status": "PENDING", "trigger": 98.05},
                {"symbol": "CCC", "status": "PENDING", "trigger": float("nan")}, {"symbol": "DDD", "status": "PENDING", "trigger": None},
                {"symbol": "EEE", "status": "PENDING", "trigger": 0.0}]
        block = notify.format_tracked_block([], rows, snapshot=True)
        self.assertIn("AAA 1,251.40 · BBB 98.05\nCCC · DDD\nEEE", block)             # a missing or unusable price is left out, not shown as nan / 0 / -
        self.assertNotIn("nan", block.lower())
        self.assertNotIn("None", block)

    def test_a_formatting_slip_still_sends_a_plain_notice(self):
        sent = []

        def post(url, json=None, timeout=None, **kw):
            sent.append(json["text"])
            return mock.Mock(ok=True, status_code=200, text="ok")

        with mock.patch.object(notify, "format_scan_message", side_effect=RuntimeError("boom")), mock.patch("requests.post", post):
            logging.disable(logging.CRITICAL)
            try:
                ok = notify.notify_scan(self._swing(), "swing", "123456:TESTTOKENVALUE", "1", now=at(2026, 10, 13, 17, 0))
            finally:
                logging.disable(logging.NOTSET)
        self.assertTrue(ok)
        self.assertEqual(len(sent), 1)
        self.assertIn("SWING scan finished, but its message could not be formatted", sent[0])
        self.assertNotIn("boom", sent[0])

    def test_short_phrases_cover_every_feature(self):
        self.assertEqual(set(fp.FEATURE_SHORT), set(fp.FEATURE_TEXT))
        for name, phrase in fp.FEATURE_SHORT.items():
            self.assertTrue(0 < len(phrase) <= 26, name)

    def test_scoreboard_line_is_one_short_honest_line(self):
        cols = ["status", "net", "R", "signal_date", "scan_mode", "setup", "exit_n", "fill_n"]
        self.assertTrue(outcomes.format_scoreboard_line(pd.DataFrame(columns=cols)).startswith("Record: nothing tracked"))
        waiting = pd.DataFrame({"status": ["OPEN", "PENDING"], "net": [np.nan] * 2, "R": [np.nan] * 2, "signal_date": ["2026-10-21"] * 2,
                                "scan_mode": ["swing"] * 2, "setup": ["primed"] * 2, "exit_n": [-1, -1], "fill_n": [1, -1]})
        self.assertEqual(outcomes.format_scoreboard_line(waiting), "Record since 21 Oct: no trade sold yet")
        two = pd.DataFrame({"status": ["CLOSED", "CLOSED", "OPEN"], "net": [0.10, -0.05, np.nan], "R": [2.0, -1.0, np.nan],
                            "signal_date": ["2026-10-21"] * 3, "scan_mode": ["swing"] * 3, "setup": ["primed"] * 3,
                            "exit_n": [10, 5, -1], "fill_n": [1, 1, 1]})
        line = outcomes.format_scoreboard_line(two)
        self.assertEqual(line, "Record since 21 Oct: 2 sold, 50% won, average +0.50R (+2.5% per trade after costs) - too few to judge yet (need 30+)")

    def test_the_record_also_says_how_the_trades_still_held_stand_and_that_it_is_not_final(self):
        """Two lines: the record of SOLD trades, then 'Open now' for the ones still held (marked at the last close, after costs). The second line is
        left out when nothing held has a price yet, so the sold-trade line is exactly what it was."""
        def frame(rows):
            return pd.DataFrame([{"status": s, "net": n, "R": r, "signal_date": "2026-10-21", "scan_mode": m, "setup": "primed", "exit_n": -1, "fill_n": 1}
                                 for s, n, r, m in rows])
        held = frame([("OPEN", 0.10, 2.0, "btst"), ("OPEN", 0.02, 0.4, "swing"), ("OPEN", -0.03, -0.6, "btst"), ("OPEN", -0.01, -0.2, "swing"),
                      ("PENDING", np.nan, np.nan, "swing")])
        self.assertEqual(outcomes.format_scoreboard_line(held),
                         "Record since 21 Oct: no trade sold yet\nOpen now: 2 of 4 up, average +2.0% after costs (not final)")
        o = outcomes.open_now(held)
        self.assertEqual((o["n"], o["up"]), (4, 2))
        self.assertAlmostEqual(o["avg_net"], 0.02)
        self.assertAlmostEqual(o["median_net"], 0.005)
        self.assertAlmostEqual(o["avg_R"], 0.4)
        only_btst = outcomes.open_now(held, scan_mode="btst")                                    # the BTST ones alone: +10% and -3%
        self.assertEqual((only_btst["n"], only_btst["up"]), (2, 1))
        self.assertAlmostEqual(only_btst["avg_net"], 0.035)
        # with sold trades as well, both lines; a trade that is exactly flat after costs is not "up"
        both = pd.concat([frame([("CLOSED", 0.10, 2.0, "swing"), ("CLOSED", -0.05, -1.0, "swing"), ("OPEN", 0.0, 0.0, "swing")])], ignore_index=True)
        text = outcomes.format_scoreboard_line(both)
        self.assertEqual(text.splitlines()[0], "Record since 21 Oct: 2 sold, 50% won, average +0.50R (+2.5% per trade after costs) - too few to judge yet (need 30+)")
        self.assertEqual(text.splitlines()[1], "Open now: 0 of 1 up, average +0.0% after costs (not final)")
        # nothing held has a price: no second line
        self.assertEqual(outcomes.format_open_line(frame([("PENDING", np.nan, np.nan, "swing")])), "")
        self.assertEqual(outcomes.open_now(pd.DataFrame(columns=["status", "net", "R", "scan_mode"])), {"n": 0})
        # and the footer of the Telegram message carries both lines inside the italic record
        res = pl.select_picks("swing", make_market(), now=at(2026, 10, 13, 17, 0))
        msg = notify.format_scan_message(res, "swing", now=at(2026, 10, 13, 17, 0), scoreboard_line=outcomes.format_scoreboard_line(held))
        self.assertIn("🧾 <i>Record since 21 Oct: no trade sold yet\nOpen now: 2 of 4 up, average +2.0% after costs (not final)</i>", msg)


# --------------------------------------------------------------------------- #
@unittest.skipUnless(importlib.util.find_spec("streamlit"), "streamlit is not installed")
class Dashboard(unittest.TestCase):
    """app.py run headless with Streamlit's own test runner on small files: it must not crash, and what it shows is in plain words (2026-11-01)."""

    JARGON = ("buy-stop", "Buy-stop", "Current stop", "Mark-to-market", "Open - keep", "Waiting for their", "Sessions left",
              "plus_1R", "+1R price", "entry_mode", "trail_pct", "Avg after costs")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        os.makedirs("tracking")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    @staticmethod
    def _run():
        from streamlit.testing.v1 import AppTest
        logging.disable(logging.CRITICAL)
        try:
            return AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=90).run()
        finally:
            logging.disable(logging.NOTSET)

    @staticmethod
    def _write_files():
        from reports import summary
        res = pl.select_picks("swing", make_market(), now=at(2026, 10, 13, 17, 0), max_picks=1000)
        summary.picks_frame(res.picks).to_csv("scan_results_swing.csv", index=False)
        base = {c: "" for c in outcomes.COLUMNS}
        common = {"scan_mode": "swing", "regime": "weak"}
        rows = [
            {**base, **common, "trade_id": "SOLD", "symbol": "SOLD", "setup": "primed", "entry_mode": "stop", "signal_date": "2026-10-20", "quality_pct": 70,
             "status": "CLOSED", "trigger": 100.0, "fill_date": "2026-10-21", "fill_px": 100.0, "exit_date": "2026-11-02", "exit_px": 94.0,
             "exit_reason": "trail", "net": 0.05, "R": 1.0, "peak_R": 1.5, "fill_n": 1, "exit_n": 9},
            {**base, **common, "trade_id": "HELD", "symbol": "HELD", "setup": "fresh", "entry_mode": "close", "signal_date": "2026-10-28", "quality_pct": 80,
             "status": "OPEN", "fill_date": "2026-10-29", "fill_px": 100.0, "trail": 92.5, "last_close": 104.0, "net": 0.03, "R": 0.5, "peak_R": 0.8,
             "init_stop": 92.5, "atr_pct": 0.03, "stop_mult": 2.5, "trail_mult": 5.0, "hc": 105.0},
            {**base, **common, "trade_id": "WAITING", "symbol": "WAITING", "setup": "primed", "entry_mode": "stop", "signal_date": "2026-10-30", "quality_pct": 75,
             "status": "PENDING", "trigger": 250.5, "sessions": 1, "valid_sessions": 5, "atr_pct": 0.02, "stop_mult": 2.5},
            {**base, **common, "trade_id": "NEVER", "symbol": "NEVER", "setup": "primed", "entry_mode": "stop", "signal_date": "2026-10-20", "quality_pct": 60,
             "status": "UNFILLED", "trigger": 80.0, "exit_reason": "expired"},
        ]
        pd.DataFrame(rows, columns=outcomes.COLUMNS).to_csv("tracking/trades.csv", index=False)

    @staticmethod
    def _everything_shown(page):
        """Every label, heading, caption, message and column name on the page, as one text."""
        parts = [t.label for t in page.tabs] + [m.label for m in page.metric] + [s.value for s in page.subheader] + [c.value for c in page.caption]
        parts += [i.value for i in page.info] + [w.value for w in page.warning]
        for df in page.dataframe:
            parts += [str(c) for c in df.value.columns]
        return "\n".join(parts)

    def test_it_opens_and_every_heading_is_in_plain_words(self):
        self._write_files()
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertEqual([t.label for t in page.tabs], ["🌙 BTST", "📈 Swing", "📌 Your list", "📒 Scoreboard"])
        subheaders = [s.value for s in page.subheader]
        self.assertIn("Bought, not sold yet - keep your stop-loss updated", subheaders)
        self.assertIn("Waiting to buy - buy orders that have not triggered yet", subheaders)
        labels = [m.label for m in page.metric]
        for plain in ("Bought, not sold yet", "Waiting to buy", "Sold (finished)", "Never bought", "Buy now / buy orders"):
            self.assertIn(plain, labels)
        shown = self._everything_shown(page)
        for jargon in self.JARGON:
            self.assertNotIn(jargon, shown, jargon)

    def test_the_tables_have_readable_columns_and_the_buy_price_next_to_each_waiting_stock(self):
        self._write_files()
        page = self._run()
        tables = [df.value for df in page.dataframe]

        def table_with(column):
            return next(t for t in tables if column in list(t.columns))

        waiting = table_with("Order valid for (more sessions)")
        self.assertEqual(list(waiting["Stock"]), ["WAITING"])
        self.assertAlmostEqual(float(waiting["Buy at"].iloc[0]), 250.5)
        self.assertEqual(int(waiting["Order valid for (more sessions)"].iloc[0]), 4)
        holding = table_with("Sell if price falls to")
        self.assertEqual(list(holding["Stock"]), ["HELD"])
        self.assertAlmostEqual(float(holding["Sell if price falls to"].iloc[0]), 92.5)
        self.assertLessEqual({"Highest close so far", "Stop starts rising above"}, set(holding.columns))      # why a stop may not have moved yet
        self.assertAlmostEqual(float(holding["Highest close so far"].iloc[0]), 105.0)
        self.assertAlmostEqual(float(holding["Stop starts rising above"].iloc[0]), 92.5 / (1 - 5 * 0.03), places=6)   # the trail passes the first stop above 108.82
        picks = table_with("Skip if it opens above")
        self.assertLessEqual({"Stock", "Buy at", "Stop-loss", "How to buy", "Trailing stop-loss (% below highest close)", "Profit mark 1"}, set(picks.columns))
        self.assertEqual(set(picks["How to buy"]), {"Place buy order"})
        trades = table_with("Why it ended")
        self.assertEqual(set(trades["Status"]), {"Sold", "Bought, not sold yet", "Waiting to buy", "Never bought"})
        self.assertEqual(set(trades["Why it ended"].dropna()), {"Trailing stop-loss hit", "Buy order expired"})
        stats = table_with("Average gain/loss (after costs)")
        self.assertLessEqual({"Win rate", "Average R", "Typical trade (median)", "Profit factor"}, set(stats.columns))

    def test_the_watch_list_has_its_own_plain_table_and_shows_even_when_there_is_no_new_pick(self):
        self._write_files()
        os.makedirs("reports/output")
        names = [pl.BuildingUp("AAA", 95.4, True, 0.034, 1740.0, 1680.84, 0.032, 0.53), pl.BuildingUp("BBB", 77.0, False, 0.052, 8949.5, 8484.0, 0.012, None)]
        pd.DataFrame([b.to_row() for b in names], columns=pl.BUILDING_UP_COLUMNS).to_csv("reports/output/building_up_swing.csv", index=False)
        heading = "🔭 Building up near a breakout (watch only - no order yet)"
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertIn(heading, [s.value for s in page.subheader])
        table = next(df.value for df in page.dataframe if "Below its 50-day high" in list(df.value.columns))
        self.assertEqual(list(table["Stock"]), ["AAA", "BBB"])
        self.assertEqual(list(table["200-day trend"]), ["💪 strong", "rising"])
        self.assertAlmostEqual(float(table["50-day high (the level to clear)"].iloc[0]), 1740.0)
        for jargon in self.JARGON:
            self.assertNotIn(jargon, self._everything_shown(page), jargon)
        os.remove("scan_results_swing.csv")                                             # no new pick: the watch list is still there
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertIn(heading, [s.value for s in page.subheader])
        os.remove("reports/output/building_up_swing.csv")                               # no file (or an empty one): no section
        self.assertNotIn(heading, [s.value for s in self._run().subheader])
        pd.DataFrame([], columns=pl.BUILDING_UP_COLUMNS).to_csv("reports/output/building_up_swing.csv", index=False)
        self.assertNotIn(heading, [s.value for s in self._run().subheader])

    @staticmethod
    def _plan_table(page):
        return next(df.value for df in page.dataframe if "What to do" in list(df.value.columns))

    def test_the_capital_plan_sizes_the_list_by_the_readers_own_numbers(self):
        self._write_files()
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        plan = self._plan_table(page)
        self.assertEqual(list(plan["Stock"]), ["HELD", "WAITING"])
        # weak market on the pick day = half of 1% at risk = 5,000 of 1,000,000: HELD 5,000 / (100 x 7.5%) = 666 shares, WAITING 5,000 / (250.5 x 5%) = 399
        self.assertEqual([int(x) for x in plan["Shares"]], [666, 399])
        self.assertEqual(list(plan["What to do"]), ["Take", "Take"])
        self.assertEqual(list(plan["Status"]), ["Bought", "Waiting"])
        self.assertAlmostEqual(float(plan["First stop-loss"].iloc[0]), 92.5)
        self.assertAlmostEqual(float(plan["Buy at"].iloc[1]), 250.5)
        metrics = {m.label: m.value for m in page.metric}
        self.assertEqual(metrics["Whole list needs (of your capital)"], "17%")                  # (66,600 + 99,949.5) / 1,000,000
        self.assertEqual(metrics["Lost if every stop-loss hit"], "-1.0%")                       # 0.5% + 0.5%
        self.assertEqual(metrics["Held or to take"], "2 of 10")
        self.assertEqual(metrics["Money in them (of your capital)"], "17%")
        self.assertTrue(all(len(v) <= 12 for k, v in metrics.items() if "capital" in k or k.startswith("Lost")))     # short values: long ones are cut off with "..."
        for jargon in self.JARGON:
            self.assertNotIn(jargon, self._everything_shown(page), jargon)
        # one room only: the second stock finds none
        next(n for n in page.number_input if n.label == "Most stocks to hold at once").set_value(1)
        page = page.run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertEqual(list(self._plan_table(page)["What to do"]), ["Take", "Skip - every room is taken"])
        # a stock the reader already holds comes first and uses the room
        next(m for m in page.multiselect if m.label.startswith("Stocks you already hold")).set_value(["WAITING"])
        page = page.run()
        plan = self._plan_table(page)
        self.assertEqual(list(plan["Stock"]), ["WAITING", "HELD"])
        self.assertEqual(list(plan["What to do"]), ["Held", "Skip - every room is taken"])
        # a stock with a tight stop-loss (2.5% below its price) at the top risk setting takes all the money (2.5% at risk / 2.5% = 100%): the other no
        # longer fits although a room is free
        ledger = pd.read_csv("tracking/trades.csv")
        ledger.loc[ledger["symbol"] == "WAITING", "atr_pct"] = 0.01
        ledger.to_csv("tracking/trades.csv", index=False)
        page = self._run()
        next(n for n in page.number_input if n.label == "Risk per trade (% of your capital)").set_value(5.0)
        next(m for m in page.multiselect if m.label.startswith("Stocks you already hold")).set_value(["WAITING"])
        page = page.run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        plan = self._plan_table(page)
        self.assertEqual(list(plan["Stock"]), ["WAITING", "HELD"])
        self.assertEqual(list(plan["What to do"]), ["Held", "Skip - the money does not fit"])

    def test_the_capital_plan_is_left_out_when_nothing_is_bought_or_waiting(self):
        self._write_files()
        ledger = pd.read_csv("tracking/trades.csv")
        ledger[ledger["status"].isin(["CLOSED", "UNFILLED"])].assign(status="CLOSED").to_csv("tracking/trades.csv", index=False)
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertFalse(any("What to do" in list(df.value.columns) for df in page.dataframe))

    def test_the_scoreboard_shows_how_the_held_trades_stand_and_how_far_the_record_is_from_30_sold_trades(self):
        self._write_files()
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        bars = page.get("progress")
        self.assertEqual(len(bars), 1)
        self.assertTrue(bars[0].proto.text.startswith("1 of 30 sold trades"), bars[0].proto.text)
        self.assertEqual(bars[0].proto.value, 3)
        heading = "How the trades you still hold stand right now (not final)"
        self.assertIn(heading, [s.value for s in page.subheader])
        table = next(df.value for df in page.dataframe if "Typical (median)" in list(df.value.columns))
        self.assertEqual(list(table.index), ["📈 Swing", "Both scans"])                                  # no BTST trade is held in the fixture
        self.assertEqual((int(table.loc["Both scans", "Bought, not sold yet"]), int(table.loc["Both scans", "Up"]), int(table.loc["Both scans", "Down"])), (1, 1, 0))
        self.assertAlmostEqual(float(table.loc["Both scans", "Average so far"]), 0.03)
        self.assertAlmostEqual(float(table.loc["Both scans", "Average R"]), 0.5)
        for jargon in self.JARGON:
            self.assertNotIn(jargon, self._everything_shown(page), jargon)
        # a held trade with no price yet is not a result of any kind: no table
        ledger = pd.read_csv("tracking/trades.csv")
        ledger.loc[ledger["status"] == "OPEN", "net"] = float("nan")
        ledger.to_csv("tracking/trades.csv", index=False)
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertNotIn(heading, [s.value for s in page.subheader])

    def test_with_no_files_at_all_it_still_opens(self):
        page = self._run()
        self.assertEqual([str(e.value) for e in page.exception], [])
        self.assertTrue(any("No tracked trades yet" in i.value for i in page.info))


# --------------------------------------------------------------------------- #
class E2EHarness:
    """main.py with a stub broker, in a scratch directory (shared by the end-to-end test classes)."""

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

    def _make_store(self, bars, quotes=None, report=None):
        """Stub broker: candles for any stock in `bars`; live quotes only if `quotes` (a function, or an Exception to raise) is given.
        `report` overrides parts of the fetch report the store hands back (e.g. why it stopped)."""
        class Store:
            def get_universe_bars(s, symbols, lookback_days, max_duration_seconds=5400):
                s.symbols = list(symbols)
                s.last_report = {"requested": len(symbols), "loaded": sum(1 for k in symbols if k in bars), "stopped": "", "errors": [],
                                 **(report or {})}
                return {k: bars[k].tail(lookback_days).copy() for k in symbols if k in bars}

            def get_bars(s, symbol, lookback_days):
                raise KeyError(symbol)

        store = Store()
        store.symbols = []                      # stocks fetched the slow (candle) way
        store.quote_calls = []                  # stocks asked for in live-quote calls
        if quotes is not None:
            def get_quotes(symbols):
                store.quote_calls.append(list(symbols))
                if isinstance(quotes, Exception):
                    raise quotes
                return quotes(list(symbols))
            store.get_quotes = get_quotes
        return store

    def _run(self, argv, now, source="manual", bars=None, quotes=None, universe=None, report=None):
        bars = self.bars if bars is None else bars
        store = self._make_store(bars, quotes, report)
        universe = list(bars) if universe is None else list(universe)       # the stocks the scan ASKS for (may be more than the broker returns)

        def post(url, json=None, timeout=None, **kw):
            self.sent.append(json["text"])
            return mock.Mock(ok=True, status_code=200, text="ok")

        os.environ["TRADING_RUN_SOURCE"] = source
        with mock.patch.object(self.M, "_get_store", lambda: store), \
                mock.patch.object(self.M, "_load_universe", lambda test_limit=None: list(universe)), \
                mock.patch.object(self.M, "_global_snapshot", lambda: {}), \
                mock.patch.object(self.M, "_sector_map", lambda: {}), \
                mock.patch("scanner.market_direction._fetch_yahoo_history", lambda *a, **k: None), \
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


class EndToEnd(E2EHarness, unittest.TestCase):
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
        with open("reports/output/last_message_swing.txt", encoding="utf-8") as fh:        # a copy of exactly what was sent
            self.assertEqual(fh.read().strip(), "\n\n".join(self.sent).strip())
        for gone in ("qty", "position_value"):                                      # no share counts: they depend on money no scan knows
            self.assertNotIn(gone, csv.columns)
        self.assertFalse(any("share" in m.lower() or "lakh" in m.lower() for m in self.sent), self.sent)
        with open("summary_swing.md", encoding="utf-8") as fh:
            self.assertNotIn("Shares", fh.read())

    def test_the_swing_run_lists_the_building_up_names_and_a_failure_of_the_list_costs_nothing(self):
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        self.assertEqual(code, 0)
        listed = pd.read_csv("reports/output/building_up_swing.csv")
        self.assertTrue(len(listed))                                                    # the synthetic market has strong stocks 3%-6% under their high
        message = "\n".join(self.sent)
        block = next(b for b in message.split("\n\n") if b.startswith("🔭"))
        self.assertIn(f"<b>{listed['symbol'].iloc[0]}</b>", block)                       # the best one leads the message block, as it leads the file
        with open("summary_swing.md", encoding="utf-8") as fh:
            summary_text = fh.read()
        for sym in listed["symbol"]:
            self.assertIn(f"| **{sym}**", summary_text)
        self.assertFalse(set(listed["symbol"]) & set(pd.read_csv("tracking/trades.csv")["symbol"]))   # watch names are never logged as trades
        picks = set(pd.read_csv("scan_results_swing.csv")["symbol"])
        self.assertFalse(set(listed["symbol"]) & picks)
        # the afternoon BTST message never carries the block and never touches the file
        nxt = self._next_session()
        before = open("reports/output/building_up_swing.csv", encoding="utf-8").read()
        self.sent.clear()
        self._run(["scan_btst"], at(2026, 10, 14, 14, 0), source="manual", bars=nxt)
        self.assertTrue(self.sent)
        self.assertNotIn("BUILDING UP", "\n".join(self.sent))
        self.assertEqual(open("reports/output/building_up_swing.csv", encoding="utf-8").read(), before)
        # if working out the list ever fails, the scan still runs and the message goes out without the block
        self.sent.clear()
        with mock.patch.object(pl, "building_up", side_effect=RuntimeError("boom")):
            code, _ = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=nxt)
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)
        self.assertNotIn("BUILDING UP", "\n".join(self.sent))
        self.assertEqual(len(pd.read_csv("reports/output/building_up_swing.csv")) if os.path.getsize("reports/output/building_up_swing.csv") > 3 else 0, 0)

    def test_the_note_about_the_whole_list_reaches_the_message_and_a_failure_of_it_costs_nothing(self):
        import capital_plan
        with mock.patch.object(capital_plan, "list_load", return_value=3.04):
            code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        self.assertEqual(code, 0)
        self.assertIn("Your whole list now needs about 300% of your capital at that rule", "\n".join(self.sent))
        with open("reports/output/last_message_swing.txt", encoding="utf-8") as fh:
            self.assertIn("about 300% of your capital", fh.read())
        self.sent.clear()
        with mock.patch.object(capital_plan, "list_load", side_effect=RuntimeError("boom")):
            code, _ = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=self._next_session())
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)
        self.assertNotIn("Your whole list now needs", "\n".join(self.sent))

    def test_the_evening_message_carries_the_stop_list_and_the_afternoon_one_does_not(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))                          # PLANT is logged as a waiting buy-stop
        first = pd.read_csv("tracking/trades.csv")
        waiting_before = first[first["status"] == "PENDING"]
        self.assertIn("PLANT", list(waiting_before["symbol"]))
        nxt = self._next_session()
        self.sent.clear()
        self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=nxt)
        evening = "\n".join(self.sent)
        self.assertIn("<b>YOUR LIST</b>", evening)
        self.assertRegex(evening, r"<b>WAITING TO BUY \(\d+\)</b> - buy when the price rises to:\n(?:[^\n]*\n)*?[^\n]*PLANT [\d,]+\.\d{2}")
        for r in waiting_before.itertuples():                                         # every waiting stock carries the price its order triggers at
            self.assertIn(f"{r.symbol} {pl.fmt_price(r.trigger)}", evening)
        self.sent.clear()
        self._run(["scan_btst"], at(2026, 10, 15, 14, 0), source="manual", bars=nxt)
        afternoon = "\n".join(self.sent)
        self.assertTrue(afternoon)
        self.assertNotIn("sell if the price falls to", afternoon)                    # stops only change after a completed session
        self.assertNotIn("WAITING TO BUY", afternoon)                                # (the earlier-picks block says "order still waiting")

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

    def _next_session(self):
        nxt = {}
        for s, d in self.bars.items():                                              # next session = a copy of the last bar, a day later
            last = d.iloc[[-1]].copy()
            last.index = [d.index[-1] + pd.Timedelta(days=1)]
            nxt[s] = pd.concat([d, last])
        return nxt

    def test_only_a_complete_live_btst_run_blocks_the_next_one(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        nxt = self._next_session()
        wl = self.M.load_watchlist(at(2026, 10, 14, 14, 0))
        thin = {s: nxt[s] for s in wl["symbols"][: max(1, len(wl["symbols"]) // 4)]}
        self._run(["scan_btst"], at(2026, 10, 14, 13, 50), source="timer", bars=thin)
        marker = _load_json("data_cache/run_markers.json")["btst"]
        self.assertFalse(marker["ok"])                                               # starved: loaded a quarter of the watchlist
        self.sent.clear()
        code, _ = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), source="timer", bars=nxt)
        marker = _load_json("data_cache/run_markers.json")["btst"]
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)                                                   # the thin run did not use up the slot
        self.assertTrue(marker["ok"] and marker["live"], marker)
        self.sent.clear()
        self._run(["scan_btst"], at(2026, 10, 14, 14, 8), source="schedule", bars=nxt)
        self.assertEqual(self.sent, [])                                              # the fallback arriving right after: quiet
        self._run(["scan_btst"], at(2026, 10, 14, 14, 9), source="manual", bars=nxt)
        self.assertTrue(self.sent)                                                   # the button always runs

    def test_a_morning_btst_run_does_not_use_up_the_afternoon_one(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        nxt = self._next_session()
        self._run(["scan_btst"], at(2026, 10, 14, 8, 42), source="timer", bars=nxt)  # a pre-open test: last-close view, not live
        marker = _load_json("data_cache/run_markers.json")["btst"]
        self.assertFalse(marker["live"])
        self.sent.clear()
        self._run(["scan_btst"], at(2026, 10, 14, 14, 0), source="timer", bars=nxt)
        self.assertTrue(self.sent)

    def test_a_thin_fetch_never_overwrites_the_watchlist(self):
        small = {k: v for k, v in list(self.bars.items())[:90]}                       # a partial fetch: far fewer stocks than the universe
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0), bars=small)
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists("data_cache/btst_watchlist.json"))

    def test_no_data_is_reported_and_exit_code_is_nonzero(self):
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0), bars={})
        self.assertEqual(code, 1)
        self.assertTrue(any("No price data" in m for m in self.sent))

    def test_every_run_leaves_a_fetch_report_behind(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        doc = _load_json("reports/output/fetch_report_swing.json")
        self.assertEqual((doc["scan"], doc["kind"], doc["route"], doc["requested"], doc["loaded"]), ("swing", "universe", "candles", len(self.bars), len(self.bars)))
        self.assertEqual(doc["at"], "2026-10-13 17:00 IST")
        self.assertIn("stopped", doc["store"])

    def test_a_full_run_fetches_tracked_trades_and_last_evenings_watchlist_first(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        head = [s for s in dict.fromkeys(self.M._must_have_symbols()) if s in self.bars]
        self.assertTrue(len(head) >= 5, head)
        self.sent.clear()
        _, store = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual")
        self.assertEqual(store.symbols[:len(head)], head)                              # a run cut short by the time limit still has them
        self.assertEqual(sorted(store.symbols), sorted(self.bars))                      # and nothing was added or dropped

    def test_a_run_cut_short_says_so_in_plain_words_and_does_not_block_the_next_one(self):
        asked = list(self.bars) + [f"GONE{i}" for i in range(60)]                        # 60 more stocks asked for than the broker returns
        report = {"stopped": "time budget", "not_reached": 59, "not_found": ["GONE0"], "not_found_n": 1,
                  "errors": [("Access denied because of exceeding access rate", 4)]}
        code, _ = self._run(["scan_swing"], at(2026, 10, 13, 17, 0), universe=asked, report=report)
        self.assertEqual(code, 0)
        msg = "\n".join(self.sent)
        self.assertIn(f"Partial scan: {len(self.bars)} of {len(asked)} stocks loaded - the 45-minute limit was reached, "
                      f"so some names may be missing", msg)
        self.assertIn("The broker said: Access denied because of exceeding access rate (x4)", msg)
        self.assertNotIn("GONE0", msg)                                                    # which stocks the broker lacks is in the fetch report
        self.assertNotIn("&#x27;", msg)                                                   # no stray quote characters
        doc = _load_json("reports/output/fetch_report_swing.json")
        self.assertEqual((doc["requested"], doc["loaded"], doc["store"]["stopped"], doc["store"]["not_reached"]),
                         (len(asked), len(self.bars), "time budget", 59))
        self.assertTrue(_load_json("data_cache/run_markers.json")["swing"]["ok"])      # 77% loaded still counts as a run

    def test_priority_order_only_reorders(self):
        order = self.M._priority_order(["A", "B", "C", "D", "E"], ["D", "ZZ", "B", "D"])
        self.assertEqual(order, ["D", "B", "A", "C", "E"])
        self.assertEqual(self.M._priority_order(["A", "B"], []), ["A", "B"])

    def test_partial_scan_wording(self):
        base = {"loaded": 448, "requested": 501, "budget_s": 2700}
        note = self.M._partial_note({**base, "report": {
            "stopped": "time budget", "not_reached": 51, "not_found": ["HFCL"], "not_found_n": 1,
            "errors": [("Access denied because of exceeding access rate", 4)]}})
        self.assertEqual(note, "Partial scan: 448 of 501 stocks loaded - the 45-minute limit was reached, so some names may be missing. "
                               "The broker said: Access denied because of exceeding access rate (x4)")
        for bad in ("#", "''", '""', "HFCL"):
            self.assertNotIn(bad, note)
        many = self.M._partial_note({**base, "report": {"stopped": "", "not_found": list("ABCDEFGH"), "not_found_n": 11}})
        self.assertIn("the broker returned no data for the rest", many)
        self.assertNotIn("A, B", many)                                                    # the names are in the fetch report, not the message
        self.assertEqual(self.M._partial_note({"loaded": 0, "requested": 501, "report": {"stopped": "the broker kept refusing requests"}}),
                         "None of the 501 stocks could be loaded - the broker kept refusing requests")

    def test_the_market_line_has_a_second_source_for_the_index(self):
        frame = next(iter(self.bars.values())).tail(120)
        store = self._make_store(self.bars)

        def broker_ok(symbol, lookback_days):
            return frame
        # broker first after the close ...
        store.get_bars = broker_ok
        with mock.patch("scanner.market_direction._fetch_yahoo_history", lambda t, *a, **k: self.fail("Yahoo should not be asked")):
            df, found, src = self.M._fetch_benchmark(store, 300)
        self.assertEqual((found, src, len(df)), (True, config.RS_BENCHMARK, 120))
        # ... Yahoo first in market hours, and the broker when Yahoo has nothing
        with mock.patch("scanner.market_direction._fetch_yahoo_history", lambda t, *a, **k: frame):
            df, found, src = self.M._fetch_benchmark(store, 300, prefer_yahoo=True)
        self.assertEqual((found, src), (True, "^NSEI"))
        with mock.patch("scanner.market_direction._fetch_yahoo_history", lambda t, *a, **k: None):
            df, found, src = self.M._fetch_benchmark(store, 300, prefer_yahoo=True)
        self.assertEqual((found, src), (True, config.RS_BENCHMARK))

        def broker_down(symbol, lookback_days):
            raise RuntimeError("Something Went Wrong, Please Try After Sometime")
        store.get_bars = broker_down
        with mock.patch("scanner.market_direction._fetch_yahoo_history", lambda t, *a, **k: frame):
            df, found, src = self.M._fetch_benchmark(store, 300)                       # the broker is down: Yahoo rescues the line
        self.assertEqual((found, src), (True, "^NSEI"))
        with mock.patch("scanner.market_direction._fetch_yahoo_history", lambda t, *a, **k: None):
            df, found, src = self.M._fetch_benchmark(store, 300)
        self.assertEqual((found, src, len(df)), (False, "", 0))


# --------------------------------------------------------------------------- #
class EarlierPicksEndToEnd(E2EHarness, unittest.TestCase):
    """The whole run: the next evening the stocks picked yesterday come back as "EARLIER PICKS", and nothing is logged twice."""

    def _next_session(self):
        nxt = {}
        for s, d in self.bars.items():                                              # next session = a copy of the last bar, a day later
            last = d.iloc[[-1]].copy()
            last.index = [d.index[-1] + pd.Timedelta(days=1)]
            nxt[s] = pd.concat([d, last])
        return nxt

    def test_the_next_evening_lists_the_waiting_picks_and_logs_nothing_twice(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        before = pd.read_csv("tracking/trades.csv")
        waiting = set(before.loc[before["status"] == "PENDING", "symbol"])
        self.assertGreaterEqual(len(waiting), 2)
        self.sent.clear()
        code, _ = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), bars=self._next_session())
        self.assertEqual(code, 0)
        text = "\n\n".join(self.sent)
        self.assertIn("<b>EARLIER PICKS, STILL STRONG</b>", text)
        block = text[text.index("EARLIER PICKS, STILL STRONG"):].split("\n\n")[0]
        listed = [ln.split("</b>")[0].replace("<b>", "") for ln in block.split("\n")[1:] if ln.startswith("<b>")]
        self.assertTrue(listed and set(listed) <= waiting, (listed, waiting))
        self.assertIn(f"order valid {config.ENTRY_VALID_SESSIONS - 1} more sessions", block)       # one session has gone since they were picked
        new = pd.read_csv("scan_results_swing.csv")
        if len(new):
            self.assertIn("NEW · ", text)
            self.assertLess(text.index("EARLIER PICKS"), text.index("NEW · "))
        else:
            self.assertNotIn("NEW ·", text)
            self.assertLess(text.index("NO NEW PICK."), text.index("EARLIER PICKS"))
        after = pd.read_csv("tracking/trades.csv")
        self.assertEqual(len(after), len(before) + len(new))                           # only genuinely new picks are logged: nothing is picked twice
        self.assertEqual(set(before["trade_id"]), set(after["trade_id"]) - set(f"{r.symbol}_{r.signal_date}_{r.scan_mode}" for r in new.itertuples()))
        with open("summary_swing.md", encoding="utf-8") as fh:
            self.assertIn("**Earlier picks, still strong**", fh.read())
        with open("reports/output/last_message_swing.txt", encoding="utf-8") as fh:        # a copy of exactly what was sent
            self.assertEqual(fh.read().strip(), text.strip())
        self.assertTrue(all(len(m) <= 4096 for m in self.sent))

    def test_a_scan_with_nothing_waiting_has_no_block(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        self.assertNotIn("EARLIER PICKS", "\n".join(self.sent))                         # the first scan ever: nothing was picked before
        self.assertNotIn("NEW ·", "\n".join(self.sent))

    def test_a_ledger_that_cannot_be_read_never_costs_the_scan(self):
        with mock.patch.object(pl, "earlier_picks", side_effect=RuntimeError("boom")):
            self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
            self.sent.clear()
            code, _ = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), bars=self._next_session())
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)
        self.assertNotIn("EARLIER PICKS", "\n".join(self.sent))


class QuoteRoute(E2EHarness, unittest.TestCase):
    """The afternoon BTST on saved candles + live quotes: a handful of broker calls instead of one per stock."""

    @classmethod
    def setUpClass(cls):
        base = make_market(n_stocks=200)
        # real prices sit on a 0.05 tick: round the synthetic ones so the saved candles are lossless
        cls.bars = {s: d.round({"open": 2, "high": 2, "low": 2, "close": 2, "volume": 0}) for s, d in base.items()}
        cls.nxt = {}
        for s, d in cls.bars.items():                                               # next session: a slightly different bar
            last = d.iloc[[-1]].copy()
            last.index = [d.index[-1] + pd.Timedelta(days=1)]
            last[["open", "high", "low", "close"]] = (last[["open", "high", "low", "close"]] * 1.01).round(2)
            cls.nxt[s] = pd.concat([d, last])

    @staticmethod
    def quotes_from(bars):
        def fn(symbols):
            out = {}
            for s in symbols:
                t, y = bars[s].iloc[-1], bars[s].iloc[-2]
                out[s] = {"ltp": float(t["close"]), "open": float(t["open"]), "high": float(t["high"]), "low": float(t["low"]),
                          "prev_close": float(y["close"]), "volume": float(t["volume"])}
            return out
        return fn

    def _evening(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))
        self.sent.clear()
        return self.M.load_watchlist(at(2026, 10, 14, 14, 0))

    def test_the_evening_run_saves_the_candles_of_exactly_the_watchlist(self):
        wl = self._evening()
        self.assertIsNotNone(wl)
        from data import history_cache
        hist, as_of = history_cache.load()
        self.assertEqual(set(hist), set(wl["symbols"]))
        self.assertEqual(as_of, "2026-10-13")
        sym = wl["symbols"][0]
        pd.testing.assert_frame_equal(hist[sym], self.bars[sym].tail(len(hist[sym])), check_freq=False, check_dtype=False)

    def test_merged_bars_equal_the_candle_bars(self):
        wl = self._evening()
        syms = list(wl["symbols"])
        store = self._make_store(self.nxt, self.quotes_from(self.nxt))
        bars, info = self.M._watchlist_bars(store, wl, syms, at(2026, 10, 14, 14, 0), None)
        self.assertEqual((info["via"], store.symbols, len(store.quote_calls)), ("quotes", [], 1))
        self.assertEqual(set(bars), set(syms))
        for s in syms:
            exp = self.nxt[s].tail(len(bars[s]))
            pd.testing.assert_frame_equal(bars[s], exp, check_freq=False, check_dtype=False)

    def test_scan_btst_uses_quotes_only_and_says_so(self):
        self._evening()
        code, store = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), bars=self.nxt, quotes=self.quotes_from(self.nxt))
        self.assertEqual(code, 0)
        self.assertEqual(store.symbols, [])                                         # not one candle call for the watchlist
        self.assertEqual(len(store.quote_calls), 1)
        self.assertTrue(any("live quotes" in m for m in self.sent), self.sent[:1])
        self.assertFalse(any("Partial scan" in m for m in self.sent))
        self.assertFalse(any("Live quotes not used" in m for m in self.sent))
        self.assertFalse(any("sell if the price falls to" in m or "WAITING TO BUY" in m for m in self.sent))   # the afternoon message has no stop list
        doc = _load_json("reports/output/fetch_report_btst.json")
        self.assertEqual((doc["kind"], doc["route"], doc["loaded"] == doc["requested"]), ("watchlist", "quotes", True))

    def test_the_live_run_logs_a_snapshot_of_the_watchlist_and_the_evening_run_completes_it(self):
        from data import snapshots
        wl = self._evening()
        code, _ = self._run(["scan_btst"], at(2026, 10, 14, 14, 45), bars=self.nxt, quotes=self.quotes_from(self.nxt))
        self.assertEqual(code, 0)
        snap = snapshots.load()
        self.assertEqual(set(snap["symbol"]), set(wl["symbols"]))                                       # every stock the scan looked at, picks or not
        self.assertEqual((set(snap["date"]), set(snap["time_ist"]), set(snap["via"])), ({"2026-10-14"}, {"14:45"}, {"quotes"}))
        self.assertTrue((abs(snap["assumed_frac"] - session.volume_fraction(at(2026, 10, 14, 14, 45))) < 1e-3).all())
        picked = set(pd.read_csv("scan_results_btst.csv")["symbol"]) if os.path.exists("scan_results_btst.csv") else set()
        self.assertEqual(set(snap.loc[snap["pick"] == 1, "symbol"]), picked)
        self.assertTrue(snap["final_close"].isna().all() and snap["next_close"].isna().all())          # the day has not ended
        for r in snap.itertuples():                                                                    # what the scan saw is what the quote said
            today = self.nxt[r.symbol].iloc[-1]
            self.assertAlmostEqual((r.last, r.volume), (round(float(today["close"]), 2), round(float(today["volume"]))), places=2)
        # a second live run on the same minute replaces, a morning run (not live) adds nothing; then the evening run on the finished day fills it in
        self.sent.clear()
        self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=self.nxt)
        done = snapshots.load().set_index("symbol")
        self.assertEqual(len(done), len(snap))
        self.assertTrue(done["final_close"].notna().all())
        for sym in wl["symbols"][:5]:
            d = self.nxt[sym].iloc[-1]
            self.assertEqual((done.loc[sym, "final_close"], done.loc[sym, "final_volume"]), (float(d["close"]), float(d["volume"])))
            self.assertAlmostEqual(done.loc[sym, "last"], round(float(d["close"]), 2))                  # the synthetic quote is the final bar, so the two agree

    def test_a_failure_of_the_snapshot_log_never_costs_the_scan(self):
        from data import snapshots
        self._evening()
        with mock.patch.object(snapshots, "record", side_effect=OSError("disk full")):
            code, _ = self._run(["scan_btst"], at(2026, 10, 14, 14, 45), bars=self.nxt, quotes=self.quotes_from(self.nxt))
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)
        self.assertFalse(os.path.exists("data_cache/btst_snapshots.csv.gz"))
        self.sent.clear()
        with mock.patch.object(snapshots, "complete", side_effect=OSError("disk full")):
            code, _ = self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=self.nxt)
        self.assertEqual(code, 0)
        self.assertTrue(self.sent)

    def test_quote_failure_falls_back_to_candles(self):
        wl = self._evening()
        code, store = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), bars=self.nxt, quotes=RuntimeError("no usable quote from the broker"))
        self.assertEqual(code, 0)
        self.assertEqual(set(store.symbols), set(wl["symbols"]))

    def test_a_stock_whose_price_scale_changed_is_fetched_the_slow_way(self):
        wl = self._evening()
        odd = wl["symbols"][3]
        good = self.quotes_from(self.nxt)

        def quotes(symbols):
            out = good(symbols)
            out[odd] = {**out[odd], "prev_close": out[odd]["prev_close"] * 0.5}       # e.g. a 2-for-1 split since the saved candles
            return out
        code, store = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), bars=self.nxt, quotes=quotes)
        self.assertEqual(code, 0)
        self.assertEqual(store.symbols, [odd])

    def test_without_saved_candles_the_normal_fetch_is_used(self):
        wl = self._evening()
        os.remove("data_cache/btst_history.csv.gz")
        code, store = self._run(["scan_btst"], at(2026, 10, 14, 14, 0), bars=self.nxt, quotes=self.quotes_from(self.nxt))
        self.assertEqual(code, 0)
        self.assertEqual(store.quote_calls, [])
        self.assertEqual(set(store.symbols), set(wl["symbols"]))
        self.assertTrue(any("Live quotes not used (no saved candles for this watchlist)" in m for m in self.sent), self.sent[:1])
        self.assertEqual(_load_json("reports/output/fetch_report_btst.json")["route"], "candles")

    def test_partial_fetch_note_quotes_the_brokers_words(self):
        note = self.M._partial_note({"loaded": 11, "requested": 501, "report": {
            "stopped": "the broker kept refusing requests", "errors": [("Something Went Wrong, Please Try After Sometime", 440)]}})
        self.assertIn("11 of 501", note)
        self.assertIn("kept refusing", note)
        self.assertIn("Something Went Wrong", note)
        self.assertIn("(x440)", note)
        self.assertIn("None of the 501", self.M._partial_note({"loaded": 0, "requested": 501, "report": {}}))


class ResearchRun(E2EHarness, unittest.TestCase):
    """`main.py backtest`: the broker route is unchanged, and the Yahoo route never touches the broker."""

    def _backtest(self, env):
        with mock.patch.dict(os.environ, env), mock.patch.object(self.M, "_load_universe", lambda test_limit=None: list(self.bars)):
            logging.disable(logging.CRITICAL)
            try:
                return self.M.main(["backtest", "--calibrate-mode", "swing"])
            finally:
                logging.disable(logging.NOTSET)

    def test_yahoo_route_writes_the_report_and_never_touches_the_broker(self):
        frames = {f"{sym}.NS": d.rename(columns=str.title) for sym, d in self.bars.items()}        # Open, High, Low, Close, Volume
        calls: list = []

        def no_broker():
            raise AssertionError("the research run must not use the broker")

        with mock.patch.dict(sys.modules, {"yfinance": fake_yfinance(frames, calls)}), \
                mock.patch("data.yahoo_bars.time.sleep", lambda s: None), \
                mock.patch.object(self.M, "_get_store", no_broker):
            code = self._backtest({"TRADING_BACKTEST_SOURCE": "yahoo", "TRADING_BACKTEST_DAYS": "80"})
        self.assertEqual(code, 0)
        names = os.listdir("reports/output")
        md = [n for n in names if n.startswith("backtest_swing_yahoo_") and n.endswith(".md")]
        trades_csv = [n for n in names if n.startswith("backtest_swing_yahoo_") and n.endswith("_trades.csv")]
        self.assertTrue(md and trades_csv, names)
        rep = _load_json("reports/output/research_fetch_yahoo.json")
        self.assertEqual((rep["source"], rep["asked"], rep["usable"]), ("yahoo", len(self.bars), len(self.bars)))
        self.assertEqual(len(calls), len(self.bars))
        self.assertTrue(all(kw["auto_adjust"] is False for _, kw in calls))
        with open(f"reports/output/{md[0]}", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Yahoo", text)
        self.assertIn("TODAY's NIFTY 500", text)                                                   # the survivorship warning travels with the numbers
        trades = pd.read_csv(f"reports/output/{trades_csv[0]}")
        self.assertGreater(len(trades), 10)
        self.assertLessEqual({"z_run_up", "run_up", "pb1_status", "pb2_status"}, set(trades.columns))

    def test_the_broker_route_is_unchanged(self):
        store = self._make_store(self.bars)
        asked_yahoo = types.SimpleNamespace(Ticker=lambda t: (_ for _ in ()).throw(AssertionError("the broker route must not ask Yahoo")))
        with mock.patch.object(self.M, "_get_store", lambda: store), mock.patch.dict(sys.modules, {"yfinance": asked_yahoo}):
            code = self._backtest({"TRADING_BACKTEST_SOURCE": "", "TRADING_BACKTEST_DAYS": "60"})
        self.assertEqual(code, 0)
        names = os.listdir("reports/output")
        self.assertTrue(any(n.startswith("backtest_swing_2") and n.endswith(".md") for n in names), names)
        self.assertFalse(any("yahoo" in n for n in names), names)
        self.assertEqual(set(store.symbols), set(self.bars))

    def test_an_empty_yahoo_answer_fails_loudly_instead_of_writing_an_empty_report(self):
        frames = {f"{sym}.NS": pd.DataFrame() for sym in self.bars}
        with mock.patch.dict(sys.modules, {"yfinance": fake_yfinance(frames, [])}), mock.patch("data.yahoo_bars.time.sleep", lambda s: None):
            code = self._backtest({"TRADING_BACKTEST_SOURCE": "yahoo"})
        self.assertEqual(code, 1)
        self.assertFalse([n for n in os.listdir("reports/output") if n.endswith(".md")])


if __name__ == "__main__":
    unittest.main()

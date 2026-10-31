"""
Self-contained tests: no network, no broker, no Telegram. Run:  python -m unittest discover tests -v

They use small synthetic markets (random walks with one planted breakout) so they run in seconds, and check the
parts where a silent mistake would cost money: the trade simulator, the session clock, the run guard, the setup
and plan arithmetic, the scoreboard (including re-runs and split gaps), the message splitter and the full
`main.py scan_*` flow with a stub broker.
"""
import dataclasses
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
        for t in self.trades:
            self.assertFalse(need - set(t), f"missing {need - set(t)} in {t}")

    def test_the_extra_columns_do_not_change_the_real_trades(self):
        with mock.patch.object(self.engine, "PULLBACKS", ()):
            plain = self.engine.run_backtest(self.bars, "swing", test_days=120)
        self.assertFalse(any("pb1_status" in t for t in plain))
        self.assertEqual(self._core(plain), self._core(self.trades))

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
        runguard.record_run("btst", at(2026, 10, 13, 13, 47), "schedule", 4, path=self.path, live=True)
        self.assertTrue(runguard.should_skip("btst", "timer", at(2026, 10, 13, 14, 0), self.path)[0])
        runguard.record_run("swing", at(2026, 10, 13, 16, 5), "timer", 4, path=self.path)
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 13, 22, 30), self.path)[0])
        self.assertTrue(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 1, 10), self.path)[0])   # after midnight, same session
        self.assertFalse(runguard.should_skip("swing", "schedule", at(2026, 10, 14, 16, 5), self.path)[0])  # next session

    def test_a_morning_or_trial_run_never_uses_up_the_afternoon_slot(self):
        # 8:42 AM timer test: market closed, so it is the last-close view and not live
        runguard.record_run("btst", at(2026, 10, 22, 8, 42), "timer", 0, path=self.path, live=False)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 0), self.path)[0])
        # an old marker written before the flag existed behaves the same
        with open(self.path, "w") as f:
            json.dump({"btst": {"at": "2026-10-22 08:42 IST", "key": "2026-10-22", "ok": True, "picks": 0, "source": "timer"}}, f)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 0), self.path)[0])
        # a live trial run at 1:29 PM does not block the 2:00 PM timer run (31 minutes later), nor does one at 1:35 ...
        for hh, mm in ((13, 29), (13, 35)):
            runguard.record_run("btst", at(2026, 10, 22, hh, mm), "timer", 2, path=self.path, live=True)
            self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 0), self.path)[0])
        # ... but a live run 10 minutes earlier does (the 1:45 PM GitHub fallback and the 2:00 PM timer arriving together)
        runguard.record_run("btst", at(2026, 10, 22, 13, 50), "schedule", 2, path=self.path, live=True)
        self.assertTrue(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 0), self.path)[0])
        self.assertTrue(runguard.should_skip("btst", "schedule", at(2026, 10, 22, 14, 5), self.path)[0])
        # a starved run (the broker refused most stocks) never blocks a retry, however recent
        runguard.record_run("btst", at(2026, 10, 22, 13, 55), "timer", 0, ok=False, path=self.path, live=True)
        self.assertFalse(runguard.should_skip("btst", "timer", at(2026, 10, 22, 14, 0), self.path)[0])
        # and a manual run always goes ahead
        runguard.record_run("btst", at(2026, 10, 22, 13, 58), "timer", 2, path=self.path, live=True)
        self.assertFalse(runguard.should_skip("btst", "manual", at(2026, 10, 22, 14, 0), self.path)[0])

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
        self.assertTrue(p.why)                                                     # the few-words reasons the Telegram card shows
        for gone in ("qty", "position_value", "size_note"):                         # share counts depend on money no scan can know
            self.assertFalse(hasattr(p, gone), gone)
            self.assertNotIn(gone, p.to_row())
        order = [(0 if q.strong_trend else 1, 0 if q.setup == "fresh" else 1) for q in res.picks]
        self.assertEqual(order, sorted(order))                                     # strong uptrends first, then fresh before primed

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
        self.assertLessEqual(len(card.split("\n")), 6)
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
        self.assertTrue(card[3].startswith("💪 strong uptrend · "), card[3])
        self.assertEqual(card[3].split(" · ")[1:], plain[3].split(" · "))           # the tag takes its own slot: nothing else is dropped
        self.assertNotIn("💪", "\n".join(plain))
        # a fresh buy-stop card also shows "up x% on y volume": the tag + the usual four items
        line = notify.format_pick(dataclasses.replace(self.by["PLANT"], strong_trend=True), 1).split("\n")[3]
        self.assertTrue(line.startswith("💪 strong uptrend · up "), line)
        self.assertEqual(len(line.split(" · ")), 5, line)

    def test_the_footer_explains_the_tag_only_when_a_card_carries_it(self):
        legend = "strong uptrend = the 200-day average is up 5%+ in a month; these are listed first"
        msg = notify.format_scan_message(self.res, "swing", now=self.now)
        self.assertEqual(msg.count(legend), 1)
        self.assertLess(msg.index(legend), msg.index("ℹ️ <i>Trail ="))
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
        d.iloc[-1, d.columns.get_loc("volume")] *= 0.64                             # 2:00 PM: 64% of the day's volume so far
        return pl.select_picks("btst", live, now=at(2026, 10, 13, 14, 0))

    def _all_messages(self):
        return [(self._swing(), "swing", at(2026, 10, 13, 17, 0)), (self._live(), "btst", at(2026, 10, 13, 14, 0))]

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
                self.assertTrue(3 <= len(lines) <= 6, card)
                self.assertLessEqual(len(card), 330 + 110 * len(warnings), card)
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

    def test_the_section_heading_still_says_which_cards_are_trigger_orders(self):
        # the cards say "Buy at"; this heading is what tells a reader to enter them as buy-stop / GTT trigger orders, not as plain limit orders
        msg = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 13, 17, 0))
        self.assertIn("BUY-STOP ORDERS</b> (GTT / stop-limit", msg)

    def test_the_message_stays_short(self):
        for res, mode, now in self._all_messages():
            msg = notify.format_scan_message(res, mode, now=now)
            self.assertLessEqual(len(msg), 1100 + 300 * len(res.picks), len(msg))
            self.assertEqual(len(notify.split_message(msg)), 1)

    def test_no_new_pick_is_one_short_line_that_says_why(self):
        base = self._swing()
        none = dataclasses.replace(base, picks=[], repeats=[])
        msg = notify.format_scan_message(none, "swing", now=at(2026, 10, 13, 17, 0))
        self.assertIn("No new pick.", msg)
        self.assertLessEqual(len(msg), 900, msg)
        again = dataclasses.replace(base, picks=[], repeats=[("A", "primed")] * 3,
                                    funnel={**base.funnel, "passed_quality": 3, "repeats": 3})
        self.assertIn("all already on your list", notify.format_scan_message(again, "swing", now=at(2026, 10, 13, 17, 0)))
        empty = dataclasses.replace(none, funnel={**base.funnel, "loaded": 0})
        self.assertIn("No price data was loaded", notify.format_scan_message(empty, "swing", now=at(2026, 10, 13, 17, 0)))

    def test_title_says_what_the_prices_are_based_on(self):
        swing = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 13, 17, 0)).split("\n")[0]
        self.assertEqual(swing, "📈 <b>Swing scan</b> · Tue 13 Oct · today's close")
        later = notify.format_scan_message(self._swing(), "swing", now=at(2026, 10, 17, 11, 0)).split("\n")[0]
        self.assertEqual(later, "📈 <b>Swing scan</b> · Sat 17 Oct · close of Tue 13 Oct")
        live = notify.format_scan_message(self._live(), "btst", now=at(2026, 10, 13, 14, 1)).split("\n")[0]
        self.assertEqual(live, "🌙 <b>BTST scan</b> · Tue 13 Oct, 2:01 PM · live prices")

    def test_the_weak_market_rule_is_stated_once_in_relative_terms(self):
        res = dataclasses.replace(self._swing(), regime="weak", breadth={"breadth50": 0.18, "n": 400})
        msg = notify.format_scan_message(res, "swing", now=at(2026, 10, 13, 17, 0))
        self.assertEqual(msg.count("half size"), 1)
        self.assertIn("Breadth 18%", msg)
        self.assertIn("half in a weak market", msg)                                  # the footer says it as a rule

    def test_tracked_block_lists_stops_only_in_the_evening_message(self):
        events = [{"kind": "trail_raised", "symbol": "AAA", "old": 100.0, "new": 105.0},
                  {"kind": "filled", "symbol": "BBB", "price": 50.0},
                  {"kind": "closed", "symbol": "CCC", "reason": "stop", "price": 90.0, "R": -1.0, "net": -0.06},
                  {"kind": "unfilled", "symbol": "DDD", "reason": "expired"}]
        rows = [{"symbol": "AAA", "status": "OPEN", "trail": 105.0}, {"symbol": "BBB", "status": "OPEN", "trail": 45.5},
                {"symbol": "EEE", "status": "PENDING", "trigger": 200.0}]
        evening = notify.format_tracked_block(events, rows, snapshot=True)
        self.assertIn("Open - current stops (2)", evening)
        self.assertIn("AAA 105.00↑", evening)
        self.assertIn("BBB 45.50", evening)
        self.assertNotIn("BBB 45.50↑", evening)
        self.assertNotIn("raise stop", evening)                                      # the arrow replaces the separate line
        self.assertIn("Buy-stops still waiting (1):</b> EEE", evening)
        self.assertNotIn("200.00", evening)                                          # names only
        self.assertIn("BBB</b> - buy-stop filled at 50.00", evening)
        self.assertIn("CCC</b> - stopped out at 90.00 (-1.0R, -6.0% after costs)", evening)
        self.assertIn("DDD</b> - buy-stop expired; no trade", evening)
        afternoon = notify.format_tracked_block(events, rows, snapshot=False)
        self.assertNotIn("current stops", afternoon)
        self.assertNotIn("waiting", afternoon)
        self.assertIn("AAA</b> - raise stop 100.00 → <b>105.00</b>", afternoon)      # nothing is lost when there is no list to carry the arrow
        self.assertEqual(notify.format_tracked_block([], rows, snapshot=False), "")
        self.assertEqual(notify.format_tracked_block([], [], snapshot=True), "")
        for block in (evening, afternoon):
            chk = self._Check()
            chk.feed(block)
            self.assertEqual((chk.err, chk.stack), ([], []))

    def test_a_long_list_is_capped_not_dumped(self):
        rows = [{"symbol": f"S{i:02d}", "status": "OPEN", "trail": 10.0 + i} for i in range(30)]
        rows += [{"symbol": f"W{i:02d}", "status": "PENDING", "trigger": 5.0} for i in range(20)]
        block = notify.format_tracked_block([], rows, snapshot=True)
        self.assertIn("Open - current stops (30)", block)
        self.assertIn("...and 6 more in the dashboard", block)
        self.assertIn("Buy-stops still waiting (20)", block)
        self.assertIn(", +4 more", block)

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
        self.assertEqual(outcomes.format_scoreboard_line(waiting), "Record since 21 Oct: no trade closed yet")
        two = pd.DataFrame({"status": ["CLOSED", "CLOSED", "OPEN"], "net": [0.10, -0.05, np.nan], "R": [2.0, -1.0, np.nan],
                            "signal_date": ["2026-10-21"] * 3, "scan_mode": ["swing"] * 3, "setup": ["primed"] * 3,
                            "exit_n": [10, 5, -1], "fill_n": [1, 1, 1]})
        line = outcomes.format_scoreboard_line(two)
        self.assertEqual(line, "Record since 21 Oct: 2 closed, 50% won, average +0.50R (+2.5% per trade after costs) - too few to judge yet (need 30+)")


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

    def test_the_evening_message_carries_the_stop_list_and_the_afternoon_one_does_not(self):
        self._run(["scan_swing"], at(2026, 10, 13, 17, 0))                          # PLANT is logged as a waiting buy-stop
        nxt = self._next_session()
        self.sent.clear()
        self._run(["scan_swing"], at(2026, 10, 14, 17, 0), source="manual", bars=nxt)
        evening = "\n".join(self.sent)
        self.assertIn("Your list", evening)
        self.assertRegex(evening, r"Buy-stops still waiting \(\d+\):</b> .*PLANT")
        self.sent.clear()
        self._run(["scan_btst"], at(2026, 10, 15, 14, 0), source="manual", bars=nxt)
        afternoon = "\n".join(self.sent)
        self.assertTrue(afternoon)
        self.assertNotIn("current stops", afternoon)                                 # stops only change after a completed session
        self.assertNotIn("still waiting", afternoon)

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
        self.assertFalse(any("current stops" in m or "still waiting" in m for m in self.sent))   # the afternoon message has no stop list
        doc = _load_json("reports/output/fetch_report_btst.json")
        self.assertEqual((doc["kind"], doc["route"], doc["loaded"] == doc["requested"]), ("watchlist", "quotes", True))

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

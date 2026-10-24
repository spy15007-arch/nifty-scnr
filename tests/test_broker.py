"""
Broker-facing code, tested without a broker: the candle/quote client (a refused answer must never look like
"no data", and a few refusals must never slow a whole run down), the universe fetch (cool-down, second pass,
honest report) and the saved-candles + live-quote merge.
Run:  python -m unittest discover tests -v
"""
import os
import random
import shutil
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from data import history_cache  # noqa: E402
from data.brokers.angelone_client import (AngelOneDataClient, BrokerRefused, Pace, clean_reply,  # noqa: E402
                                          is_rate_limit_error, is_transient_error, read_candle_reply, read_quote_record)
from data.historical import HistoricalStore, classify_failure  # noqa: E402

REFUSAL = {"status": False, "message": "Something Went Wrong, Please Try After Sometime", "errorcode": "AB1004", "data": None}
RATE_REFUSAL = {"status": False, "message": "Access denied because of exceeding access rate", "errorcode": "AB1004", "data": None}


def candle_reply(n=3):
    rows = [[f"2026-10-{10 + i:02d}T00:00:00+05:30", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000 * (i + 1)] for i in range(n)]
    return {"status": True, "message": "SUCCESS", "errorcode": "", "data": rows}


class FakeClient(AngelOneDataClient):
    """The real client's logic with the login and the SDK removed."""

    def __init__(self):
        self.client = mock.Mock()
        self._instrument_cache = {}
        self.pace = Pace(0.4)
        self.error_counts = Counter()
        self.error_examples = {}

    def _symbol_token(self, tradingsymbol, exchange="NSE"):
        if tradingsymbol.startswith("BAD"):
            raise KeyError(f"Symbol {tradingsymbol} not found in Angel One instrument master")
        return "T" + tradingsymbol


class NoSleep(unittest.TestCase):
    """time.sleep does nothing, but every wait is recorded in self.sleeps."""

    def setUp(self):
        self.sleeps = []
        p = mock.patch("time.sleep", lambda s: self.sleeps.append(s))
        p.start()
        self.addCleanup(p.stop)


class ErrorWords(unittest.TestCase):
    def test_which_errors_are_worth_a_retry(self):
        for msg in ("Access denied because of exceeding access rate", "Something Went Wrong, Please Try After Sometime",
                    "Too many requests", "Couldn't parse the JSON response received from the server: b'<html>Bad Gateway'",
                    "HTTPSConnectionPool(host='x'): Read timed out", "no data and no message", "unexpected reply of type NoneType"):
            self.assertTrue(is_transient_error(msg), msg)
        for msg in ("Invalid symboltoken", "Symbol XYZ not found in Angel One instrument master", "Could not generate the report"):
            self.assertFalse(is_transient_error(msg), msg)

    def test_which_errors_say_too_fast(self):
        for msg in ("Access denied because of exceeding access rate", "Too many requests", "AB1021 limit"):
            self.assertTrue(is_rate_limit_error(msg), msg)
        for msg in ("Something Went Wrong, Please Try After Sometime", "502 Bad Gateway", "Read timed out"):
            self.assertFalse(is_rate_limit_error(msg), msg)

    def test_replies_are_tidied_for_a_person(self):
        self.assertEqual(clean_reply(KeyError("Symbol HFCL not found in Angel One instrument master")),
                         "Symbol HFCL not found in Angel One instrument master")                     # KeyError wraps its text in quotes
        self.assertEqual(clean_reply(Exception("Couldn't parse the JSON response received from the server: "
                                               "b'Access denied because of exceeding access rate'")),
                         "Access denied because of exceeding access rate")
        self.assertEqual(clean_reply(Exception("Couldn't parse the JSON response received from the server: "
                                               "b'<html><body><h1>502 Bad Gateway</h1></body></html>'")), "502 Bad Gateway")
        self.assertEqual(clean_reply(Exception("Couldn't parse the JSON response received from the server: b''")), "an unreadable reply")
        self.assertEqual(clean_reply("WHIRLPOOL: the broker gave no usable answer after 3 attempts "
                                     "(last reply: Something Went Wrong, Please Try After Sometime; delay now 8.0s)"),
                         "Something Went Wrong, Please Try After Sometime")
        self.assertEqual(clean_reply("  lots   of \n space  "), "lots of space")
        self.assertEqual(clean_reply(""), "no reply text")
        self.assertIn("3", clean_reply("gave up after 3 tries"))                                     # digits are kept, never '#'
        self.assertNotIn("#", clean_reply("gave up after 3 tries"))

    def test_long_replies_are_cut_at_a_word_not_in_the_middle_of_one(self):
        long = " ".join(["Something", "went", "wrong", "inside", "the", "gateway"] * 20)
        out = clean_reply(long)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 141)
        self.assertTrue(long.startswith(out[:-1]))
        self.assertIn(long[len(out) - 1], " ")                                                      # the cut fell on a space

    def test_failures_are_classified(self):
        self.assertEqual(classify_failure(KeyError("Symbol HFCL not found in Angel One instrument master")),
                         ("notfound", "Symbol HFCL not found in Angel One instrument master"))
        self.assertEqual(classify_failure(KeyError("some other key")), ("permanent", "some other key"))
        kind, words = classify_failure(BrokerRefused("WHIRLPOOL", 3, "Access denied because of exceeding access rate"))
        self.assertEqual((kind, words), ("transient", "Access denied because of exceeding access rate"))
        self.assertEqual(classify_failure(BrokerRefused("X", 3, "Invalid Token"))[0], "permanent")
        self.assertEqual(classify_failure(RuntimeError("Read timed out"))[0], "transient")

    def test_reading_candle_replies(self):
        self.assertEqual(read_candle_reply(candle_reply(2))[:2], (True, candle_reply(2)["data"]))
        self.assertEqual(read_candle_reply({"status": True, "data": []})[:2], (True, []))          # really no candles
        ok, candles, why = read_candle_reply(REFUSAL)
        self.assertFalse(ok)
        self.assertIn("Something Went Wrong", why)
        self.assertFalse(read_candle_reply(None)[0])
        self.assertFalse(read_candle_reply({"status": False, "data": []})[0])                      # a refusal with an empty list
        rows = candle_reply(2)["data"]
        for flag in (None, "true", "True", 1):                                                    # candles present = success, however the flag is spelt
            self.assertEqual(read_candle_reply({"status": flag, "data": rows})[:2], (True, rows), flag)
        self.assertEqual(read_candle_reply({"status": "true", "data": []})[:2], (True, []))

    def test_reading_quote_records(self):
        good = {"ltp": 100, "open": 99, "high": 101, "low": 98, "close": 99.5, "tradeVolume": 5000}
        rec = read_quote_record(good)
        self.assertEqual((rec["ltp"], rec["prev_close"], rec["volume"]), (100.0, 99.5, 5000.0))
        self.assertEqual(read_quote_record({**good, "ltp": 105})["high"], 105.0)                   # the range always contains the last price
        for bad in ({**good, "ltp": 0}, {**good, "tradeVolume": None}, {**good, "close": "x"}, {}, None):
            self.assertIsNone(read_quote_record(bad))


class PaceRules(unittest.TestCase):
    """The pace follows what the broker is doing now - never its whole history."""

    def feed(self, pace, pattern):
        """'.' = answered, 'x' = refused, 'r' = refused with a 'rate' complaint."""
        for ch in pattern:
            pace.record(ch in "xr", ch == "r")

    def test_scattered_refusals_never_slow_it_down(self):
        p = Pace(0.4)
        for ch in ".x...x....x.....x...x....x..x.....x.x...":                                      # about one call in five refused
            self.feed(p, ch)
            self.assertEqual(p.delay, 0.4, ch)

    def test_three_in_a_row_slows_it_and_the_next_answer_ends_the_streak(self):
        p = Pace(0.4)
        self.feed(p, "..........xxx")
        self.assertEqual(p.delay, Pace.SLOW)
        self.feed(p, ".")
        self.assertEqual(p.delay, 0.4)                                                              # 3 of 14 is not a bad patch

    def test_six_in_a_row_is_the_slowest_level(self):
        p = Pace(0.4)
        self.feed(p, "xxxxxx")
        self.assertEqual(p.delay, Pace.SLOWEST)

    def test_two_rate_complaints_go_straight_to_the_slowest_level(self):
        p = Pace(0.4)
        self.feed(p, "....r..r")
        self.assertEqual(p.delay, Pace.SLOWEST)
        p = Pace(0.4)
        self.feed(p, "....r......")
        self.assertEqual(p.delay, 0.4)                                                              # one complaint alone does not

    def test_a_bad_patch_is_forgotten_after_twenty_calls(self):
        p = Pace(0.4)
        self.feed(p, "xxxxxxxxx")
        self.assertEqual(p.delay, Pace.SLOWEST)
        self.feed(p, "." * 14)
        self.assertEqual(p.delay, 0.4)

    def test_counters(self):
        p = Pace(0.4)
        self.feed(p, "..xr.")
        self.assertEqual((p.calls, p.refusals, p.rate_limited), (5, 2, 1))


class CandleCalls(NoSleep):
    def setUp(self):
        super().setUp()
        self.c = FakeClient()

    def test_a_refused_answer_is_a_failure_never_an_empty_success(self):
        self.c.client.getCandleData.return_value = REFUSAL
        with self.assertRaises(BrokerRefused) as cm:
            self.c.get_historical_bars("AAA", days=250)
        self.assertEqual(cm.exception.reply, "Something Went Wrong, Please Try After Sometime [AB1004]")
        self.assertIn("Something Went Wrong", str(cm.exception))
        self.assertTrue(is_transient_error(cm.exception))                                         # so the fetch loop counts it as throttling
        self.assertEqual(self.c.client.getCandleData.call_count, 3)
        self.assertEqual(self.c.pace.refusals, 3)
        self.assertEqual(sum(self.c.error_counts.values()), 3)
        self.assertEqual(list(self.c.error_examples.values()), ["Something Went Wrong, Please Try After Sometime [AB1004]"])

    def test_a_retry_waits_a_short_fixed_pause_and_a_longer_one_when_the_broker_said_rate(self):
        self.c.client.getCandleData.return_value = REFUSAL
        with self.assertRaises(BrokerRefused):
            self.c.get_historical_bars("AAA", days=250)
        self.assertEqual(self.sleeps[0], 0.4)                                                      # normal pacing before the first try
        self.assertGreaterEqual(self.sleeps[1], 1.0)
        self.assertGreaterEqual(self.sleeps[2], 2.0)
        self.assertLess(max(self.sleeps), 3.0)                                                     # but never the old 8-second crawl
        self.sleeps.clear()
        self.c.pace = Pace(0.4)
        self.c.client.getCandleData.return_value = RATE_REFUSAL
        with self.assertRaises(BrokerRefused):
            self.c.get_historical_bars("AAA", days=250)
        self.assertGreaterEqual(self.sleeps[1], 3.0)
        self.assertGreaterEqual(self.sleeps[2], 6.0)

    def test_a_real_empty_answer_is_an_empty_frame_and_causes_no_slowdown(self):
        self.c.client.getCandleData.return_value = {"status": True, "message": "SUCCESS", "data": []}
        df = self.c.get_historical_bars("AAA", days=250)
        self.assertTrue(df.empty)
        self.assertEqual(self.c.pace.delay, 0.4)

    def test_one_refusal_followed_by_an_answer_leaves_no_slowdown(self):
        self.c.client.getCandleData.side_effect = [REFUSAL, candle_reply(3)]
        df = self.c.get_historical_bars("AAA", days=2)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(df), 2)                                                              # tail(days)
        self.assertEqual(self.c.pace.delay, 0.4)                                                  # THE regression: this used to leave 0.4 x 1.8

    def test_exceptions_retry_only_when_transient(self):
        self.c.client.getCandleData.side_effect = [Exception("Access denied because of exceeding access rate"), candle_reply(3)]
        self.assertEqual(len(self.c.get_historical_bars("AAA", days=250)), 3)
        self.c.client.getCandleData.reset_mock()
        self.c.client.getCandleData.side_effect = Exception("Invalid symboltoken 123")
        with self.assertRaises(Exception):
            self.c.get_historical_bars("AAA", days=250)
        self.assertEqual(self.c.client.getCandleData.call_count, 1)                               # no pointless retries

    def test_a_stock_that_always_fails_then_clean_answers_return_to_full_speed(self):
        self.c.client.getCandleData.return_value = REFUSAL
        for sym in ("AAA", "BBB", "CCC"):
            with self.assertRaises(BrokerRefused):
                self.c.get_historical_bars(sym, days=250)
        self.assertEqual(self.c.pace.delay, Pace.SLOWEST)                                         # nine refusals in a row
        self.c.client.getCandleData.return_value = candle_reply(3)
        for _ in range(14):
            self.c.get_historical_bars("AAA", days=250)
        self.assertEqual(self.c.pace.delay, 0.4)

    def test_unknown_symbol_raises_key_error_without_touching_the_broker(self):
        with self.assertRaises(KeyError):
            self.c.get_historical_bars("BADCO", days=250)
        self.c.client.getCandleData.assert_not_called()

    def test_the_pace_report(self):
        self.c.client.getCandleData.side_effect = [REFUSAL, candle_reply(3), candle_reply(3)]
        self.c.get_historical_bars("AAA", days=250)
        self.c.get_historical_bars("BBB", days=250)
        rep = self.c.pace_report()
        self.assertEqual((rep["calls"], rep["refused"], rep["rate_limited"], rep["slowed_calls"]), (3, 1, 0, 0))


class QuoteCalls(NoSleep):
    def setUp(self):
        super().setUp()
        self.c = FakeClient()

    @staticmethod
    def entry(token, price=100.0, **kw):
        return {"exchange": "NSE", "tradingSymbol": token[1:] + "-EQ", "symbolToken": token, "ltp": price, "open": price * 0.99,
                "high": price * 1.01, "low": price * 0.98, "close": price * 0.995, "tradeVolume": 123456, **kw}

    def test_fifty_stocks_per_call(self):
        symbols = [f"S{i}" for i in range(120)]
        seen = []

        def reply(mode, tokens):
            seen.append((mode, len(tokens["NSE"])))
            return {"status": True, "message": "SUCCESS", "data": {"fetched": [self.entry(t) for t in tokens["NSE"]], "unfetched": []}}
        self.c.client.getMarketData.side_effect = reply
        out = self.c.get_quotes(symbols)
        self.assertEqual(seen, [("FULL", 50), ("FULL", 50), ("FULL", 20)])
        self.assertEqual(len(out), 120)
        self.assertEqual(out["S7"]["prev_close"], 99.5)

    def test_a_refused_chunk_is_retried_and_unknown_symbols_are_skipped(self):
        ok = {"status": True, "data": {"fetched": [self.entry("TAAA"), self.entry("TBBB", 200.0)], "unfetched": []}}
        self.c.client.getMarketData.side_effect = [REFUSAL, ok]
        out = self.c.get_quotes(["AAA", "BBB", "BADCO"])
        self.assertEqual(set(out), {"AAA", "BBB"})
        self.assertEqual(self.c.client.getMarketData.call_count, 2)

    def test_unusable_entries_are_dropped_and_nothing_usable_raises(self):
        mixed = {"status": True, "data": {"fetched": [self.entry("TAAA"), self.entry("TBBB", ltp=0), self.entry("TCCC", tradeVolume=None)]}}
        self.c.client.getMarketData.return_value = mixed
        self.assertEqual(set(self.c.get_quotes(["AAA", "BBB", "CCC"])), {"AAA"})
        self.c.client.getMarketData.return_value = REFUSAL
        with self.assertRaises(RuntimeError):
            self.c.get_quotes(["AAA"])


# --------------------------------------------------------------------------- #
def bars_frame():
    idx = pd.bdate_range(end="2026-10-13", periods=5).tz_localize("Asia/Kolkata")
    return pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}, index=idx)


class ScriptedStore(HistoricalStore):
    COOLDOWN_S = 0.0
    RETRY_PAUSE_S = 0.0

    def __init__(self, script):
        self.script = script
        self.calls = Counter()

    def get_bars(self, symbol, lookback_days):
        self.calls[symbol] += 1
        out = self.script(symbol, self.calls[symbol])
        if isinstance(out, Exception):
            raise out
        return out


class UniverseFetch(NoSleep):
    SYMS = [f"S{i:03d}" for i in range(60)]

    def test_it_cools_down_carries_on_and_recovers_the_refused_stocks_in_a_second_pass(self):
        def script(sym, n):
            i = int(sym[1:])
            if 10 <= i < 30 and n == 1:
                return RuntimeError("S: the broker gave no usable answer after 3 attempts (last reply: Something Went Wrong, Please Try After Sometime)")
            return bars_frame()
        store = ScriptedStore(script)
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual(len(out), 60)
        self.assertEqual((rep["loaded"], rep["failed"], rep["stopped"]), (60, 0, ""))
        self.assertEqual(rep["cooldowns"], 1)                                                     # 20 refusals in a row tripped it once
        self.assertEqual(rep["recovered"], 20)
        self.assertEqual((rep["refused_n"], rep["not_reached"]), (0, 0))
        self.assertEqual(rep["errors"][0][0], "Something Went Wrong, Please Try After Sometime")  # the broker's words, not our wrapper

    def test_it_stops_and_says_why_when_the_broker_never_recovers(self):
        class Never(ScriptedStore):
            MAX_COOLDOWNS = 1
            BREAKER_CONSECUTIVE = 5
        store = Never(lambda sym, n: BrokerRefused(sym, 3, "Access denied because of exceeding access rate"))
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual(out, {})
        self.assertEqual(rep["stopped"], "the broker kept refusing requests")
        self.assertEqual(rep["cooldowns"], 1)
        self.assertEqual(rep["errors"], [("Access denied because of exceeding access rate", 10)])  # 5 before and 5 after the cool-down
        self.assertLess(sum(store.calls.values()), 20)                                            # it did not hammer the broker
        self.assertEqual(rep["not_reached"], len(self.SYMS) - 10)                                 # said plainly how many were never tried
        self.assertEqual(rep["refused_n"], 10)

    def test_same_words_from_different_stocks_are_one_error(self):
        store = ScriptedStore(lambda sym, n: BrokerRefused(sym, 3, "Rate limit hit, wait 12 seconds") if int(sym[1:]) < 6 else bars_frame())
        store.get_universe_bars(self.SYMS, 320)
        self.assertEqual(len(store.last_report["errors"]), 1)                                     # the stock names and the digits do not split them
        self.assertTrue(store.last_report["errors"][0][1] >= 6)

    def test_permanent_errors_do_not_trip_the_breaker(self):
        def script(sym, n):
            return BrokerRefused(sym, 3, "Invalid Token") if int(sym[1:]) < 40 else bars_frame()
        store = ScriptedStore(script)
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual((len(out), rep["cooldowns"], rep["stopped"], rep["recovered"]), (20, 0, "", 0))
        self.assertEqual(sum(store.calls.values()), 60)                                           # each tried once, none retried

    def test_stocks_the_broker_does_not_know_are_named_not_blamed_on_the_broker(self):
        def script(sym, n):
            return KeyError(f"Symbol {sym} not found in Angel One instrument master") if sym in ("S003", "S017", "S041") else bars_frame()
        store = ScriptedStore(script)
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual(len(out), 57)
        self.assertEqual((rep["not_found"], rep["not_found_n"]), (["S003", "S017", "S041"], 3))
        self.assertEqual((rep["errors"], rep["refused_n"], rep["cooldowns"], rep["stopped"]), ([], 0, 0, ""))
        self.assertEqual(sum(store.calls.values()), 60)

    def test_empty_but_valid_answers_are_not_failures(self):
        store = ScriptedStore(lambda sym, n: pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
        out = store.get_universe_bars(self.SYMS, 320)
        self.assertEqual((out, store.last_report["cooldowns"], store.last_report["stopped"]), ({}, 0, ""))

    def test_the_time_budget_stops_gracefully_and_counts_what_was_never_reached(self):
        store = ScriptedStore(lambda sym, n: bars_frame())
        out = store.get_universe_bars(self.SYMS, 320, max_duration_seconds=0)
        rep = store.last_report
        self.assertEqual((out, rep["stopped"], rep["not_reached"]), ({}, "time budget", 60))

    def test_a_store_without_quotes_says_so(self):
        with self.assertRaises(NotImplementedError):
            ScriptedStore(lambda s, n: None).get_quotes(["A"])


# --------------------------------------------------------------------------- #
class VirtualClock:
    """Time the test controls: sleeping and broker latency advance it, nothing really waits."""

    def __init__(self):
        self.now = 1_000_000.0

    def sleep(self, s):
        self.now += s

    def time(self):
        return self.now


class VirtualBroker:
    """Stands in for the SmartAPI SDK: answers after `latency` seconds, refusing according to `refuse(elapsed_s, rng)`."""

    def __init__(self, clock, refuse, latency=0.35, seed=7):
        self.clock, self.refuse, self.latency = clock, refuse, latency
        self.rng = random.Random(seed)
        self.t0 = clock.now
        self.calls = 0

    def getCandleData(self, params):
        self.calls += 1
        self.clock.now += self.latency
        if self.refuse(self.clock.now - self.t0, self.rng):
            return REFUSAL
        return candle_reply(3)


class SimulatedStore(HistoricalStore):
    """The real fetch loop and the real client over a simulated broker (the store's own waits are the real ones, on the virtual clock)."""

    def __init__(self, broker):
        self._client = FakeClient()
        self._client.client = broker

    def get_bars(self, symbol, lookback_days):
        return self._client.get_historical_bars(symbol, days=lookback_days)

    def pace_report(self):
        return self._client.pace_report()


class PaceSimulation(unittest.TestCase):
    """
    The 2026-10-23 failure, reproduced on a virtual clock. The broker refuses a share of calls even when nothing is
    wrong; the old throttle multiplied the wait by 1.8 on each refusal and the Swing scan of 501 stocks then took more
    than 90 minutes (and was cut off with the last 53 unfetched). A healthy fetch must take about ten minutes.
    """
    UNIVERSE = [f"S{i:03d}" for i in range(501)]

    def run_fetch(self, refuse, budget=2700, seed=7):
        clock = VirtualClock()
        broker = VirtualBroker(clock, refuse, seed=seed)
        store = SimulatedStore(broker)
        with mock.patch("time.sleep", clock.sleep), mock.patch("time.time", clock.time):
            out = store.get_universe_bars(self.UNIVERSE, 320, max_duration_seconds=budget)
        return out, store.last_report, broker, clock

    def test_a_healthy_broker_takes_about_six_minutes(self):
        out, rep, broker, _ = self.run_fetch(lambda t, r: False)
        self.assertEqual((len(out), rep["stopped"]), (501, ""))
        self.assertLess(rep["seconds"], 8 * 60)
        self.assertEqual(broker.calls, 501)

    def test_one_call_in_six_refused_at_random_still_finishes_in_about_ten_minutes(self):
        out, rep, broker, _ = self.run_fetch(lambda t, r: r.random() < 0.17)
        self.assertEqual(rep["stopped"], "")
        self.assertGreaterEqual(len(out), 495)                                                    # retries recover nearly everything
        self.assertLess(rep["seconds"], 14 * 60)                                                  # the old throttle needed 78+ minutes here
        self.assertEqual(rep["cooldowns"], 0)
        self.assertLess(rep["pace"]["slowed_calls"], 0.25 * rep["pace"]["calls"])                 # it stayed at full speed almost throughout

    def test_a_three_minute_outage_is_waited_out_and_the_stocks_are_recovered(self):
        out, rep, _, _ = self.run_fetch(lambda t, r: 120 <= t < 300 or r.random() < 0.05)
        self.assertEqual(rep["stopped"], "")
        self.assertGreaterEqual(rep["cooldowns"], 1)
        self.assertGreaterEqual(len(out), 495)
        self.assertLess(rep["seconds"], 20 * 60)

    def test_a_broker_that_never_answers_is_given_up_on_quickly_and_in_its_own_words(self):
        out, rep, broker, _ = self.run_fetch(lambda t, r: True)
        self.assertEqual(out, {})
        self.assertEqual(rep["stopped"], "the broker kept refusing requests")
        self.assertLess(rep["seconds"], 15 * 60)
        self.assertIn("Something Went Wrong", rep["errors"][0][0])
        self.assertGreater(rep["not_reached"], 400)
        self.assertLess(broker.calls, 300)                                                        # no hammering

    def test_the_time_budget_ends_the_run_and_reports_how_many_were_never_reached(self):
        out, rep, _, _ = self.run_fetch(lambda t, r: r.random() < 0.17, budget=120)
        self.assertEqual(rep["stopped"], "time budget")
        self.assertEqual(len(out) + rep["not_reached"] + rep["refused_n"], 501)
        self.assertGreater(rep["not_reached"], 300)


# --------------------------------------------------------------------------- #
class SavedCandles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "h.csv.gz")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self):
        a, b = bars_frame(), bars_frame() * 2
        a.iloc[2, a.columns.get_loc("close")] = np.nan                                            # a gap row is dropped, not saved
        n = history_cache.save({"AAA": a, "BBB": b, "ZZZ": bars_frame()}, ["AAA", "BBB", "MISSING"], path=self.path)
        self.assertEqual(n, 2)
        hist, as_of = history_cache.load(self.path)
        self.assertEqual((set(hist), as_of), ({"AAA", "BBB"}, "2026-10-13"))
        self.assertEqual(len(hist["AAA"]), 4)
        self.assertEqual(str(hist["BBB"].index.tz), "Asia/Kolkata")
        self.assertEqual(float(hist["BBB"]["close"].iloc[-1]), 3.0)
        self.assertEqual(float(hist["BBB"]["volume"].iloc[-1]), 20.0)

    def test_nothing_to_save_writes_nothing_and_a_missing_file_loads_empty(self):
        self.assertEqual(history_cache.save({}, ["AAA"], path=self.path), 0)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(history_cache.load(self.path), ({}, None))

    def test_keeps_only_the_newest_bars(self):
        idx = pd.bdate_range(end="2026-10-13", periods=400).tz_localize("Asia/Kolkata")
        big = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}, index=idx)
        history_cache.save({"AAA": big}, ["AAA"], keep=320, path=self.path)
        self.assertEqual(len(history_cache.load(self.path)[0]["AAA"]), 320)

    def test_merging_live_quotes(self):
        hist = {s: bars_frame() for s in ("OK", "SPLIT", "NOQUOTE", "HASTODAY")}
        today = date(2026, 10, 14)
        # one cached stock already carries a bar for "today" (e.g. a late evening run): it is replaced, not duplicated
        extra = bars_frame().iloc[[-1]].copy()
        extra.index = extra.index + pd.Timedelta(days=1)
        hist["HASTODAY"] = pd.concat([hist["HASTODAY"], extra])
        q = {"ltp": 1.6, "open": 1.5, "high": 1.7, "low": 1.4, "prev_close": 1.5, "volume": 7.0}
        quotes = {"OK": q, "SPLIT": {**q, "prev_close": 0.75}, "HASTODAY": q}
        merged, rep = history_cache.merge_live(hist, quotes, today)
        self.assertEqual(set(merged), {"OK", "HASTODAY"})
        self.assertEqual((rep["no_quote"], rep["mismatch"]), (["NOQUOTE"], ["SPLIT"]))
        for s in merged:
            m = merged[s]
            self.assertEqual(len(m), 6)                                                           # 5 saved + today
            self.assertEqual(m.index[-1].date(), today)
            self.assertEqual((float(m["close"].iloc[-1]), float(m["volume"].iloc[-1])), (1.6, 7.0))
        # inside the 0.3% tolerance still merges; outside does not
        close_enough = {"OK": {**q, "prev_close": 1.5 * 1.002}}
        self.assertEqual(len(history_cache.merge_live({"OK": bars_frame()}, close_enough, today)[0]), 1)
        too_far = {"OK": {**q, "prev_close": 1.5 * 1.01}}
        self.assertEqual(len(history_cache.merge_live({"OK": bars_frame()}, too_far, today)[0]), 0)


if __name__ == "__main__":
    unittest.main()

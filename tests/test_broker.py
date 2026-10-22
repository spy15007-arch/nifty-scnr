"""
Broker-facing code, tested without a broker: the candle/quote client (a refused answer must never look like
"no data"), the universe fetch (cool-down, second pass, honest report) and the saved-candles + live-quote merge.
Run:  python -m unittest discover tests -v
"""
import os
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
from data.brokers.angelone_client import (AngelOneDataClient, is_transient_error, read_candle_reply,  # noqa: E402
                                          read_quote_record)
from data.historical import HistoricalStore  # noqa: E402

REFUSAL = {"status": False, "message": "Something Went Wrong, Please Try After Sometime", "errorcode": "AB1004", "data": None}


def candle_reply(n=3):
    rows = [[f"2026-10-{10 + i:02d}T00:00:00+05:30", 100 + i, 101 + i, 99 + i, 100.5 + i, 1000 * (i + 1)] for i in range(n)]
    return {"status": True, "message": "SUCCESS", "errorcode": "", "data": rows}


class FakeClient(AngelOneDataClient):
    """The real client's logic with the login and the SDK removed."""

    def __init__(self):
        self.client = mock.Mock()
        self._instrument_cache = {}
        self._current_delay = 0.4
        self._min_delay = 0.4
        self._max_delay = 8.0
        self._consecutive_successes = 0
        self._decay_after = 10
        self.error_counts = Counter()

    def _symbol_token(self, tradingsymbol, exchange="NSE"):
        if tradingsymbol.startswith("BAD"):
            raise KeyError(f"Symbol {tradingsymbol} not found in Angel One instrument master")
        return "T" + tradingsymbol


class NoSleep(unittest.TestCase):
    def setUp(self):
        p = mock.patch("time.sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)


class ErrorWords(unittest.TestCase):
    def test_which_errors_are_worth_a_retry(self):
        for msg in ("Access denied because of exceeding access rate", "Something Went Wrong, Please Try After Sometime",
                    "Too many requests", "Couldn't parse the JSON response received from the server: b'<html>Bad Gateway'",
                    "HTTPSConnectionPool(host='x'): Read timed out"):
            self.assertTrue(is_transient_error(msg), msg)
        for msg in ("Invalid symboltoken", "Symbol XYZ not found in Angel One instrument master", "Could not generate the report"):
            self.assertFalse(is_transient_error(msg), msg)

    def test_reading_candle_replies(self):
        self.assertEqual(read_candle_reply(candle_reply(2))[:2], (True, candle_reply(2)["data"]))
        self.assertEqual(read_candle_reply({"status": True, "data": []})[:2], (True, []))          # really no candles
        ok, candles, why = read_candle_reply(REFUSAL)
        self.assertFalse(ok)
        self.assertIn("Something Went Wrong", why)
        self.assertFalse(read_candle_reply(None)[0])
        self.assertFalse(read_candle_reply({"status": False, "data": []})[0])                      # a refusal with an empty list

    def test_reading_quote_records(self):
        good = {"ltp": 100, "open": 99, "high": 101, "low": 98, "close": 99.5, "tradeVolume": 5000}
        rec = read_quote_record(good)
        self.assertEqual((rec["ltp"], rec["prev_close"], rec["volume"]), (100.0, 99.5, 5000.0))
        self.assertEqual(read_quote_record({**good, "ltp": 105})["high"], 105.0)                   # the range always contains the last price
        for bad in ({**good, "ltp": 0}, {**good, "tradeVolume": None}, {**good, "close": "x"}, {}, None):
            self.assertIsNone(read_quote_record(bad))


class CandleCalls(NoSleep):
    def setUp(self):
        super().setUp()
        self.c = FakeClient()

    def test_a_refused_answer_is_a_failure_never_an_empty_success(self):
        self.c.client.getCandleData.return_value = REFUSAL
        with self.assertRaises(RuntimeError) as cm:
            self.c.get_historical_bars("AAA", days=250)
        self.assertIn("Something Went Wrong", str(cm.exception))
        self.assertTrue(is_transient_error(cm.exception))                                         # so the fetch loop counts it as throttling
        self.assertEqual(self.c.client.getCandleData.call_count, 3)
        self.assertGreater(self.c._current_delay, 2.0)                                            # it slowed down (0.4 x 1.8^3)
        self.assertEqual(sum(self.c.error_counts.values()), 3)

    def test_a_real_empty_answer_is_an_empty_frame_and_causes_no_slowdown(self):
        self.c.client.getCandleData.return_value = {"status": True, "message": "SUCCESS", "data": []}
        df = self.c.get_historical_bars("AAA", days=250)
        self.assertTrue(df.empty)
        self.assertEqual(self.c._current_delay, 0.4)

    def test_it_recovers_when_a_later_attempt_works(self):
        self.c.client.getCandleData.side_effect = [REFUSAL, candle_reply(3)]
        df = self.c.get_historical_bars("AAA", days=2)
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(df), 2)                                                              # tail(days)
        self.assertGreater(self.c._current_delay, 0.4)

    def test_exceptions_retry_only_when_transient(self):
        self.c.client.getCandleData.side_effect = [Exception("Access denied because of exceeding access rate"), candle_reply(3)]
        self.assertEqual(len(self.c.get_historical_bars("AAA", days=250)), 3)
        self.c.client.getCandleData.reset_mock()
        self.c.client.getCandleData.side_effect = Exception("Invalid symboltoken 123")
        with self.assertRaises(Exception):
            self.c.get_historical_bars("AAA", days=250)
        self.assertEqual(self.c.client.getCandleData.call_count, 1)                               # no pointless retries

    def test_the_delay_eases_back_after_a_clean_run(self):
        self.c._current_delay = 4.0
        self.c.client.getCandleData.return_value = candle_reply(3)
        for _ in range(10):
            self.c.get_historical_bars("AAA", days=250)
        self.assertLess(self.c._current_delay, 4.0)

    def test_unknown_symbol_raises_key_error_without_touching_the_broker(self):
        with self.assertRaises(KeyError):
            self.c.get_historical_bars("BADCO", days=250)
        self.c.client.getCandleData.assert_not_called()


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
        self.assertIn("something went wrong", rep["errors"][0][0].lower())

    def test_it_stops_and_says_why_when_the_broker_never_recovers(self):
        class Never(ScriptedStore):
            MAX_COOLDOWNS = 1
            BREAKER_CONSECUTIVE = 5
        store = Never(lambda sym, n: RuntimeError("Access denied because of exceeding access rate"))
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual(out, {})
        self.assertEqual(rep["stopped"], "the broker kept refusing requests")
        self.assertEqual(rep["cooldowns"], 1)
        self.assertEqual(rep["errors"][0][1], 10)                                                 # 5 before and 5 after the cool-down
        self.assertLess(sum(store.calls.values()), 20)                                            # it did not hammer the broker

    def test_permanent_errors_do_not_trip_the_breaker(self):
        def script(sym, n):
            return KeyError(f"Symbol {sym} not found in Angel One instrument master") if int(sym[1:]) < 40 else bars_frame()
        store = ScriptedStore(script)
        out = store.get_universe_bars(self.SYMS, 320)
        rep = store.last_report
        self.assertEqual((len(out), rep["cooldowns"], rep["stopped"], rep["recovered"]), (20, 0, "", 0))
        self.assertEqual(sum(store.calls.values()), 60)                                           # each tried once, none retried

    def test_empty_but_valid_answers_are_not_failures(self):
        store = ScriptedStore(lambda sym, n: pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
        out = store.get_universe_bars(self.SYMS, 320)
        self.assertEqual((out, store.last_report["cooldowns"], store.last_report["stopped"]), ({}, 0, ""))

    def test_the_time_budget_stops_gracefully(self):
        store = ScriptedStore(lambda sym, n: bars_frame())
        out = store.get_universe_bars(self.SYMS, 320, max_duration_seconds=0)
        self.assertEqual((out, store.last_report["stopped"]), ({}, "time budget"))

    def test_a_store_without_quotes_says_so(self):
        with self.assertRaises(NotImplementedError):
            ScriptedStore(lambda s, n: None).get_quotes(["A"])


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

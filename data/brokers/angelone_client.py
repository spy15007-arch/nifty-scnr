"""
Angel One SmartAPI wrapper. Requires: pip install smartapi-python pyotp

WHAT WENT WRONG, AND WHAT EACH FIX DOES
---------------------------------------
2026-10-21 - a refused answer was read as "no data".
  The SmartAPI SDK does NOT raise when the broker answers {"status": false, "message": "...", "data": null}:
  it only logs the error and hands the dictionary back. The old code then read `response.get("data", [])`, saw
  "no candles" and reported SUCCESS with an empty table. The stock was silently dropped and no circuit breaker
  ever tripped. That is how the 2 PM BTST scan of 501 stocks became a scan of 11 stocks. Now a refused answer is
  a refusal: it is retried, and if the broker keeps refusing, the error carries the broker's own words (they end
  up in the run note). Only {"status": true, "data": []} means "this stock really has no candles".

2026-10-23 - the cure made the Swing scan take 90+ minutes.
  Every refusal multiplied the wait between calls by 1.8 and it only eased back after 10 clean calls in a row.
  The broker refuses a few calls in a hundred even when nothing is wrong (gateway hiccups), so the wait sat at its
  8-second ceiling all evening: 501 stocks did not fit in 90 minutes and the last 53 were never fetched.
  Now the pace follows what the broker is doing RIGHT NOW (`Pace`): full speed while it answers, slower only
  during a real run of refusals or an explicit "rate" complaint, back to full speed once it answers again. A single
  refused call is simply retried after a short pause (1 s, then 2 s) - it does not slow every call that follows.
  On a simulated broker that refuses 17% of calls at random, 501 stocks take about 9 minutes instead of 78
  (tests/test_broker.py::PaceSimulation).

LIVE QUOTES: `get_quotes` returns today's price, open/high/low, previous close and traded volume for up to 50
stocks per call. The afternoon BTST scan uses it instead of 150 slow candle calls (see data/history_cache.py).
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter, deque
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# words the broker or its gateway uses when it is throttling or briefly unwell (matched case-insensitively)
_TRANSIENT = re.compile(
    r"\brate\b|too many|ab1021|ab1004|access denied|exceeding|something went wrong|try after|try again|try later|"
    r"timed out|timeout|temporar|unavailable|\bbusy\b|gateway|connection|couldn't parse|server error|"
    r"no data and no message|unexpected reply",
    re.IGNORECASE,
)
# the subset that says "you are calling too fast" (as opposed to "the server had a hiccup")
_RATE = re.compile(r"\brate\b|too many|ab1021|exceeding|access denied", re.IGNORECASE)
# replies that say this request can never succeed ("not found", "invalid ...") - the broker ANSWERED, so no retry and no slowdown
_PERMANENT = re.compile(r"not found|invalid|does not exist|no such", re.IGNORECASE)

# the SDK's message when the gateway answers with something that is not JSON (an HTML error page, plain text)
_SDK_UNREADABLE = re.compile(r"couldn'?t parse the json response received from the server:?\s*", re.IGNORECASE)
# our own wrapper around a broker reply, so a plain RuntimeError carrying it groups with the real thing
_WRAPPED = re.compile(r"gave no usable answer after \d+ attempts? \(last reply: (.*?)(?:; delay now [\d.]+s)?\)\s*$", re.IGNORECASE | re.DOTALL)

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


def is_transient_error(error) -> bool:
    """True for throttling / a briefly unwell broker (worth a pause and another try), False for a permanent error."""
    return bool(_TRANSIENT.search(str(error)))


def is_permanent_reply(error) -> bool:
    """True when the broker's words say the request itself is the problem ('not found', 'invalid ...'): retrying cannot help."""
    text = str(error)
    return bool(_PERMANENT.search(text)) and not _TRANSIENT.search(text)


def is_rate_limit_error(error) -> bool:
    """True when the broker's words say 'too many / too fast' (rate, exceeding, access denied)."""
    return bool(_RATE.search(str(error)))


def _is_rate_limit_error(e: Exception) -> bool:       # kept for older callers
    return is_transient_error(e)


def clean_reply(error, limit: int = 140) -> str:
    """
    The broker's words, tidied for a person: no wrapping quotes (KeyError adds its own), no b'...' byte-string noise,
    no HTML tags from a gateway error page, no repeated spaces, cut at a word boundary. Digits are left alone.
    """
    text = str(error if error is not None else "").strip()
    m = _WRAPPED.search(text)
    if m:
        text = m.group(1).strip()
    unreadable = bool(_SDK_UNREADABLE.search(text))
    text = _SDK_UNREADABLE.sub("", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\\[nrt]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for _ in range(3):                                    # b'...' / "..." / '...' wrappers, possibly nested
        if len(text) >= 3 and text[0] == "b" and text[1] in "'\"" and text[-1] == text[1]:
            text = text[2:-1].strip()
        elif len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0]:
            text = text[1:-1].strip()
        else:
            break
    if not text:
        text = "an unreadable reply" if unreadable else "no reply text"
    if len(text) > limit:
        head = text[:limit]
        head = head.rsplit(" ", 1)[0] if " " in head else head
        text = head.rstrip(" ,;:-") + "…"
    return text


def _normalise(msg) -> str:
    """Grouping key for 'the same reply': digits and case do not matter."""
    return re.sub(r"\d+", "#", clean_reply(msg, 200).lower())[:90]


def _truthy(flag) -> bool:
    return flag is True or (isinstance(flag, (str, int)) and str(flag).strip().lower() in ("true", "1"))


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def read_candle_reply(response) -> tuple[bool, list, str]:
    """(ok, candles, why_not). ok=True with an empty list means the broker really has no candles for the range."""
    if not isinstance(response, dict):
        return False, [], f"unexpected reply of type {type(response).__name__}"
    status, data = response.get("status"), response.get("data")
    if isinstance(data, list) and data:                              # candles present: success, whatever the flag looks like
        return True, data, ""
    if _truthy(status):                                              # success with nothing in the range
        return True, [], ""
    msg = response.get("message") or response.get("error") or response.get("errorcode") or "no data and no message"
    code = response.get("errorcode")
    return False, [], f"{msg}" + (f" [{code}]" if code and str(code) not in str(msg) else "")


def read_quote_record(q: dict) -> dict | None:
    """One entry of the broker's FULL market-data reply -> a clean record, or None when it is unusable."""
    if not isinstance(q, dict):
        return None
    ltp, op, hi, lo = _num(q.get("ltp")), _num(q.get("open")), _num(q.get("high")), _num(q.get("low"))
    prev, vol = _num(q.get("close")), _num(q.get("tradeVolume"))
    if None in (ltp, op, hi, lo, prev, vol) or min(ltp, op, hi, lo, prev) <= 0 or vol < 0:
        return None
    hi, lo = max(hi, ltp, op), min(lo, ltp, op)           # the day's range always contains the open and the last price
    return {"ltp": ltp, "open": op, "high": hi, "low": lo, "prev_close": prev, "volume": vol}


class BrokerRefused(RuntimeError):
    """The broker kept refusing one stock's request. `reply` is its last answer, in its own words."""

    def __init__(self, symbol: str, attempts: int, reply: str):
        self.symbol, self.attempts, self.reply = symbol, attempts, reply
        super().__init__(f"{symbol}: the broker gave no usable answer after {attempts} attempt{'' if attempts == 1 else 's'} (last reply: {reply})")


class Pace:
    """
    How long to wait between broker calls, judged on what the broker is doing NOW (its last 20 answers) - never on
    its whole history, so one bad patch cannot slow the rest of a run.

        level 0  (the normal case)         wait `base` (0.4 s)
        level 1  3 refusals in a row, or 35%+ of the last 20 calls refused         wait 1.2 s
        level 2  6 in a row, or 60%+ of the last 20, or two "rate" complaints      wait 2.5 s

    A refusal that is followed by an answer costs nothing but its own retry: the streak resets on the next success and
    the window empties within 20 calls, so the pace is back at full speed about a minute after the broker recovers.
    """
    SLOW = 1.2
    SLOWEST = 2.5
    WINDOW = 20
    MIN_SAMPLE = 5            # the share-based rules need at least this many calls in the window

    def __init__(self, base: float = 0.4):
        self.base = float(base)
        self._recent: deque = deque(maxlen=self.WINDOW)       # True = the call was refused
        self._rate: deque = deque(maxlen=self.WINDOW)         # True = refused AND the words said "rate / too many / exceeding"
        self._streak = 0
        self.calls = 0
        self.refusals = 0
        self.rate_limited = 0
        self.at_level = Counter()                             # how many calls were made at each level

    @property
    def level(self) -> int:
        n = len(self._recent)
        share = (sum(self._recent) / n) if n >= self.MIN_SAMPLE else 0.0
        if self._streak >= 6 or share >= 0.60 or sum(self._rate) >= 2:
            return 2
        if self._streak >= 3 or share >= 0.35:
            return 1
        return 0

    @property
    def delay(self) -> float:
        return max(self.base, (0.0, self.SLOW, self.SLOWEST)[self.level])

    def record(self, refused: bool, rate_limited: bool = False, level: int | None = None) -> None:
        self.calls += 1
        self.at_level[self.level if level is None else level] += 1
        self._recent.append(bool(refused))
        self._rate.append(bool(refused and rate_limited))
        if refused:
            self.refusals += 1
            self._streak += 1
            if rate_limited:
                self.rate_limited += 1
        else:
            self._streak = 0


class AngelOneDataClient:
    MAX_ATTEMPTS = 3
    RETRY_BACKOFF_S = (1.0, 2.0)   # extra pause before the 2nd and 3rd attempt of the same request
    RATE_BACKOFF_MULT = 3.0        # ... multiplied by this when the broker's words were "rate / too many / exceeding"
    QUOTE_PAUSE_S = 1.1            # the quote endpoint allows about one call a second
    QUOTE_CHUNK = 50               # tokens per quote call (the broker's maximum)

    def __init__(self, api_key: str, client_id: str, password: str, totp_secret: str):
        from SmartApi import SmartConnect
        import pyotp

        totp = pyotp.TOTP(totp_secret).now()
        self.client = SmartConnect(api_key=api_key)
        session = self.client.generateSession(client_id, password, totp)
        if not session.get("status"):
            raise RuntimeError(f"Angel One login failed: {session.get('message')}")

        self._instrument_cache: dict[str, str] = {}
        self.pace = Pace(0.4)
        self.error_counts: Counter = Counter()
        self.error_examples: dict[str, str] = {}

    INDEX_ALIASES = {
        "NIFTY": "Nifty 50",
        "BANKNIFTY": "Nifty Bank",
        "FINNIFTY": "Nifty Fin Service",
        "MIDCPNIFTY": "Nifty Midcap Select",
        "SENSEX": "SENSEX",
    }

    def _symbol_token(self, tradingsymbol: str, exchange: str = "NSE") -> str:
        import requests

        if not self._instrument_cache:
            urls = [
                "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json",
                "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
            ]
            data = None
            last_error = None
            for url in urls:
                try:
                    resp = requests.get(url, timeout=30)
                    data = resp.json()
                    break
                except Exception as e:  # noqa: BLE001
                    last_error = e
                    continue
            if data is None:
                raise RuntimeError(f"Could not fetch Angel One instrument master from either URL: {last_error}")

            for row in data:
                if row.get("exch_seg") == exchange:
                    self._instrument_cache[row["symbol"]] = row["token"]

        alias = self.INDEX_ALIASES.get(tradingsymbol.upper())
        if alias and alias in self._instrument_cache:
            return self._instrument_cache[alias]

        key = f"{tradingsymbol}-EQ" if not tradingsymbol.endswith("-EQ") else tradingsymbol
        if key not in self._instrument_cache:
            raise KeyError(f"Symbol {tradingsymbol} not found in Angel One instrument master")
        return self._instrument_cache[key]

    # ------------------------------------------------------------------ pacing / bookkeeping
    @property
    def _current_delay(self) -> float:                   # read-only view kept for older callers and logs
        return self.pace.delay

    def _note_error(self, msg) -> None:
        key = _normalise(msg)
        self.error_counts[key] += 1
        self.error_examples.setdefault(key, clean_reply(msg))

    def pace_report(self) -> dict:
        p = self.pace
        return {"calls": p.calls, "refused": p.refusals, "rate_limited": p.rate_limited,
                "slowed_calls": p.at_level[1] + p.at_level[2], "slowest_calls": p.at_level[2], "wait_now_s": p.delay}

    # ------------------------------------------------------------------ candles
    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                            interval: str = "ONE_DAY", exchange: str = "NSE") -> pd.DataFrame:
        token = self._symbol_token(tradingsymbol, exchange)
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days * 2)

        params = {
            "exchange": exchange,
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M"),
        }

        last_reply = ""
        last_was_rate = False
        for attempt in range(self.MAX_ATTEMPTS):
            level = self.pace.level
            wait = self.pace.delay
            if attempt:                                   # a retry waits at least a short fixed pause, longer if the broker said "rate"
                pause = self.RETRY_BACKOFF_S[min(attempt - 1, len(self.RETRY_BACKOFF_S) - 1)]
                wait = max(wait, pause * (self.RATE_BACKOFF_MULT if last_was_rate else 1.0))
            time.sleep(wait)
            try:
                response = self.client.getCandleData(params)
            except Exception as e:  # noqa: BLE001
                if not is_transient_error(e):
                    raise                                 # a permanent error: retrying cannot help, and it is no pacing signal
                last_reply = clean_reply(e)
                last_was_rate = is_rate_limit_error(e)
                self._note_error(e)
                self.pace.record(True, last_was_rate, level)
                continue

            ok, candles, why = read_candle_reply(response)
            if ok:
                self.pace.record(False, False, level)
                if not candles:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)
                df = pd.DataFrame(candles, columns=["timestamp", *CANDLE_COLUMNS])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df.set_index("timestamp").tail(days)

            last_reply = clean_reply(why)                  # a refused answer is a failure, never "no candles"
            self._note_error(why)
            if is_permanent_reply(why):                    # e.g. "not found": an answer about THIS stock, not throttling
                self.pace.record(False, False, level)
                raise BrokerRefused(tradingsymbol, attempt + 1, last_reply)
            last_was_rate = is_rate_limit_error(why)
            self.pace.record(True, last_was_rate, level)

        raise BrokerRefused(tradingsymbol, self.MAX_ATTEMPTS, last_reply)

    # ------------------------------------------------------------------ live quotes
    def get_quotes(self, symbols: list[str], exchange: str = "NSE", mode: str = "FULL") -> dict[str, dict]:
        """
        {symbol: {"ltp", "open", "high", "low", "prev_close", "volume"}} for as many of `symbols` as the broker
        answers (50 per call, about 1 call a second). Symbols missing from the answer are simply absent.
        Raises RuntimeError only when nothing at all could be read, so the caller can fall back to candles.
        """
        by_token: dict[str, str] = {}
        for s in symbols:
            try:
                by_token[str(self._symbol_token(s, exchange))] = s
            except KeyError:
                continue
        tokens = list(by_token)
        out: dict[str, dict] = {}
        problems: list[str] = []

        for i in range(0, len(tokens), self.QUOTE_CHUNK):
            part = tokens[i:i + self.QUOTE_CHUNK]
            for attempt in range(self.MAX_ATTEMPTS):
                time.sleep(self.QUOTE_PAUSE_S * (attempt + 1))
                try:
                    reply = self.client.getMarketData(mode, {exchange: part})
                except Exception as e:  # noqa: BLE001
                    problems.append(clean_reply(e))
                    self._note_error(e)
                    if not is_transient_error(e):
                        break
                    continue
                data = reply.get("data") if isinstance(reply, dict) else None
                fetched = data.get("fetched") if isinstance(data, dict) else None
                if isinstance(reply, dict) and (fetched or _truthy(reply.get("status"))):
                    for q in fetched or []:
                        sym = by_token.get(str((q or {}).get("symbolToken")))
                        rec = read_quote_record(q) if sym else None
                        if rec:
                            out[sym] = rec
                    break
                why = (reply or {}).get("message") if isinstance(reply, dict) else f"unexpected reply {type(reply).__name__}"
                problems.append(clean_reply(why))
                self._note_error(why)

        if not out:
            raise RuntimeError("no usable quote from the broker" + (f" (last problem: {problems[-1]})" if problems else ""))
        return out

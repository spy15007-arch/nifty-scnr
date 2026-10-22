"""
Angel One SmartAPI wrapper. Requires: pip install smartapi-python pyotp

WHAT WENT WRONG ON 2026-10-21, AND THE FIX
------------------------------------------
The SmartAPI SDK does NOT raise when the broker answers {"status": false, "message": "...", "data": null}:
it only logs the error and hands the dictionary back. The old code then read `response.get("data", [])`, saw
"no candles" and reported SUCCESS with an empty table. The stock was silently dropped, the adaptive delay even
EASED DOWN, and no circuit breaker ever tripped. That is how the 2 PM BTST scan of 501 stocks became a scan
of 11 stocks. Now a refused answer is a refusal: the delay rises, the call is retried, and if the broker keeps
refusing, the error carries the broker's own words (they end up in the run note). Only
{"status": true, "data": []} means "this stock really has no candles".

RATE LIMITING: ADAPTIVE throttle shared across every call from this client instance. It starts fast (0.4 s),
slows down whenever the broker refuses or throttles, and RECOVERS speed after a sustained run of successes
(without recovery one bad patch slows the whole rest of a 1500-symbol run even after the throttling clears).

LIVE QUOTES: `get_quotes` returns today's price, open/high/low, previous close and traded volume for up to 50
stocks per call. The afternoon BTST scan uses it instead of 150 slow candle calls (see data/history_cache.py).
"""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# words the broker or its gateway uses when it is throttling or briefly unwell (matched case-insensitively)
_TRANSIENT = re.compile(
    r"\brate\b|too many|ab1021|ab1004|access denied|exceeding|something went wrong|try after|try again|try later|"
    r"timed out|timeout|temporar|unavailable|\bbusy\b|gateway|connection|couldn't parse|server error",
    re.IGNORECASE,
)

CANDLE_COLUMNS = ["open", "high", "low", "close", "volume"]


def is_transient_error(error) -> bool:
    """True for throttling / a briefly unwell broker (worth a pause and another try), False for a permanent error."""
    return bool(_TRANSIENT.search(str(error)))


def _is_rate_limit_error(e: Exception) -> bool:       # kept for older callers
    return is_transient_error(e)


def _normalise(msg) -> str:
    return re.sub(r"\d+", "#", str(msg).strip().lower())[:90]


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


class AngelOneDataClient:
    MAX_ATTEMPTS = 3
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
        self._current_delay = 0.4
        self._min_delay = 0.4
        self._max_delay = 8.0
        self._consecutive_successes = 0
        self._decay_after = 10  # after this many clean successes, ease the delay back down
        self.error_counts: Counter = Counter()

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

    # ------------------------------------------------------------------ throttle
    def _on_success(self):
        self._consecutive_successes += 1
        if self._consecutive_successes >= self._decay_after:
            self._current_delay = max(self._current_delay * 0.85, self._min_delay)
            self._consecutive_successes = 0

    def _on_rate_limit(self):
        self._current_delay = min(self._current_delay * 1.8, self._max_delay)
        self._consecutive_successes = 0

    def _note_error(self, msg) -> None:
        self.error_counts[_normalise(msg)] += 1

    # ------------------------------------------------------------------ candles
    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                            interval: str = "ONE_DAY", exchange: str = "NSE") -> pd.DataFrame:
        print(f"Fetching: {tradingsymbol} | Current Delay: {self._current_delay:.1f}s", flush=True)
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

        last_error: object = None
        for _attempt in range(self.MAX_ATTEMPTS):
            time.sleep(self._current_delay)
            try:
                response = self.client.getCandleData(params)
            except Exception as e:  # noqa: BLE001
                last_error = e
                self._note_error(e)
                if not is_transient_error(e):
                    raise
                self._on_rate_limit()
                continue

            ok, candles, why = read_candle_reply(response)
            if ok:
                self._on_success()
                if not candles:
                    return pd.DataFrame(columns=CANDLE_COLUMNS)
                df = pd.DataFrame(candles, columns=["timestamp", *CANDLE_COLUMNS])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df.set_index("timestamp").tail(days)

            last_error = why                       # a refused answer is a failure, never "no candles"
            self._note_error(why)
            self._on_rate_limit()

        raise RuntimeError(f"{tradingsymbol}: the broker gave no usable answer after {self.MAX_ATTEMPTS} attempts "
                           f"(last reply: {last_error}; delay now {self._current_delay:.1f}s)")

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
                    problems.append(str(e))
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
                problems.append(str(why))
                self._note_error(why)

        if not out:
            raise RuntimeError("no usable quote from the broker" + (f" (last problem: {problems[-1]})" if problems else ""))
        return out

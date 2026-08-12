"""
Angel One SmartAPI wrapper. Requires: pip install smartapi-python pyotp

Rate limiting: ADAPTIVE throttle shared across every call from this
client instance. Starts fast (0.4s), slows down on rate-limit errors,
and - critically - RECOVERS speed after a sustained run of successes.
Without recovery, one early bad patch permanently slows the entire
rest of a 1500-symbol run even after the throttling clears (this was
the actual cause of the ~2 hour EOD runtimes - not detection, but no
way back down once the delay climbed).

Detects two Angel One rate-limit response formats:
  - "Access denied because of exceeding access rate"
  - "Too many requests" / errorcode AB1021
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta


def _is_rate_limit_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "rate" in msg or "too many" in msg or "ab1021" in msg


class AngelOneDataClient:
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
                except Exception as e:
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

    def _on_success(self):
        self._consecutive_successes += 1
        if self._consecutive_successes >= self._decay_after:
            self._current_delay = max(self._current_delay * 0.85, self._min_delay)
            self._consecutive_successes = 0

    def _on_rate_limit(self):
        self._current_delay = min(self._current_delay * 1.8, self._max_delay)
        self._consecutive_successes = 0

    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                             interval: str = "ONE_DAY", exchange: str = "NSE") -> pd.DataFrame:
        import time

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

        max_attempts = 3
        last_error = None
        for attempt in range(max_attempts):
            time.sleep(self._current_delay)
            try:
                response = self.client.getCandleData(params)
                candles = response.get("data", [])
                if not candles:
                    self._on_success()
                    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                self._on_success()
                return df.set_index("timestamp").tail(days)
            except Exception as e:
                last_error = e
                if not _is_rate_limit_error(e):
                    raise
                self._on_rate_limit()
                continue

        raise RuntimeError(f"{tradingsymbol}: rate-limited after {max_attempts} attempts at delay={self._current_delay:.1f}s: {last_error}")

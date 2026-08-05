"""
Angel One SmartAPI wrapper. Requires: pip install smartapi-python pyotp

Auth note: Angel One login also expires (session-based), but supports
TOTP-based login, which CAN be fully automated - no manual step needed
each day.

Rate limiting: uses an ADAPTIVE throttle shared across every call from
this client instance, not a fixed per-symbol retry schedule. Starts
fast (0.4s between calls) and only slows down when it actually hits a
rate-limit error - and once it slows down, EVERY subsequent symbol
uses that new, safer pace too (not just the one that failed). This is
much faster in the common case (no fixed floor per symbol) and more
effective under real throttling (learns the safe pace once instead of
paying a slow retry penalty on every single symbol).
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta


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
        self._max_delay = 4.0

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

        key = f"{tradingsymbol}-EQ" if not tradingsymbol.endswith("-EQ") else tradingsymbol
        if key not in self._instrument_cache:
            raise KeyError(f"Symbol {tradingsymbol} not found in Angel One instrument master")
        return self._instrument_cache[key]

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
                    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                return df.set_index("timestamp").tail(days)
            except Exception as e:
                last_error = e
                if "rate" not in str(e).lower():
                    raise
                self._current_delay = min(self._current_delay * 1.8, self._max_delay)
                continue

        raise RuntimeError(f"{tradingsymbol}: rate-limited after {max_attempts} attempts at delay={self._current_delay:.1f}s: {last_error}")

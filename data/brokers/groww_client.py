"""
Groww API wrapper. Requires: pip install growwapi

Auth note: Groww's official API uses an API key + TOTP-based access
token, similar to Angel One - generate a fresh access token each run
using pyotp against your Groww API secret. This is automatable, same
as Angel One, unlike Kite's manual daily flow.

Groww identifies instruments by trading symbol directly on NSE/BSE for
equities, so no separate instrument-token lookup is needed for most
equity scans (simpler than Kite/Angel One/Dhan in that respect).
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta


class GrowwDataClient:
    def __init__(self, api_key: str, api_secret: str):
        from growwapi import GrowwAPI
        import pyotp

        totp = pyotp.TOTP(api_secret).now()
        access_token = GrowwAPI.get_access_token(api_key, totp)
        self.groww = GrowwAPI(access_token)

    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                             exchange: str = "NSE", segment: str = "CASH") -> pd.DataFrame:
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days * 2)

        response = self.groww.get_historical_candle_data(
            trading_symbol=tradingsymbol,
            exchange=exchange,
            segment=segment,
            start_time=from_date.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=to_date.strftime("%Y-%m-%d %H:%M:%S"),
            interval_in_minutes=1440,  # daily bars
        )
        candles = response.get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        # Groww returns [timestamp, open, high, low, close, volume] per candle
        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        return df.set_index("timestamp").tail(days)

    def get_quote(self, tradingsymbol: str, exchange: str = "NSE", segment: str = "CASH") -> dict:
        return self.groww.get_quote(trading_symbol=tradingsymbol, exchange=exchange, segment=segment)

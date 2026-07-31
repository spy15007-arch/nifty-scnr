"""
Dhan (DhanHQ) wrapper. Requires: pip install dhanhq

Auth note: Dhan access tokens are generated from the Dhan web
dashboard (Profile > DhanHQ Trading APIs) and are long-lived (typically
~24hr-30 days depending on plan) but NOT auto-refreshable via TOTP like
Angel One - there's no programmatic login flow. Treat it like Kite's
token: generate it, store as a GitHub secret, and refresh manually or
via a small local script when it expires.

Dhan identifies instruments by `security_id`, not tradingsymbol - you
need their instrument master CSV to map one to the other (mirrors the
Angel One pattern below).
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta


class DhanDataClient:
    def __init__(self, client_id: str, access_token: str):
        from dhanhq import dhanhq
        self.dhan = dhanhq(client_id, access_token)
        self._instrument_cache: dict[str, str] = {}

    def _security_id(self, tradingsymbol: str, exchange_segment: str = "NSE_EQ") -> str:
        """
        Dhan publishes a CSV instrument master - download and cache the
        symbol -> security_id lookup rather than hitting it per call.
        """
        import requests
        import io

        if not self._instrument_cache:
            url = "https://images.dhan.co/api-data/api-scrip-master.csv"
            resp = requests.get(url, timeout=30)
            df = pd.read_csv(io.StringIO(resp.text))
            nse_eq = df[df["SEM_EXM_EXCH_ID"] == "NSE"]
            self._instrument_cache = dict(zip(nse_eq["SEM_TRADING_SYMBOL"], nse_eq["SEM_SMST_SECURITY_ID"].astype(str)))

        if tradingsymbol not in self._instrument_cache:
            raise KeyError(f"Symbol {tradingsymbol} not found in Dhan instrument master")
        return self._instrument_cache[tradingsymbol]

    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                             exchange_segment: str = "NSE_EQ", instrument_type: str = "EQUITY") -> pd.DataFrame:
        security_id = self._security_id(tradingsymbol, exchange_segment)
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days * 2)

        response = self.dhan.historical_daily_data(
            security_id=security_id,
            exchange_segment=exchange_segment,
            instrument_type=instrument_type,
            from_date=from_date.strftime("%Y-%m-%d"),
            to_date=to_date.strftime("%Y-%m-%d"),
        )
        data = response.get("data", response) if isinstance(response, dict) else response
        if not data or "close" not in data:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame({
            "timestamp": pd.to_datetime(data["timestamp"], unit="s"),
            "open": data["open"], "high": data["high"], "low": data["low"],
            "close": data["close"], "volume": data["volume"],
        }).set_index("timestamp")
        return df.tail(days)

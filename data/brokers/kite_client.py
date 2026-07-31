"""
Zerodha Kite Connect wrapper. Requires: pip install kiteconnect

Auth note: Kite access tokens expire DAILY (this is Zerodha's design,
not a bug). For a scheduled GitHub Action that runs unattended, you
cannot do the interactive login flow each run. The standard pattern:

  1. Run the interactive login flow once yourself each morning (or via
     a small local script) to get a fresh `access_token`.
  2. Store it as a GitHub Actions secret (KITE_ACCESS_TOKEN) and update
     it daily - either manually, or with a scheduled local cron job
     that refreshes the secret via the GitHub CLI (`gh secret set`).

There is no way around the daily token refresh with Kite - budget for
it rather than fighting it.
"""
from __future__ import annotations
import pandas as pd
from datetime import datetime, timedelta


class KiteDataClient:
    def __init__(self, api_key: str, access_token: str):
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._instrument_cache: dict[str, int] = {}

    def _instrument_token(self, tradingsymbol: str, exchange: str = "NSE") -> int:
        """Kite needs numeric instrument tokens, not symbols - cache the lookup."""
        key = f"{exchange}:{tradingsymbol}"
        if key not in self._instrument_cache:
            instruments = self.kite.instruments(exchange)
            lookup = {i["tradingsymbol"]: i["instrument_token"] for i in instruments}
            self._instrument_cache.update({f"{exchange}:{k}": v for k, v in lookup.items()})
        return self._instrument_cache[key]

    def get_historical_bars(self, tradingsymbol: str, days: int = 250,
                             interval: str = "day", exchange: str = "NSE") -> pd.DataFrame:
        token = self._instrument_token(tradingsymbol, exchange)
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days * 2)  # buffer for weekends/holidays

        candles = self.kite.historical_data(token, from_date, to_date, interval)
        df = pd.DataFrame(candles)
        if df.empty:
            return df
        df = df.rename(columns={"date": "timestamp"}).set_index("timestamp")
        return df[["open", "high", "low", "close", "volume"]].tail(days)

    def get_quote(self, tradingsymbol: str, exchange: str = "NSE") -> dict:
        key = f"{exchange}:{tradingsymbol}"
        return self.kite.quote([key])[key]


def get_login_url(api_key: str) -> str:
    """Step 1 of daily auth: visit this URL, log in, get redirected with a request_token."""
    from kiteconnect import KiteConnect
    return KiteConnect(api_key=api_key).login_url()


def generate_access_token(api_key: str, api_secret: str, request_token: str) -> str:
    """Step 2 of daily auth: exchange the request_token (from the redirect) for an access_token."""
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    return session["access_token"]

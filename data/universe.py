"""
Loads the NIFTY 500 constituent list from NSE's published archive.
Falls back to a hardcoded liquid NIFTY 50 list if the fetch fails
(NSE occasionally blocks non-browser user agents or the URL moves) -
a scheduled scan should never hard-fail just because a CSV download
broke, it should degrade gracefully to a smaller, still-useful universe.
"""
from __future__ import annotations
import logging
import pandas as pd

logger = logging.getLogger(__name__)

NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

# Hardcoded fallback: liquid NIFTY 50 names. Used only if the live
# fetch fails - update periodically since index constituents change.
FALLBACK_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "SBIN",
    "BHARTIARTL", "LT", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "HINDUNILVR",
    "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "NESTLEIND",
    "WIPRO", "ADANIENT", "TATAMOTORS", "TATASTEEL", "POWERGRID", "NTPC",
    "HCLTECH", "M&M", "BAJAJFINSV", "TECHM", "INDUSINDBK", "GRASIM",
    "JSWSTEEL", "DRREDDY", "CIPLA", "COALINDIA", "DIVISLAB", "EICHERMOT",
    "BPCL", "HEROMOTOCO", "BRITANNIA", "APOLLOHOSP", "ADANIPORTS",
    "TATACONSUM", "HINDALCO", "SBILIFE", "HDFCLIFE", "ONGC", "UPL",
    "BAJAJ-AUTO", "SHREECEM", "SBICARD",
]


def load_universe(headers: dict | None = None, timeout: int = 15) -> list[str]:
    import requests
    import io

    headers = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = requests.get(NSE_500_URL, headers=headers, timeout=timeout)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].dropna().unique().tolist()
        if len(symbols) < 100:
            raise ValueError(f"Suspiciously short list ({len(symbols)}) - treating as failed fetch")
        logger.info(f"Loaded {len(symbols)} symbols from NSE NIFTY 500 list")
        return symbols
    except Exception as e:
        logger.warning(f"NIFTY 500 fetch failed ({e}) - falling back to {len(FALLBACK_UNIVERSE)} liquid names")
        return FALLBACK_UNIVERSE

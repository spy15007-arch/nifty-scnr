"""
Loads the tradable stock universe: NIFTY 500 (guaranteed liquid core)
combined with additional NSE-listed names, capped at MAX_UNIVERSE_SIZE
(default 1500) for a predictable runtime.

Why 1500, not the full ~2000 NSE list: NIFTY 500 is NSE's own
definition of "the market that matters" (union of Nifty 100 + Midcap
150 + Smallcap 250). The ~1500 names beyond that are overwhelmingly
micro-caps/thin traders that mostly get discarded by the liquidity
filter anyway - fetching all ~2000 adds more runtime for very little
extra signal. 1500 keeps the guaranteed-liquid core AND adds real
small-cap breadth without the long illiquid tail.

Time-critical scans (morning/afternoon) override this down to 500 via
the TRADING_MAX_UNIVERSE env var set in their workflow files - only
the non-time-critical EOD/run_all scans use the full 1500 by default.

Fallback chain, each degrading gracefully rather than hard-failing a
scheduled scan:
  1. NIFTY 500 + additional NSE names, capped at MAX_UNIVERSE_SIZE
  2. NIFTY 500 only, if the full NSE list fetch fails
  3. Hardcoded liquid NIFTY 50, if both live fetches fail
"""
from __future__ import annotations
import logging
import os
import random
import pandas as pd

logger = logging.getLogger(__name__)

NSE_FULL_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

MAX_UNIVERSE_SIZE = int(os.getenv("TRADING_MAX_UNIVERSE", "1500"))

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

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _fetch_csv(url: str, headers: dict, timeout: int) -> pd.DataFrame:
    import requests
    import io

    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return pd.read_csv(io.StringIO(resp.text))


def _load_nifty500(headers: dict, timeout: int) -> list[str]:
    df = _fetch_csv(NSE_500_URL, headers, timeout)
    symbols = df["Symbol"].dropna().unique().tolist()
    if len(symbols) < 100:
        raise ValueError(f"Suspiciously short NIFTY 500 list ({len(symbols)})")
    return symbols


def _load_full_nse(headers: dict, timeout: int) -> list[str]:
    df = _fetch_csv(NSE_FULL_URL, headers, timeout)
    df.columns = [c.strip() for c in df.columns]
    if "SERIES" in df.columns:
        df = df[df["SERIES"].str.strip() == "EQ"]
    symbols = df["SYMBOL"].dropna().str.strip().unique().tolist()
    if len(symbols) < 500:
        raise ValueError(f"Suspiciously short full-NSE list ({len(symbols)})")
    return symbols


def load_universe(headers: dict | None = None, timeout: int = 20) -> list[str]:
    headers = headers or _DEFAULT_HEADERS

    try:
        nifty500 = _load_nifty500(headers, timeout)
    except Exception as e:
        logger.warning(f"NIFTY 500 fetch failed ({e}) - falling back to {len(FALLBACK_UNIVERSE)} liquid names")
        result = list(FALLBACK_UNIVERSE)
        random.shuffle(result)
        return result

    try:
        full_nse = _load_full_nse(headers, timeout)
        extra = sorted(set(full_nse) - set(nifty500))
        remaining_slots = max(0, MAX_UNIVERSE_SIZE - len(nifty500))
        universe = nifty500 + extra[:remaining_slots]
        logger.info(
            f"Loaded {len(nifty500)} NIFTY 500 + {min(len(extra), remaining_slots)} additional NSE names "
            f"= {len(universe)} total (capped at {MAX_UNIVERSE_SIZE})"
        )
    except Exception as e:
        logger.warning(f"Full NSE list fetch failed ({e}) - using NIFTY 500 only ({len(nifty500)} symbols)")
        universe = nifty500

    random.shuffle(universe)
    return universe

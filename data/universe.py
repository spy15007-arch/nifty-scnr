"""
Loads the tradable stock universe: the NIFTY 500 - NSE's own definition of
"the market that matters" (Nifty 100 + Midcap 150 + Smallcap 250), i.e. the
500 largest listed companies. These are the stocks big funds, FIIs and DIIs
actually trade, which is where sustained breakouts come from.

CHANGED 2026-10-18: the old loader padded the NIFTY 500 with the first N
extra NSE names IN ALPHABETICAL ORDER (so a Swing scan "enlarged" to 750 got
250 arbitrary micro-caps starting with "20MICRONS", "3IINFOTECH"...). That
added runtime and junk, not signal, so it is gone. Extra names beyond the
NIFTY 500 are now only ever added when they are chosen on LIQUIDITY (see
`extra_symbols` below) - never alphabetically.

Price is NOT a universe criterion (no price cap - see config.py); the only
filters are a penny-stock floor and a 20-day turnover floor, applied later by
the scanner engine on the bars it actually fetched.

Fallback chain, each degrading gracefully rather than hard-failing a
scheduled scan:
  1. NIFTY 500 (+ liquidity-ranked extras if any were supplied)
  2. Hardcoded liquid NIFTY 50, if the live NIFTY 500 fetch fails
"""
from __future__ import annotations
import logging
import os
import random
import pandas as pd

logger = logging.getLogger(__name__)

NSE_500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

# Upper bound on universe size (NIFTY 500 is always kept whole; this only
# limits how many liquidity-ranked extras may be appended beyond it).
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


def load_universe(headers: dict | None = None, timeout: int = 20,
                  extra_symbols: list[str] | None = None) -> list[str]:
    """
    The NIFTY 500, shuffled (so a run cut short by a time budget still sees an
    unbiased sample), plus - only if the caller passes them - `extra_symbols`
    that were chosen on liquidity, up to MAX_UNIVERSE_SIZE in total.
    """
    headers = headers or _DEFAULT_HEADERS

    try:
        nifty500 = _load_nifty500(headers, timeout)
    except Exception as e:
        logger.warning(f"NIFTY 500 fetch failed ({e}) - falling back to {len(FALLBACK_UNIVERSE)} liquid names")
        result = list(FALLBACK_UNIVERSE)
        random.shuffle(result)
        return result

    universe = list(nifty500)
    if extra_symbols:
        have = set(universe)
        room = max(0, MAX_UNIVERSE_SIZE - len(universe))
        extras = [s for s in extra_symbols if s not in have][:room]
        universe += extras
        logger.info(f"Universe: {len(nifty500)} NIFTY 500 + {len(extras)} liquidity-ranked extras = {len(universe)}")
    else:
        logger.info(f"Universe: {len(universe)} NIFTY 500 stocks")

    random.shuffle(universe)
    return universe

"""
Global market cues: overnight US markets, crude oil, dollar index, and
Asian markets as a macro backdrop for today's setups. Angel One only
covers NSE/BSE, so this uses Yahoo Finance (yfinance) for everything
outside India.

This produces a MULTIPLIER, not a hard gate - a hostile global backdrop
makes even good technical setups more likely to fail (broad risk-off
drags most stocks down together), so it shifts conviction, it doesn't
override a genuine setup entirely.

CAVEAT: needs network access to Yahoo Finance and the yfinance package.
GitHub Actions runners have unrestricted outbound internet by default,
so this should work there. If it ever fails it degrades to "neutral"
(1.0x, no effect on scoring) rather than breaking the scan.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

GLOBAL_TICKERS = {
    "us_dow": "^DJI",
    "us_nasdaq": "^IXIC",
    "crude_oil": "CL=F",
    "dollar_index": "DX-Y.NYB",
    "nikkei": "^N225",
    "hang_seng": "^HSI",
}


def fetch_global_snapshot() -> dict:
    """Returns % change for each global proxy over its most recent session. Empty dict on any failure."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed - skipping global cues, treating as neutral")
        return {}

    snapshot = {}
    for name, ticker in GLOBAL_TICKERS.items():
        try:
            data = yf.Ticker(ticker).history(period="5d")
            if len(data) >= 2:
                pct_change = float(data["Close"].iloc[-1] / data["Close"].iloc[-2] - 1)
                snapshot[name] = round(pct_change, 4)
        except Exception as e:
            logger.warning(f"Could not fetch {name} ({ticker}): {e}")
            continue
    return snapshot


def compute_market_regime_bias(snapshot: dict) -> tuple[str, float]:
    """
    Combines global cues into a conviction multiplier. NOT a hard gate -
    shifts probability by a modest amount, never overrides a genuine
    technical setup entirely.
    """
    if not snapshot:
        return "unknown (no data)", 1.0

    us_bias = (snapshot.get("us_dow", 0) + snapshot.get("us_nasdaq", 0)) / 2
    asia_bias = (snapshot.get("nikkei", 0) + snapshot.get("hang_seng", 0)) / 2
    crude_headwind = -snapshot.get("crude_oil", 0) * 0.5
    dollar_headwind = -snapshot.get("dollar_index", 0) * 0.3

    composite = us_bias * 0.4 + asia_bias * 0.2 + crude_headwind + dollar_headwind

    if composite > 0.005:
        return f"supportive (composite {composite:+.2%})", 1.08
    elif composite < -0.005:
        return f"hostile (composite {composite:+.2%})", 0.90
    else:
        return f"neutral (composite {composite:+.2%})", 1.0

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

See compute_index_trend_bias() below for the 2026-10-13 addition
reading the NSE market's own trend, as a separate signal from this
module's overnight international proxies.
"""
from __future__ import annotations
import logging
import pandas as pd

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


def compute_index_trend_bias(benchmark_df, ema_period: int = 50) -> tuple[str, float]:
    """
    ADDED 2026-10-13. Separate signal from compute_market_regime_bias
    above: that one reads overnight INTERNATIONAL proxies (US/Asia,
    crude, dollar) as a backdrop. This reads the NSE market's OWN
    current trend, via the NIFTYBEES benchmark that's already fetched
    every single scan for relative_strength - no extra API call, no
    extra network dependency, nothing that can newly fail.

    Breakout/momentum systems are well documented to have materially
    different real-world hit rates depending on whether the broader
    market is itself trending up or chopping/declining - a textbook-
    perfect single-stock setup still fights a stronger headwind when
    the index itself is below its own trend. This says nothing about
    today's candidate specifically, only about the backdrop it's
    breaking out into - conceptually the same "shift conviction, don't
    override a genuine setup" role as the global-cues multiplier.

    NON-GATING by design, same as compute_market_regime_bias: never
    rejects a candidate, only nudges its rank within the candidates
    that already passed every real gate. Mirrors that function's
    magnitude (0.90-1.08x) on purpose, for the same reason - until
    there's real evidence (the backtest's by_market_trend breakdown)
    that this deserves to be a harder filter, it only nudges ranking.
    """
    if benchmark_df is None or len(benchmark_df) < ema_period + 5:
        return "unknown (insufficient benchmark history)", 1.0

    close = benchmark_df["close"]
    trend_ema = close.ewm(span=ema_period, adjust=False).mean()
    current = close.iloc[-1]
    ema_now = trend_ema.iloc[-1]
    if ema_now in (0, None) or pd.isna(ema_now):
        return "unknown (bad benchmark data)", 1.0
    distance_pct = (current - ema_now) / ema_now

    if distance_pct > 0.005:
        return f"index above its {ema_period}d trend ({distance_pct:+.2%})", 1.08
    elif distance_pct < -0.005:
        return f"index below its {ema_period}d trend ({distance_pct:+.2%})", 0.90
    else:
        return f"index flat on its {ema_period}d trend ({distance_pct:+.2%})", 1.0

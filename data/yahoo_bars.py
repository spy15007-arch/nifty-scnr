"""
Daily bars from Yahoo Finance for the RESEARCH back-test only. The live scans never use this: they read the broker.

Why it exists: the broker returns about 3 years of daily candles that are NOT adjusted for splits and bonuses. Yahoo's history is
split-adjusted and goes back 10+ years, which gives the back-test several times as many trades - enough to tell whether a small
effect (for example "had the stock already spiked before the signal?") is real or just noise. No credentials are needed.

Prices are split-adjusted but NOT dividend-adjusted (auto_adjust=False), so they stay comparable with what a chart shows.

Limits (the back-test report repeats them):
  * the universe is TODAY's NIFTY 500, so companies that left the index or were delisted are missing - past results look somewhat
    better than they would have at the time (survivorship);
  * Yahoo's volume may not be split-adjusted, so turnover and volume ratios are slightly off in the weeks around a split;
  * Yahoo now and then returns a bad bar; rows whose high/low/close contradict each other are dropped.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)

MIN_ROWS = 300                    # a stock with less history than this cannot be scored (12-month return needs 253 bars)
_IST = timezone(timedelta(hours=5, minutes=30))


def yahoo_ticker(symbol: str) -> str:
    """NSE symbol -> Yahoo ticker (RELIANCE -> RELIANCE.NS; M&M and BAJAJ-AUTO keep their punctuation)."""
    return f"{symbol.strip().upper()}.NS"


def tidy(raw: pd.DataFrame | None, now_ist: datetime | None = None) -> pd.DataFrame | None:
    """
    Yahoo's table -> open/high/low/close/volume floats, oldest first, one row per completed session.
    Today's row is dropped while the market could still be trading (a forming bar must never be replayed).
    Returns None when nothing usable is left.
    """
    if raw is None or len(raw) == 0:
        return None
    cols = {c.lower().replace(" ", "_"): c for c in raw.columns}
    need = ["open", "high", "low", "close", "volume"]
    if any(c not in cols for c in need):
        return None
    d = pd.DataFrame({c: pd.to_numeric(raw[cols[c]], errors="coerce") for c in need}, index=raw.index).astype("float64")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d.dropna(subset=["open", "high", "low", "close"])
    sane = ((d["low"] > 0) & (d["high"] >= d["low"]) & (d["close"] <= d["high"] * 1.001) & (d["close"] >= d["low"] * 0.999)
            & (d["open"] <= d["high"] * 1.001) & (d["open"] >= d["low"] * 0.999))
    d = d[sane]
    d["volume"] = d["volume"].fillna(0.0)
    now_ist = (now_ist or datetime.now(_IST)).astimezone(_IST)
    if now_ist.hour < 17 and len(d) and pd.Timestamp(d.index[-1]).date() >= now_ist.date():
        d = d.iloc[:-1]
    return d if len(d) else None


def fetch_yahoo_bars(symbols: list[str], start: str = "2015-06-01", pause: float = 0.25, max_seconds: int = 2700,
                     attempts: int = 3) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    One history request per stock, with short pauses and a few retries. Stocks Yahoo does not know (renamed, delisted) are skipped.
    Returns ({symbol: bars}, report); the report is what the run saves so a thin result can be explained without the run log.
    """
    import yfinance as yf

    bars: dict[str, pd.DataFrame] = {}
    failed: list[str] = []
    too_short: list[str] = []
    errors: Counter = Counter()
    stopped = ""
    t0 = time.time()
    for i, sym in enumerate(symbols, 1):
        if time.time() - t0 > max_seconds:
            stopped = "time budget"
            break
        d = None
        for attempt in range(attempts):
            try:
                raw = yf.Ticker(yahoo_ticker(sym)).history(start=start, interval="1d", auto_adjust=False, actions=False)
                d = tidy(raw)
                break
            except Exception as e:  # noqa: BLE001 - Yahoo failures are many and varied; the report counts them
                errors[f"{type(e).__name__}: {str(e)[:90]}"] += 1
                time.sleep(2.0 * (attempt + 1))
        if d is None:
            failed.append(sym)
        elif len(d) < MIN_ROWS:
            too_short.append(sym)
        else:
            bars[sym] = d
        if i % 50 == 0:
            logger.info(f"Yahoo: {i}/{len(symbols)} asked, {len(bars)} usable, {len(failed)} without data, {len(too_short)} too short")
        time.sleep(pause)
    report = {
        "source": "yahoo", "asked": len(symbols), "usable": len(bars), "no_data_n": len(failed), "no_data": failed[:60],
        "too_short_n": len(too_short), "seconds": round(time.time() - t0), "stopped": stopped,
        "errors": errors.most_common(5), "start": start,
        "first_bar": min((str(d.index[0].date()) for d in bars.values()), default=""),
        "last_bar": max((str(d.index[-1].date()) for d in bars.values()), default=""),
    }
    logger.info(f"Yahoo bars: {report['usable']} of {report['asked']} stocks usable ({report['first_bar']} to {report['last_bar']}), "
                f"{report['no_data_n']} without data, {report['too_short_n']} too short, {report['seconds']}s")
    return bars, report

"""
Yesterday's daily candles for the BTST watchlist, plus the logic that turns them into "today so far".

WHY: the afternoon BTST scan has to finish before about 2:30 PM, and the broker's candle endpoint is slow and
throttles hard in market hours (one call per stock). But everything except TODAY'S bar is already known the
evening before. So:

  * the evening full-market run saves the last ~320 completed daily candles of the watchlist stocks here
    (data_cache/btst_history.csv.gz, committed with the rest of data_cache/);
  * the afternoon run asks the broker for ONE live quote per stock - 50 stocks per call, so 150 stocks are 3 calls -
    and appends today's partial bar (open, high, low, last price, volume so far) to the saved candles.

SAFETY CHECK: a stock is merged only if the quote's PREVIOUS CLOSE agrees with the last saved close (within 0.3%).
That proves the saved candles end at the previous session and that no split or bonus changed the price scale since.
Anything that fails the check (or has no quote) is fetched the slow way instead, so wrong data never enters a score.
"""
from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

PATH = "data_cache/btst_history.csv.gz"
TZ = "Asia/Kolkata"
COLS = ["open", "high", "low", "close", "volume"]


def save(bars: dict, symbols: list[str], keep: int = 320, path: str = PATH) -> int:
    """Write the last `keep` completed daily bars of `symbols`. Returns how many stocks were saved (0 = nothing written)."""
    try:
        frames = []
        for s in symbols:
            df = bars.get(s)
            if df is None or len(df) == 0:
                continue
            d = df[COLS].dropna().tail(keep)
            if d.empty:
                continue
            idx = pd.DatetimeIndex(d.index)
            idx = idx.tz_localize(None) if idx.tz is not None else idx
            frames.append(pd.DataFrame({
                "symbol": s, "date": idx.strftime("%Y-%m-%d"),
                "open": d["open"].round(2).to_numpy(), "high": d["high"].round(2).to_numpy(),
                "low": d["low"].round(2).to_numpy(), "close": d["close"].round(2).to_numpy(),
                "volume": d["volume"].round(0).astype("int64").to_numpy(),
            }))
        if not frames:
            return 0
        rows = pd.concat(frames, ignore_index=True)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp.gz"
        rows.to_csv(tmp, index=False, compression="gzip")
        os.replace(tmp, path)
        return int(rows["symbol"].nunique())
    except Exception:  # noqa: BLE001 - the cache is an optimisation; never break the scan for it
        logger.warning("Could not save the BTST history cache", exc_info=True)
        return 0


def load(path: str = PATH) -> tuple[dict[str, pd.DataFrame], str | None]:
    """({symbol: daily bars with a tz-aware IST date index}, newest date as 'YYYY-MM-DD'), or ({}, None) when unusable."""
    try:
        df = pd.read_csv(path, dtype={"symbol": str, "date": str})
        if df.empty or not {"symbol", "date", *COLS}.issubset(df.columns):
            return {}, None
        bars: dict[str, pd.DataFrame] = {}
        for s, g in df.groupby("symbol", sort=False):
            idx = pd.DatetimeIndex(pd.to_datetime(g["date"]).to_numpy()).tz_localize(TZ)
            bars[str(s)] = pd.DataFrame({c: g[c].to_numpy(dtype=float) for c in COLS}, index=idx).sort_index()
        return bars, str(df["date"].max())
    except Exception:  # noqa: BLE001
        return {}, None


def merge_live(hist: dict[str, pd.DataFrame], quotes: dict[str, dict], today: date,
               tol: float = 0.003) -> tuple[dict[str, pd.DataFrame], dict]:
    """
    Saved candles + today's partial bar from live quotes -> {symbol: bars}, and a report
    {"merged": n, "no_quote": [...], "mismatch": [...]}. See the module docstring for the safety check.
    """
    out: dict[str, pd.DataFrame] = {}
    no_quote: list[str] = []
    mismatch: list[str] = []
    day = pd.Timestamp(today).tz_localize(TZ)
    for s, h in hist.items():
        q = quotes.get(s)
        if q is None:
            no_quote.append(s)
            continue
        base = h[h.index.date < today]
        if base.empty:
            mismatch.append(s)
            continue
        last = float(base["close"].iloc[-1])
        if last <= 0 or abs(float(q["prev_close"]) / last - 1.0) > tol:
            mismatch.append(s)
            continue
        row = pd.DataFrame({"open": [q["open"]], "high": [q["high"]], "low": [q["low"]],
                            "close": [q["ltp"]], "volume": [q["volume"]]}, index=pd.DatetimeIndex([day]))
        out[s] = pd.concat([base, row])
    return out, {"merged": len(out), "no_quote": no_quote, "mismatch": mismatch}

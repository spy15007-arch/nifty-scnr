"""
End-of-day DELIVERY % from NSE's public daily file -> a lagged 20-day average per stock.

WHY: delivery % is the share of a day's traded shares that were actually taken
home (delivered) rather than squared off the same day. High and persistent
delivery means the volume comes from investors who hold, not day-trading
churn - the closest thing to an institutional footprint that is available for
free. In 7 years of real NSE data it was the single strongest predictor of
which breakouts followed through (see reports/evidence/).

SOURCE: NSE's "security-wise delivery position" file, published every evening
(about 6-7 PM IST):
    https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv
So the afternoon and 4 PM scans can only use YESTERDAY'S and older days. That
is exactly how the research used it: the 20-day average EXCLUDES the day being
scanned (so there is no look-ahead).

FAULT TOLERANCE (this can never be allowed to hurt a scan):
  * Every network call has a short timeout and the whole refresh has a time
    budget; two failures in a row stop the refresh for this run.
  * A day that NSE does not have (holiday) is remembered so it is not asked
    for again; "today's file isn't out yet" is NOT remembered as a holiday.
  * The cache (data_cache/delivery.csv) is committed to the repo by the scan
    workflows, so it survives between runs. It was seeded with 30 real
    sessions so the very first run already has a full 20-day average.
  * If the data is missing or too old (> DELIVERY_MAX_STALE_DAYS), the scan
    simply scores on the 7 price/volume features instead of 8 and says so.
"""
from __future__ import annotations

import io
import logging
import os
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)

BHAV_URL = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
CACHE_PATH = "data_cache/delivery.csv"
MISSING_PATH = "data_cache/delivery_missing.txt"
KEEP_CALENDAR_DAYS = 70          # the cache keeps about 45 sessions
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


# --------------------------------------------------------------------------- #
def parse_bhavcopy(text: str, expect_day: date | None = None) -> pd.DataFrame | None:
    """EQ-series rows of NSE's delivery file as (symbol, dlv_pct). None if it is not the file we expect."""
    try:
        df = pd.read_csv(io.StringIO(text), skipinitialspace=True)
    except Exception:
        return None
    df.columns = [str(c).strip().upper() for c in df.columns]
    if not {"SYMBOL", "SERIES", "DELIV_PER"}.issubset(df.columns):
        return None
    if expect_day is not None and "DATE1" in df.columns and len(df):
        try:
            file_day = pd.to_datetime(str(df["DATE1"].iloc[0]).strip(), format="%d-%b-%Y").date()
            if file_day != expect_day:
                return None                      # a different day's data under this name: do not trust it
        except Exception:
            pass
    df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
    dp = pd.to_numeric(df["DELIV_PER"].astype(str).str.strip().replace({"-": np.nan, "": np.nan}), errors="coerce")
    out = pd.DataFrame({"symbol": df["SYMBOL"].astype(str).str.strip(), "dlv_pct": dp}).dropna()
    out = out[(out["dlv_pct"] >= 0) & (out["dlv_pct"] <= 100)]
    return out if len(out) >= 100 else None      # a real file has ~1,500+ EQ rows


def fetch_day(day: date, http, timeout: float = 15.0) -> tuple[str, pd.DataFrame | None]:
    """('ok', frame) | ('missing', None) when NSE has no file for that day | ('error', None) on any failure."""
    url = BHAV_URL.format(ddmmyyyy=day.strftime("%d%m%Y"))
    try:
        resp = http.get(url, headers=HEADERS, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - deliberately broad: nothing here may stop a scan
        logger.warning(f"Delivery file {day}: request failed ({type(e).__name__})")
        return "error", None
    if resp.status_code == 404:
        return "missing", None
    if resp.status_code != 200:
        logger.warning(f"Delivery file {day}: HTTP {resp.status_code}")
        return "error", None
    frame = parse_bhavcopy(resp.text, expect_day=day)
    if frame is None:
        logger.warning(f"Delivery file {day}: unexpected content")
        return "error", None
    return "ok", frame


# --------------------------------------------------------------------------- #
def load_cache(path: str = CACHE_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=["date", "symbol", "dlv_pct"])
    try:
        df = pd.read_csv(path, dtype={"date": str, "symbol": str})
    except Exception:
        return pd.DataFrame(columns=["date", "symbol", "dlv_pct"])
    df["dlv_pct"] = pd.to_numeric(df["dlv_pct"], errors="coerce")
    return df.dropna(subset=["dlv_pct"])[["date", "symbol", "dlv_pct"]]


def _load_missing(path: str) -> set[str]:
    try:
        with open(path) as f:
            return {ln.strip() for ln in f if ln.strip()}
    except OSError:
        return set()


def _business_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def update_cache(path: str = CACHE_PATH, today: date | None = None, lookback_days: int = 45,
                 max_fetches: int = 14, time_budget_s: float = 100.0, http=None) -> dict:
    """
    Fill in the delivery days the cache does not have yet (most recent first).
    Never raises. Returns {"fetched", "errors", "missing", "newest", "days"}.
    """
    from scanner import session
    today = today or session.ist_date()
    stats = {"fetched": 0, "errors": 0, "missing": 0, "newest": "", "days": 0}
    try:
        cache = load_cache(path)
        have = set(cache["date"])
        missing_path = os.path.join(os.path.dirname(path) or ".", os.path.basename(MISSING_PATH))
        known_missing = _load_missing(missing_path)
        wanted = [d for d in reversed(_business_days(today - timedelta(days=lookback_days), today))
                  if d.isoformat() not in have and d.isoformat() not in known_missing]
        if wanted:
            if http is None:
                import requests
                http = requests.Session()
            t0 = time.time()
            consecutive_errors = 0
            new_frames, new_missing = [], []
            for d in wanted:
                if stats["fetched"] + stats["missing"] >= max_fetches or time.time() - t0 > time_budget_s:
                    break
                status, frame = fetch_day(d, http)
                if status == "ok":
                    frame = frame.assign(date=d.isoformat())
                    new_frames.append(frame[["date", "symbol", "dlv_pct"]])
                    stats["fetched"] += 1
                    consecutive_errors = 0
                elif status == "missing":
                    stats["missing"] += 1
                    consecutive_errors = 0
                    if d <= today - timedelta(days=3):       # an old date with no file is a holiday; a recent one may just be unpublished
                        new_missing.append(d.isoformat())
                else:
                    stats["errors"] += 1
                    consecutive_errors += 1
                    if consecutive_errors >= 2:
                        logger.warning("Delivery refresh: two failures in a row - giving up for this run")
                        break
            if new_frames:
                cache = pd.concat([cache] + new_frames, ignore_index=True)
            if new_missing:
                os.makedirs(os.path.dirname(missing_path) or ".", exist_ok=True)
                with open(missing_path, "a") as f:
                    f.write("\n".join(new_missing) + "\n")
        if len(cache):
            cutoff = (today - timedelta(days=KEEP_CALENDAR_DAYS)).isoformat()
            cache = cache[cache["date"] >= cutoff].drop_duplicates(["date", "symbol"], keep="last")
            cache = cache.sort_values(["date", "symbol"])
            if stats["fetched"] or not os.path.exists(path):
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                cache.to_csv(path, index=False)
            stats["newest"] = str(cache["date"].max())
            stats["days"] = int(cache["date"].nunique())
    except Exception:  # noqa: BLE001
        logger.exception("Delivery refresh failed - continuing without new delivery data")
    logger.info(f"📦 Delivery data: {stats['fetched']} new day(s) fetched, {stats['errors']} errors, "
                f"newest day {stats['newest'] or 'none'}, {stats['days']} days cached")
    return stats


def get_dlv20(as_of: date, path: str = CACHE_PATH, window: int = 20, min_obs: int = 10) -> tuple[pd.Series | None, dict]:
    """
    Per-stock mean delivery % over the previous `window` sessions BEFORE `as_of` (a stock needs `min_obs`
    of them). Returns (Series indexed by symbol, meta), or (None, meta) when the data is missing or stale.
    """
    meta = {"newest": "", "days": 0, "stale_days": None, "symbols": 0, "reason": ""}
    cache = load_cache(path)
    if cache.empty:
        meta["reason"] = "no delivery data cached"
        return None, meta
    prior = cache[cache["date"] < as_of.isoformat()]
    dates = sorted(prior["date"].unique())[-window:]
    meta["days"] = len(dates)
    if len(dates) < min_obs:
        meta["reason"] = f"only {len(dates)} delivery days available"
        return None, meta
    newest = date.fromisoformat(dates[-1])
    meta["newest"] = dates[-1]
    meta["stale_days"] = (as_of - newest).days
    if meta["stale_days"] > config.DELIVERY_MAX_STALE_DAYS:
        meta["reason"] = f"delivery data is {meta['stale_days']} days old"
        return None, meta
    piv = prior[prior["date"].isin(dates)].pivot_table(index="date", columns="symbol", values="dlv_pct", aggfunc="last")
    cnt = piv.count()
    mean = piv.mean()[cnt >= min_obs]
    meta["symbols"] = int(len(mean))
    return mean.astype(float), meta

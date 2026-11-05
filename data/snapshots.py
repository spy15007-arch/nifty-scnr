"""
The BTST snapshot log: what each watchlist stock looked like when the afternoon scan judged it, and how that day (and the next) really ended.

WHY (5 Nov 2026): the afternoon BTST scan judges a PARTLY FORMED day: the live price, and the volume so far, which it projects to a whole day with an assumed
profile (scanner/session.py: about 64% of a normal day's volume traded by 2:00 PM, 72% by 2:30, 81% by 3:00 - an estimate, never measured on this universe).
The ten-year replay cannot test the afternoon BTST at all, because it works on daily candles. So nothing could say whether the live scan's volume projection is
right, or what a stock bought at the afternoon price then did. This log is the missing record. It starts on the day it is first written and fills in as the
days pass; nothing is ever read back by the scan, so it cannot change a pick.

WHAT: data_cache/btst_snapshots.csv.gz, one row per watchlist stock per live BTST run:
  * written by record() at the scan: date, time_ist, symbol, open, high, low, last, volume (so far), prev_close, assumed_frac (the share of the day's volume the
    scan ASSUMED had traded by then), pick / setup (was it one of that run's picks), via (live quotes or candles);
  * added by complete() in any later run on completed days: final_open / high / low / close / volume of that day, and next_date / next_open / high / low /
    close of the session after it.
The BTST and Swing workflows commit data_cache/ as a whole, so the file is saved with each run's other files. backtest/snapshot_study.py reads it.

HONESTY: a snapshot holds the quote the scan SAW; a buyer's real fill differs. Prices after a split or bonus between the snapshot and the finished day are not
adjusted - the study drops moves too big to be real.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
PATH = "data_cache/btst_snapshots.csv.gz"
KEEP_SESSIONS = 600                       # the newest sessions kept (about two and a half years: a few MB at most)
BAR = ["open", "high", "low", "close", "volume"]
SNAP = ["date", "time_ist", "symbol", "open", "high", "low", "last", "volume", "prev_close", "assumed_frac", "pick", "setup", "via"]
FINAL = ["final_open", "final_high", "final_low", "final_close", "final_volume"]
NEXT = ["next_date", "next_open", "next_high", "next_low", "next_close"]
COLUMNS = SNAP + FINAL + NEXT
TEXT = ["date", "time_ist", "symbol", "setup", "via", "next_date"]


def load(path: str = PATH) -> pd.DataFrame:
    """The log with every column present (blank where a day has not finished yet); an empty frame when there is no usable file."""
    try:
        df = pd.read_csv(path, dtype={c: str for c in TEXT})
    except Exception:  # noqa: BLE001 - no file yet, or one that cannot be read
        return pd.DataFrame(columns=COLUMNS)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = df[c].fillna("") if c in TEXT else pd.to_numeric(df[c], errors="coerce")
    return df[COLUMNS].reset_index(drop=True)


def _save(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp.gz"
    df.to_csv(tmp, index=False, compression="gzip", float_format="%.12g")
    os.replace(tmp, path)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def record(bars: dict, when: datetime, assumed_frac: float, picks=(), via: str = "", path: str = PATH) -> int:
    """
    Append one row per stock whose newest bar is TODAY's still-forming bar (open, high, low, last price, volume so far; the previous close is the bar before it).
    `when` = the clock time of the scan, `assumed_frac` = the share of the day's volume the scan assumed had traded by then, `picks` = this run's picks.
    A second run on the same day at another minute adds its own rows; the same minute replaces them. Returns the number of rows written (0 = nothing).
    """
    t = when.astimezone(IST)
    today = t.date()
    setup = {getattr(p, "symbol", None): str(getattr(p, "setup", "") or "") for p in picks}
    rows = []
    for sym, df in bars.items():
        if df is None or len(df) < 2 or pd.Timestamp(df.index[-1]).date() != today:
            continue
        last, prev = df.iloc[-1], df.iloc[-2]
        vals = [_num(last[c]) for c in BAR] + [_num(prev["close"])]
        if not all(np.isfinite(v) for v in vals):
            continue
        o, h, lo, c, vol, pc = vals
        rows.append({"date": today.isoformat(), "time_ist": t.strftime("%H:%M"), "symbol": str(sym), "open": round(o, 2), "high": round(h, 2),
                     "low": round(lo, 2), "last": round(c, 2), "volume": round(vol), "prev_close": round(pc, 2),
                     "assumed_frac": round(float(assumed_frac), 4), "pick": int(sym in setup), "setup": setup.get(sym, ""), "via": str(via or "")})
    if not rows:
        return 0
    new = pd.DataFrame(rows)
    for c in COLUMNS:
        if c not in new.columns:
            new[c] = "" if c in TEXT else np.nan
    old = load(path)
    if not old.empty:
        keys = set(zip(new["date"], new["time_ist"], new["symbol"]))
        old = old[[k not in keys for k in zip(old["date"], old["time_ist"], old["symbol"])]]
    out = pd.concat([old, new[COLUMNS]], ignore_index=True) if not old.empty else new[COLUMNS]
    out = out.sort_values(["date", "time_ist", "symbol"], kind="stable").reset_index(drop=True)
    out = out[out["date"].isin(sorted(set(out["date"]))[-KEEP_SESSIONS:])]
    _save(out, path)
    return len(rows)


def complete(bars: dict, max_date: date | None = None, path: str = PATH) -> int:
    """
    Fill in, from COMPLETED daily bars, how the logged days ended: that day's final open / high / low / close / volume, and the next session's open / high /
    low / close. `max_date` = the newest session `bars` really completes (a day after it is never filled). Only blanks are filled - what is already there is
    never overwritten, so running it again changes nothing. Returns the number of rows changed.
    """
    snap = load(path)
    if snap.empty:
        return 0
    todo = snap["final_close"].isna() | snap["next_close"].isna()
    limit = max_date.isoformat() if max_date is not None else None
    if limit is not None:
        todo &= snap["date"] <= limit
    if not todo.any():
        return 0
    seen: dict = {}
    changed = 0
    for i in snap.index[todo]:
        sym, day = snap.at[i, "symbol"], snap.at[i, "date"]
        if sym not in seen:
            df = bars.get(sym)
            if df is None or len(df) == 0:
                seen[sym] = None
            else:
                days = np.asarray(pd.DatetimeIndex(df.index).strftime("%Y-%m-%d"))
                order = np.argsort(days, kind="stable")
                seen[sym] = (days[order], df[BAR].to_numpy(dtype=float)[order])
        if seen[sym] is None:
            continue
        days, vals = seen[sym]
        pos = int(np.searchsorted(days, day))
        if pos >= len(days) or days[pos] != day:
            continue                                                       # no bar for that day (a holiday mix-up, or the stock was not loaded)
        touched = False
        if pd.isna(snap.at[i, "final_close"]) and np.isfinite(vals[pos]).all():
            for c, v in zip(FINAL, vals[pos]):
                snap.at[i, c] = v
            touched = True
        nxt = pos + 1
        if pd.isna(snap.at[i, "next_close"]) and nxt < len(days) and (limit is None or days[nxt] <= limit) and np.isfinite(vals[nxt][:4]).all():
            snap.at[i, "next_date"] = days[nxt]
            for c, v in zip(NEXT[1:], vals[nxt][:4]):
                snap.at[i, c] = v
            touched = True
        changed += int(touched)
    if changed:
        _save(snap, path)
    return changed

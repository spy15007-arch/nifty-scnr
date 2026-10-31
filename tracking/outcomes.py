"""
The scoreboard: a ledger of every pick the scanner has issued, and what a
trader who followed the plan exactly would have got - after costs.

WHAT CHANGED (2026-10-19). The old tracker had three flaws that made its
numbers untrustworthy: a pick that never triggered was counted as a LOSS; a
same-day target+stop touch was counted as a WIN; and only the first target
was tracked, with no costs. Real trades are not run that way, so the old
"win rate" said little about real results. The old ledgers are kept for
reference as tracking/legacy_v1_*.csv.

HOW A TRADE IS RUN NOW - exactly the plan in scanner/tradesim.py, the same
state machine the backtest uses:

    PENDING   a buy-stop order that has not triggered yet (valid 5 sessions)
    OPEN      bought; the stop trails up behind the highest close
    CLOSED    stopped out (or 60-session time limit) - result in % and in R
    UNFILLED  the buy-stop never triggered, or the stock gapped >3% past it:
              no trade happened, so it is neither a win nor a loss
    VOID      a corporate action (split/bonus) is suspected from an
              impossible overnight gap; excluded from the statistics

R is the net return (after the 0.35% round-trip cost) divided by the initial
risk: -1R is a full stop-out, +3R is a gain three times the amount risked.

THE LEDGER IS REPLAYED, NOT PATCHED. Each run replays every unfinished trade
from its signal date over the COMPLETED daily sessions after it (today's
still-forming bar is never used). That makes updating idempotent - running it
twice, or after a missed day, gives the same answer - and a trade's state can
never be corrupted by a half-finished session.

No extra API calls are needed: it uses the bars the scan already fetched
(plus the caller may fetch a few extra symbols that are no longer in the scan).
"""
from __future__ import annotations

import logging
import math
import os
from datetime import date

import numpy as np
import pandas as pd

import config
from scanner import session
from scanner import tradesim as ts

logger = logging.getLogger(__name__)

TRACKING_DIR = "tracking"
LEDGER_PATH = f"{TRACKING_DIR}/trades.csv"

META_COLS = ["trade_id", "symbol", "scan_mode", "setup", "signal_date", "signal_time_ist",
             "quality_pct", "grade", "sector", "regime"]
PLAN_COLS = ["entry_mode", "trigger", "atr_pct", "stop_mult", "trail_mult", "valid_sessions",
             "max_hold", "max_chase", "cost"]
STATE_COLS = ["status", "sessions", "fill_n", "fill_px", "init_stop", "trail", "hc", "exit_n",
              "exit_px", "exit_reason"]
RESULT_COLS = ["fill_date", "exit_date", "net", "R", "peak_R", "last_close", "last_checked_date"]
# What the stock had done before the signal (added 2026-10-31, appended so an older ledger file keeps its column order; picks logged earlier
# have these blank = "not recorded", never "no"). trend200 = the 200-day average vs 20 sessions earlier (0.062 = up 6.2%), run_up = the gain the
# "already spiked" warning measures, z_run_up = that gain in standard deviations above the average stock's; strong_trend / spiked are the 1/0 flags
# the card showed. The raw numbers are kept so the cut-offs can be re-cut later on real trades.
SIGNAL_COLS = ["trend200", "run_up", "z_run_up", "strong_trend", "spiked"]
COLUMNS = META_COLS + PLAN_COLS + STATE_COLS + RESULT_COLS + SIGNAL_COLS

_TEXT_COLS = {"trade_id", "symbol", "scan_mode", "setup", "signal_date", "signal_time_ist", "grade", "sector",
              "regime", "entry_mode", "status", "exit_reason", "fill_date", "exit_date", "last_checked_date"}
ACTIVE = ("PENDING", "OPEN")
FINAL = ("CLOSED", "UNFILLED", "VOID")
VOID_GAP = 0.35          # an overnight gap beyond +/-35% is a corporate action, not a price move


# --------------------------------------------------------------------------- #
def load_ledger(path: str = LEDGER_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNS].copy()
    for col in COLUMNS:
        if col in _TEXT_COLS:
            df[col] = df[col].fillna("").astype(str)
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.reset_index(drop=True)


def save_ledger(df: pd.DataFrame, path: str = LEDGER_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, columns=COLUMNS, index=False)


def _row_dict(row: pd.Series) -> dict:
    """A ledger row as plain python values (NaN/blank become None) for the (de)serialisers."""
    out = {}
    for k, v in row.items():
        if isinstance(v, float) and math.isnan(v):
            out[k] = None
        elif isinstance(v, str) and v == "":
            out[k] = None
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
def active_symbols(df: pd.DataFrame | None = None, today: date | None = None,
                   cooldown_sessions: int | None = None) -> set[str]:
    """
    Symbols that must not be picked again: those with an unfinished trade, and
    those picked within the cool-down window (the research counted one trade
    per stock per 15 sessions, so a stock that stays set-up for a week is one
    idea, not five).
    """
    df = load_ledger() if df is None else df
    cooldown = config.COOLDOWN_SESSIONS if cooldown_sessions is None else cooldown_sessions
    today = today or session.ist_date()
    out: set[str] = set()
    for _, r in df.iterrows():
        if r["status"] in ACTIVE:
            out.add(r["symbol"])
            continue
        try:
            sig = date.fromisoformat(r["signal_date"])
        except Exception:
            continue
        if int(np.busday_count(sig, today)) < cooldown:
            out.add(r["symbol"])
    return out


def _signal_columns(p) -> dict:
    """
    The SIGNAL_COLS of a pick. A number the pick does not know stays blank (not recorded), and so does a flag that rests on it: a blank is
    never turned into a "no".
    """
    def num(name):
        v = getattr(p, name, None)
        try:
            return v if v is not None and math.isfinite(float(v)) else None
        except (TypeError, ValueError):
            return None

    trend, run_up, z = num("trend200_pct"), num("run_up_pct"), num("z_run_up")
    return {
        "trend200": trend, "run_up": run_up, "z_run_up": z,
        "strong_trend": int(bool(getattr(p, "strong_trend", False))) if trend is not None else None,
        "spiked": int(bool(getattr(p, "spiked", False))) if z is not None else None,
    }


def log_new_picks(picks: list, now=None, regime: str = "", path: str = LEDGER_PATH) -> int:
    """
    Append the picks (scanner.pipeline.Pick objects) as new trades: PENDING for
    a buy-stop, OPEN for a buy-now. A pick whose trade_id already exists is
    skipped, so re-running the same scan twice cannot double-count.
    """
    df = load_ledger(path)
    have = set(df["trade_id"])
    new_rows = []
    for p in picks:
        tid = f"{p.symbol}_{p.signal_date}_{p.scan_mode}"
        if tid in have:
            continue
        plan = p.plan()
        st = ts.new_state(plan)
        row = {c: None for c in COLUMNS}
        row.update({
            "trade_id": tid, "symbol": p.symbol, "scan_mode": p.scan_mode, "setup": p.setup,
            "signal_date": p.signal_date, "signal_time_ist": p.signal_time_ist,
            "quality_pct": round(p.quality_pct, 1), "grade": p.grade, "sector": p.sector or "", "regime": regime,
            **ts.plan_to_dict(plan), **ts.state_to_dict(st),
            "fill_date": p.signal_date if st.status == ts.OPEN else "",
            "last_close": p.price, "last_checked_date": p.signal_date,
            **_signal_columns(p),
        })
        # a freshly opened buy-now trade is flat (before costs) at the entry price
        if st.status == ts.OPEN:
            o = ts.outcome(plan, st, mark_px=p.price)
            row["net"], row["R"], row["peak_R"] = o["net"], o["R"], 0.0
        new_rows.append(row)
        have.add(tid)
    updated = pd.concat([df, pd.DataFrame(new_rows, columns=COLUMNS)], ignore_index=True) if new_rows else df
    # always write (even header-only) so the file exists from the first run
    save_ledger(updated, path)
    if new_rows:
        logger.info(f"📒 Scoreboard: logged {len(new_rows)} new trade(s)")
    return len(new_rows)


# --------------------------------------------------------------------------- #
def _completed(df: pd.DataFrame | None, now) -> pd.DataFrame | None:
    """The bars of `df` that are complete sessions: today's still-forming bar is dropped while the market is open."""
    if df is None or df.empty:
        return None
    d = df.sort_index()
    d = d[~d.index.duplicated(keep="last")]
    if session.session_state(now) == "open":
        last = d.index[-1]
        last_date = last.date() if hasattr(last, "date") else last
        if last_date == session.ist_date(now):
            d = d.iloc[:-1]
    return d if len(d) else None


def _idx_dates(index) -> list[date]:
    return [t.date() if hasattr(t, "date") else t for t in index]


def update_trades(bars: dict, now=None, path: str = LEDGER_PATH) -> dict:
    """
    Replay every unfinished trade over the completed sessions available in
    `bars`. Returns {"checked": n, "events": [...]} where each event describes
    something worth telling the user (a buy-stop filled, a stop hit, the stop
    raised, ...). Trades whose symbol has no bars this run are left untouched.
    """
    df = load_ledger(path)
    now = now or session.now_ist()
    report = {"checked": 0, "events": []}
    if df.empty:
        return report

    for idx in df.index[df["status"].isin(ACTIVE)]:
        row = df.loc[idx]
        sym = row["symbol"]
        bdf = _completed(bars.get(sym), now)
        if bdf is None:
            continue
        try:
            sig = date.fromisoformat(row["signal_date"])
        except Exception:
            continue
        dates = _idx_dates(bdf.index)
        keep = np.array([dt > sig for dt in dates], dtype=bool)
        after = bdf[keep]
        adates = [dt for dt, k in zip(dates, keep) if k]

        rd = _row_dict(row)
        plan = ts.plan_from_dict(rd)
        prev = ts.state_from_dict(rd)
        report["checked"] += 1

        # A split/bonus/demerger shows up as an impossible overnight gap. If the trade is still unfinished
        # when such a gap happens, its prices are no longer comparable: stop scoring it (VOID). A trade that
        # had already finished before the gap is unaffected.
        n_after = len(after)
        kv = None
        if n_after:
            before_sig = bdf[~keep]
            ref_close = float(before_sig["close"].iloc[-1]) if len(before_sig) else float("nan")
            a_open = after["open"].to_numpy(dtype=float)
            a_close = after["close"].to_numpy(dtype=float)
            prev_closes = np.concatenate([[ref_close], a_close[:-1]])
            with np.errstate(all="ignore"):
                gaps = a_open / prev_closes - 1.0
            bad_idx = np.nonzero(np.isfinite(gaps) & (np.abs(gaps) > VOID_GAP))[0]
            kv = int(bad_idx[0]) if len(bad_idx) else None
        run = after.iloc[:kv] if kv is not None else after
        st = ts.replay(plan, zip(run["open"], run["high"], run["low"], run["close"]))
        if kv is not None and st.status in (ts.PENDING, ts.OPEN):
            df.at[idx, "status"] = "VOID"
            df.at[idx, "exit_reason"] = "corporate_action_suspected"
            df.at[idx, "last_checked_date"] = str(adates[kv])
            report["events"].append({"kind": "void", "symbol": sym, "date": str(adates[kv]), "gap": float(gaps[kv])})
            continue
        last_close = float(run["close"].iloc[-1]) if len(run) else float(row["last_close"] if pd.notna(row["last_close"]) else plan.trigger)

        for k, v in ts.state_to_dict(st).items():
            df.at[idx, k] = v if v is not None else np.nan
        df.at[idx, "last_close"] = last_close
        df.at[idx, "last_checked_date"] = str(adates[-1]) if adates else row["last_checked_date"]
        if st.fill_n >= 0 and st.status in ("OPEN", "CLOSED"):
            df.at[idx, "fill_date"] = (str(adates[st.fill_n - 1]) if st.fill_n >= 1 and st.fill_n <= len(adates) else row["signal_date"])
        if st.status == "CLOSED" and 1 <= st.exit_n <= len(adates):
            df.at[idx, "exit_date"] = str(adates[st.exit_n - 1])
        o = ts.outcome(plan, st, mark_px=last_close)
        if o:
            df.at[idx, "net"], df.at[idx, "R"] = o["net"], o["R"]
            df.at[idx, "peak_R"] = ts.peak_r(plan, st)

        # ---- what is worth telling the user -----------------------------------------
        if prev.status == ts.PENDING and st.status in (ts.OPEN, ts.CLOSED):
            report["events"].append({"kind": "filled", "symbol": sym, "price": st.fill_px,
                                     "date": str(adates[st.fill_n - 1]) if 1 <= st.fill_n <= len(adates) else ""})
        if prev.status == ts.PENDING and st.status == ts.UNFILLED:
            report["events"].append({"kind": "unfilled", "symbol": sym, "reason": st.exit_reason})
        if prev.status in (ts.PENDING, ts.OPEN) and st.status == ts.CLOSED:
            report["events"].append({"kind": "closed", "symbol": sym, "reason": st.exit_reason, "price": st.exit_px,
                                     "net": o["net"] if o else None, "R": o["R"] if o else None,
                                     "date": str(adates[st.exit_n - 1]) if 1 <= st.exit_n <= len(adates) else ""})
        if prev.status == ts.OPEN and st.status == ts.OPEN and math.isfinite(prev.trail) and st.trail > prev.trail * 1.0005:
            report["events"].append({"kind": "trail_raised", "symbol": sym, "old": prev.trail, "new": st.trail})

    save_ledger(df, path)
    if report["checked"]:
        kinds = [e["kind"] for e in report["events"]]
        logger.info(f"📒 Scoreboard: checked {report['checked']} unfinished trade(s) -> "
                    f"{kinds.count('filled')} filled, {kinds.count('closed')} closed, "
                    f"{kinds.count('unfilled')} never triggered, {kinds.count('trail_raised')} stops raised")
    return report


# --------------------------------------------------------------------------- #
def scoreboard(df: pd.DataFrame | None = None, scan_mode: str | None = None, setup: str | None = None) -> dict:
    """Plain numbers describing the ledger: only CLOSED trades count toward win rate and R."""
    df = load_ledger() if df is None else df
    if scan_mode:
        df = df[df["scan_mode"] == scan_mode]
    if setup:
        df = df[df["setup"] == setup]
    closed = df[df["status"] == "CLOSED"]
    res = {
        "total": int(len(df)), "pending": int((df["status"] == "PENDING").sum()),
        "open": int((df["status"] == "OPEN").sum()), "closed": int(len(closed)),
        "unfilled": int((df["status"] == "UNFILLED").sum()), "void": int((df["status"] == "VOID").sum()),
        "first_signal": str(df["signal_date"].min()) if len(df) else "",
    }
    if len(closed):
        net = closed["net"].astype(float)
        r = closed["R"].astype(float)
        wins = net[net > 0]
        losses = net[net <= 0]
        res.update({
            "wins": int(len(wins)), "win_rate": float(len(wins) / len(closed)),
            "avg_net": float(net.mean()), "avg_R": float(r.mean()),
            "median_net": float(net.median()),
            "profit_factor": float(wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else float("inf"),
            "best_R": float(r.max()), "worst_R": float(r.min()),
            "avg_hold": float((closed["exit_n"] - closed["fill_n"]).mean()),
        })
    openpos = df[df["status"] == "OPEN"]
    if len(openpos):
        res["open_avg_R"] = float(openpos["R"].astype(float).mean())
    return res


def _short_date(iso: str) -> str:
    try:
        return pd.Timestamp(iso).strftime("%d %b").lstrip("0")
    except (ValueError, TypeError):
        return str(iso)


def format_scoreboard_line(df: pd.DataFrame | None = None, scan_mode: str | None = None) -> str:
    """One honest line for the message footer: only closed trades count (open ones are not wins or losses yet)."""
    s = scoreboard(df, scan_mode=scan_mode)
    if s["total"] == 0:
        return "Record: nothing tracked yet - results appear as picks are triggered and closed"
    since = f" since {_short_date(s['first_signal'])}" if s["first_signal"] else ""
    if not s["closed"]:
        return f"Record{since}: no trade closed yet"
    line = (f"Record{since}: {s['closed']} closed, {s['win_rate']:.0%} won, average {s['avg_R']:+.2f}R "
            f"({s['avg_net']:+.1%} per trade after costs)")
    if s["closed"] < 30:
        line += " - too few to judge yet (need 30+)"
    return line


def open_positions(df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Trades the user may be holding or waiting on, newest first."""
    df = load_ledger() if df is None else df
    act = df[df["status"].isin(ACTIVE)].copy()
    return act.sort_values("signal_date", ascending=False)

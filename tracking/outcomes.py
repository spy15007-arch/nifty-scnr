"""
Trade outcome tracker: a persistent ledger of every pick this scanner
has actually recommended, and what happened to it afterward - the
live companion to backtest/engine.py's historical simulation.

Reuses the exact resolution rule the backtest already validated (hit
target_1 before stop_loss = WON, vice versa = LOST; a same-day double
hit resolves to WON, matching _resolve_outcome's day-index tie-break)
and the exact same horizon per mode (HORIZON_DAYS_BY_MODE, imported
from backtest.engine - one shared source of truth) so live and
backtest numbers stay directly comparable, not two different
definitions of "did it work" drifting apart.

DESIGN - reuses data, never refetches: update_open_trades() is called
with the SAME `bars` dict main.py already fetched for today's scan.
No extra Angel One API calls, no extra rate-limit exposure - this
only reads bars that are already sitting in memory for the day's run.
If a symbol isn't in today's `bars` (fetch failed, or it fell out of
the universe), its open trade is just left untouched and re-checked
next run - no API call is made on its behalf.

KNOWN SIMPLIFICATION (shared with the backtest, kept consistent on
purpose): a trade resolves WON/LOST purely off target_1/stop_loss
being touched from signal_date onward, without first requiring
entry_trigger to have actually been crossed. In practice this rarely
inflates wins (target_1 is always further from entry_trigger than
entry_trigger is from the current price at signal time, so touching
target_1 almost always implies trading through the trigger too), but
it CAN mark a trade "LOST" that never actually triggered and so was
never really taken. The `triggered` column records whether
entry_trigger was ever touched while open, so that's visible in the
data even though it isn't used to change WON/LOST - changing the
resolution rule itself would make live numbers diverge from backtest
numbers, which this module exists to stay comparable with.

CSV LEDGER per mode (tracking/trade_log_<mode>.csv) - status is one
of OPEN / WON / LOST / EXPIRED. EXPIRED means neither target nor stop
was touched within HORIZON_DAYS_BY_MODE trading sessions - the same
"still open at horizon end" bucket the backtest already reports on.
"""
from __future__ import annotations
import logging
import os
import pandas as pd

from backtest.engine import HORIZON_DAYS_BY_MODE

logger = logging.getLogger(__name__)

TRACKING_DIR = "tracking"

LEDGER_COLUMNS = [
    "trade_id", "symbol", "scan_mode", "signal_date", "grade", "probability",
    "n_signals", "setup_stage", "entry_trigger", "stop_loss", "target_1",
    "status", "triggered", "days_held", "resolved_date", "exit_price",
    "pnl_pct", "last_checked_date",
]


def _ledger_path(scan_mode: str) -> str:
    return f"{TRACKING_DIR}/trade_log_{scan_mode}.csv"


def _as_bool(value) -> bool:
    """CSV round-trips bool columns as the strings 'True'/'False' (or
    leaves them as real bools if called within the same process) -
    bool('False') is True in plain Python, so this needs an explicit
    string check rather than a bare bool() cast."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _load_ledger(scan_mode: str) -> pd.DataFrame:
    path = _ledger_path(scan_mode)
    if not os.path.exists(path):
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    try:
        df = pd.read_csv(path, dtype={"signal_date": str, "resolved_date": str, "last_checked_date": str})
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    if df.empty:
        return pd.DataFrame(columns=LEDGER_COLUMNS)
    for col in LEDGER_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df["triggered"] = df["triggered"].apply(_as_bool)
    return df[LEDGER_COLUMNS].copy()


def _save_ledger(scan_mode: str, df: pd.DataFrame):
    os.makedirs(TRACKING_DIR, exist_ok=True)
    df.to_csv(_ledger_path(scan_mode), columns=LEDGER_COLUMNS, index=False)


def log_new_picks(scan_mode: str, picks: list[dict], signal_date: str) -> int:
    """
    Appends today's high-conviction picks as new OPEN trades. `picks`
    is a list of plain dicts (symbol, grade, probability, n_signals,
    setup_stage, entry_trigger, stop_loss, target_1) - the same shape
    main.py already builds for the scan CSV, kept decoupled here from
    ai.explain's Recommendation class on purpose. Skips a symbol that
    already has an OPEN trade in this mode's ledger - same setup,
    don't restart tracking on it. Returns how many rows were added.
    """
    df = _load_ledger(scan_mode)
    open_symbols = set(df.loc[df["status"] == "OPEN", "symbol"]) if not df.empty else set()

    new_rows = []
    for p in picks:
        symbol = p.get("symbol")
        if not symbol or symbol in open_symbols:
            continue
        if p.get("entry_trigger") is None or p.get("stop_loss") is None or p.get("target_1") is None:
            continue  # no valid levels, nothing to track
        new_rows.append({
            "trade_id": f"{symbol}_{signal_date}",
            "symbol": symbol,
            "scan_mode": scan_mode,
            "signal_date": signal_date,
            "grade": p.get("grade"),
            "probability": p.get("probability"),
            "n_signals": p.get("n_signals"),
            "setup_stage": p.get("setup_stage"),
            "entry_trigger": p.get("entry_trigger"),
            "stop_loss": p.get("stop_loss"),
            "target_1": p.get("target_1"),
            "status": "OPEN",
            "triggered": False,
            "days_held": 0,
            "resolved_date": None,
            "exit_price": None,
            "pnl_pct": None,
            "last_checked_date": signal_date,
        })
        open_symbols.add(symbol)  # guard against duplicate symbols within the same picks list

    # Always persist (even header-only) so the ledger file exists from the
    # first run onward - same "write valid headers even when empty"
    # convention already used for scan_results_*.csv, so a dashboard or
    # workflow checking for this file's presence never has to guess.
    updated = pd.concat([df, pd.DataFrame(new_rows, columns=LEDGER_COLUMNS)], ignore_index=True) if new_rows else df
    _save_ledger(scan_mode, updated)
    if new_rows:
        logger.info(f"📒 Tracker ({scan_mode}): logged {len(new_rows)} new open trade(s)")
    return len(new_rows)


def update_open_trades(scan_mode: str, bars: dict) -> dict:
    """
    Checks every OPEN trade in this mode's ledger against the LATEST
    bar already present in `bars` (today's already-fetched scan data -
    no new API calls here). Resolves WON/LOST exactly like
    backtest._resolve_outcome (same-day double-hit -> WON), and
    EXPIRED once a trade has been open HORIZON_DAYS_BY_MODE[scan_mode]
    checks without resolving either way.
    """
    df = _load_ledger(scan_mode)
    stats = {"checked": 0, "won": 0, "lost": 0, "expired": 0}
    if df.empty:
        return stats

    horizon = HORIZON_DAYS_BY_MODE.get(scan_mode, 30)
    open_idx = df.index[df["status"] == "OPEN"]

    for idx in open_idx:
        row = df.loc[idx]
        symbol = row["symbol"]
        sym_df = bars.get(symbol)
        if sym_df is None or sym_df.empty:
            continue  # no fresh data today - leave OPEN, try again next run

        today_bar = sym_df.iloc[-1]
        last_idx_val = sym_df.index[-1]
        today_date = str(last_idx_val.date()) if hasattr(last_idx_val, "date") else str(last_idx_val)
        if today_date == row["last_checked_date"]:
            continue  # already checked against this exact bar (e.g. a same-day re-run)

        stats["checked"] += 1
        target_1 = float(row["target_1"])
        stop_loss = float(row["stop_loss"])
        entry_trigger = float(row["entry_trigger"])

        triggered = _as_bool(row["triggered"]) or bool(today_bar["high"] >= entry_trigger)
        hit_target = bool(today_bar["high"] >= target_1)
        hit_stop = bool(today_bar["low"] <= stop_loss)
        days_held = int(row["days_held"]) + 1

        df.at[idx, "triggered"] = triggered
        df.at[idx, "days_held"] = days_held
        df.at[idx, "last_checked_date"] = today_date

        if hit_target or hit_stop:
            # Same-day double hit resolves to WON - matches
            # backtest._resolve_outcome's t_pos <= s_pos tie-break.
            # Known simplification, same caveat as the backtest: real
            # intraday sequencing isn't knowable from a daily bar.
            won = hit_target
            exit_price = target_1 if won else stop_loss
            df.at[idx, "status"] = "WON" if won else "LOST"
            df.at[idx, "resolved_date"] = today_date
            df.at[idx, "exit_price"] = round(exit_price, 2)
            df.at[idx, "pnl_pct"] = round((exit_price - entry_trigger) / entry_trigger, 4)
            stats["won" if won else "lost"] += 1
        elif days_held >= horizon:
            exit_price = float(today_bar["close"])
            df.at[idx, "status"] = "EXPIRED"
            df.at[idx, "resolved_date"] = today_date
            df.at[idx, "exit_price"] = round(exit_price, 2)
            df.at[idx, "pnl_pct"] = round((exit_price - entry_trigger) / entry_trigger, 4)
            stats["expired"] += 1

    _save_ledger(scan_mode, df)
    if stats["checked"]:
        logger.info(
            f"📒 Tracker ({scan_mode}): checked {stats['checked']} open trade(s) -> "
            f"{stats['won']} won, {stats['lost']} lost, {stats['expired']} expired"
        )
    return stats


def summarize_track_record(scan_mode: str) -> dict:
    df = _load_ledger(scan_mode)
    if df.empty:
        return {"total": 0, "open": 0, "resolved": 0, "expired": 0, "message": "No tracked trades yet"}

    resolved = df[df["status"].isin(["WON", "LOST"])].copy()
    result = {
        "total": len(df),
        "open": int((df["status"] == "OPEN").sum()),
        "resolved": len(resolved),
        "expired": int((df["status"] == "EXPIRED").sum()),
    }
    if len(resolved) == 0:
        result["message"] = "No trades resolved yet - check back once some picks hit target or stop"
        return result

    resolved["days_held"] = resolved["days_held"].astype(float)
    resolved["pnl_pct"] = resolved["pnl_pct"].astype(float)
    wins = int((resolved["status"] == "WON").sum())
    result.update({
        "wins": wins,
        "losses": len(resolved) - wins,
        "win_rate": round(wins / len(resolved), 3),
        "avg_days_held": round(resolved["days_held"].mean(), 1),
        "avg_pnl_pct": round(resolved["pnl_pct"].mean(), 4),
    })

    by_grade = {}
    for grade, g in resolved.groupby("grade"):
        w = int((g["status"] == "WON").sum())
        by_grade[grade] = {"count": len(g), "win_rate": round(w / len(g), 3)}
    result["by_grade"] = by_grade

    by_stage = {}
    for stage, g in resolved.groupby("setup_stage"):
        w = int((g["status"] == "WON").sum())
        by_stage[stage] = {"count": len(g), "win_rate": round(w / len(g), 3)}
    result["by_setup_stage"] = by_stage

    return result


def format_track_record_line(scan_mode: str) -> str:
    """Short one-line summary for the Telegram footer / dashboard."""
    s = summarize_track_record(scan_mode)
    if s.get("resolved", 0) == 0:
        return f"Track record ({scan_mode.upper()}): {s.get('open', 0)} open, no resolved trades yet"
    return (
        f"Track record ({scan_mode.upper()}): {s['wins']}W-{s['losses']}L ({s['win_rate']:.0%} win rate) "
        f"all-time, avg {s['avg_pnl_pct']:+.1%} per resolved trade, {s['open']} still open"
    )

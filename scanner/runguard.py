"""
Run guard: should this scan run go ahead, and has it already happened?

WHY: GitHub's own cron is not punctual - scheduled runs have started 4-9 hours
late. The scans can now be started on the dot by an external timer that calls
the workflow's manual trigger (see docs/TIMER_SETUP.md), and GitHub's cron is
kept only as a fallback. Two things must then hold:

  * If the timer already ran today's scan, the late GitHub run must NOT send a
    second copy.
  * A BTST message is only useful BEFORE the close (it is a same-day decision:
    buy before 3:30 PM). A scheduled BTST run that GitHub starts after ~2:20 PM
    is skipped; the evening Swing scan lists the same stocks with buy-stops.

Skipping rules:
  * schedule (GitHub's cron) and timer runs are skipped when that scan already ran for the same day/session -
    whichever of the two gets there first sends the message, the other stays quiet;
  * a scheduled BTST run that starts too late in the day is skipped (the timer run has no time limit of its own,
    its fetch simply stops at 2:25 PM);
  * a manual run (the "Run workflow" button) always goes ahead.

The record of what has run lives in data_cache/run_markers.json, which the
workflows commit like the rest of data_cache/.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, time, timedelta

import config
from scanner import session

logger = logging.getLogger(__name__)

MARKERS_PATH = "data_cache/run_markers.json"
SWING_READY = time(15, 45)       # from here a weekday's daily candle is final and the swing scan can use it


def run_source() -> str:
    """'schedule' (GitHub cron) | 'timer' (external timer) | 'manual' (button / local). Anything else counts as manual."""
    v = (os.getenv("TRADING_RUN_SOURCE") or "manual").strip().lower()
    return v if v in ("schedule", "timer", "manual") else "manual"


def last_session_key(now: datetime) -> str:
    """ISO date of the most recent weekday whose daily candle is final at `now`."""
    t = now.astimezone(session.IST) if now.tzinfo else now.replace(tzinfo=session.IST)
    d = t.date()
    if t.weekday() < 5 and t.time() >= SWING_READY:
        return d.isoformat()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def run_key(scan_mode: str, now: datetime) -> str:
    """BTST is a same-day run (key = today); the swing scan belongs to the last completed session."""
    if scan_mode == "btst":
        return session.ist_date(now).isoformat()
    return last_session_key(now)


def load_markers(path: str = MARKERS_PATH) -> dict:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def should_skip(scan_mode: str, source: str, now: datetime, path: str = MARKERS_PATH) -> tuple[bool, str]:
    """(skip, reason). Manual runs are never skipped; scheduled and timer runs are skipped as described above."""
    if source not in ("schedule", "timer"):
        return False, ""
    key = run_key(scan_mode, now)
    m = load_markers(path).get(scan_mode) or {}
    if m.get("key") == key and m.get("ok"):
        return True, (f"the {scan_mode} scan for {key} already ran ({m.get('source', '?')} run at {m.get('at', '?')}) - "
                      f"this late scheduled run would only repeat it")
    if scan_mode == "btst" and source == "schedule":
        t = now.astimezone(session.IST) if now.tzinfo else now.replace(tzinfo=session.IST)
        cutoff = time(*config.BTST_LATE_CUTOFF_IST)
        if session.session_state(t) != "open" or t.time() > cutoff:
            return True, (f"this scheduled BTST run started at {t.strftime('%H:%M')} IST, too late to be a before-the-close "
                          f"decision (GitHub's cron ran late). The evening Swing scan covers the same stocks")
    return False, ""


def record_run(scan_mode: str, now: datetime, source: str, picks: int, ok: bool = True,
               path: str = MARKERS_PATH) -> None:
    """Remember that this scan ran for its key. Best-effort: never raises."""
    try:
        data = load_markers(path)
        t = now.astimezone(session.IST) if now.tzinfo else now.replace(tzinfo=session.IST)
        data[scan_mode] = {"key": run_key(scan_mode, now), "at": t.strftime("%Y-%m-%d %H:%M IST"),
                           "source": source, "picks": int(picks), "ok": bool(ok)}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=1, sort_keys=True)
    except Exception:  # noqa: BLE001
        logger.warning("Could not write the run marker", exc_info=True)

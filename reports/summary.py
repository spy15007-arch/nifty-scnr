"""
Files a scan leaves in the repository (what the dashboard and the Markdown summaries read):

    scan_results_<mode>.csv                          latest picks (one row per pick, every number of the trade plan)
    reports/<mode>/scan_results_<mode>_<date>.csv    dated copy
    summary_<mode>.md   +  reports/<mode>/summary_<mode>.md   latest, compact table
    reports/<mode>/scan_<date>.md                    dated, with the reasons for each pick
    summary.md                                       both scans, newest section first (trimmed)

No network access here; every function only writes files.
"""
from __future__ import annotations

import html
import logging
import math
import os
import re
from datetime import datetime

import pandas as pd

from scanner import session
from scanner.pipeline import fmt_price

logger = logging.getLogger(__name__)

ROW_COLUMNS = [
    "symbol", "setup", "stage", "grade", "quality_pct", "entry_mode", "price", "entry_price", "max_entry",
    "stop_loss", "risk_pct", "trail_pct", "atr_pct", "plus_1R", "plus_2R", "plus_3R", "pivot_50d_high",
    "ext_pct", "gain_pct", "vol_ratio", "dist_52w_high_pct", "ret_12m_pct", "delivery_20d_pct",
    "days_since_break", "first_break", "sector", "valid_sessions",
    "signal_date", "signal_time_ist", "scan_mode",
]
MASTER_PATH = "summary.md"
MASTER_MAX_SECTIONS = 24
_SECTION_MARK = "<!-- scan-section -->"
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", text or ""))


def _ensure_dirs(scan_mode: str) -> str:
    folder = f"reports/{scan_mode}"
    os.makedirs(folder, exist_ok=True)
    return folder


def picks_frame(picks: list) -> pd.DataFrame:
    return pd.DataFrame([p.to_row() for p in picks], columns=ROW_COLUMNS)


# --------------------------------------------------------------------------- #
def _title(scan_mode: str) -> str:
    return {"btst": "🌙 BTST scan", "swing": "📈 Swing scan"}.get(scan_mode, f"{scan_mode} scan")


def _basis(result, now: datetime) -> str:
    t = now.astimezone(session.IST)
    if result.live:
        return f"Live prices at {t.strftime('%I:%M %p').lstrip('0')} IST (today's volume projected to a full day)"
    if result.ref_date:
        return f"Based on the close of {result.ref_date.strftime('%a %d %b %Y')}"
    return "No price data"


def _entry_cell(p) -> str:
    if p.entry_mode == "close":
        return f"Buy near {fmt_price(p.entry_price)}"
    return f"Buy at {fmt_price(p.entry_price)} (buy-stop order; skip if opens above {fmt_price(p.max_entry)})"


def _table(picks: list) -> list[str]:
    lines = [
        "| # | Stock | Setup | Quality | Entry | Stop | Trail below high close | Delivery 20d |",
        "| :-: | :-- | :-- | :-: | :-- | :-: | :-: | :-: |",
    ]
    if not picks:
        lines.append("| - | No new pick | - | - | - | - | - | - |")
        return lines
    for i, p in enumerate(picks, 1):
        dl = f"{p.dlv20:.0f}%" if p.dlv20 is not None and math.isfinite(p.dlv20) else "n/a"
        lines.append(
            f"| {i} | **{p.symbol}** | {'🔥' if p.setup == 'fresh' else '👀'} {p.stage_label} | {p.quality_pct:.0f} | "
            f"{_entry_cell(p)} | {fmt_price(p.stop)} (-{p.risk_pct:.1%}) | {p.trail_pct:.1%} | {dl} |"
        )
    return lines


def render_summary(scan_mode: str, result, now: datetime, market_line: str = "", tracked_text: str = "",
                   scoreboard_line: str = "", notes: list[str] | None = None, detail: bool = False) -> str:
    t = now.astimezone(session.IST)
    out = [f"# {_title(scan_mode)} - {t.strftime('%a %d %b %Y, %I:%M %p').replace(' 0', ' ')} IST", "",
           f"*{_basis(result, now)}*", ""]
    if market_line:
        out += [f"**Market:** {strip_html(market_line).replace('Market: ', '', 1)}", ""]
    b50 = result.breadth.get("breadth50") if result.breadth else None
    if isinstance(b50, float) and math.isfinite(b50):
        word = {"weak": "weak - use half size", "healthy": "healthy", "mixed": "mixed"}.get(result.regime, result.regime)
        out += [f"**Breadth:** {b50:.0%} of liquid stocks are above their 50-day average ({word})", ""]
    if not result.used_delivery:
        out += ["*Delivery-% data was not available: stocks were ranked on 7 price/volume factors only.*", ""]
    for n in (notes or []) + list(result.notes):
        out += [f"*{n}*", ""]
    f = result.funnel
    out += [f"*Funnel: {f.get('loaded', 0)} stocks checked → {f.get('fresh', 0)} fresh breakouts + {f.get('primed', 0)} about to break → "
            f"{f.get('passed_quality', 0)} in the top half by quality → {f.get('picks', 0)} listed"
            f"{', ' + str(f['repeats']) + ' already on the list' if f.get('repeats') else ''}*", ""]
    out += _table(result.picks)
    out += [""]
    if result.repeats:
        out += ["Already on your list (not repeated): " + ", ".join(s for s, _ in result.repeats[:20]), ""]
    if detail and result.picks:
        out += ["## Plan and reasons", ""]
        for i, p in enumerate(result.picks, 1):
            out += [f"### {i}. {p.symbol} - {p.stage_label}"]
            out += [f"- **Entry:** {_entry_cell(p)}",
                    f"- **Stop:** {fmt_price(p.stop)} (-{p.risk_pct:.1%}); trail it {p.trail_pct:.1%} below the highest close, raise only, never lower",
                    f"- **Reference levels:** +1R {fmt_price(p.r1)}, +2R {fmt_price(p.r2)}, +3R {fmt_price(p.r3)} (no fixed target)"]
            if p.reasons:
                out += ["- **Why:** " + "; ".join(p.reasons)]
            if p.cautions:
                out += ["- **Watch out:** " + "; ".join(p.cautions)]
            out += [""]
    if tracked_text:
        out += ["## Trades already on your list", "", strip_html(tracked_text).replace("📒 ", ""), ""]
    out += ["---",
            "*Ranked shortlist, not a guarantee. Quality is a filter (top half of liquid stocks), not a ladder: a higher number did not "
            "mean a better result in the back-test. Risk at most 1% of capital per trade (half in a weak market) and exit only through "
            "the stop, which trails up behind the price. Back-test 2019-2026: about 4 trades in 10 won and a few big winners paid for the "
            "many small stop-outs; 2019-23 was strong, 2024-26 was about break-even after costs. Judge it over 30+ trades.*"]
    if scoreboard_line:
        out += ["", f"*{scoreboard_line}*"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def prepend_master(section: str, path: str = MASTER_PATH, max_sections: int = MASTER_MAX_SECTIONS) -> None:
    """Put the new section on top of summary.md and keep only the newest `max_sections`."""
    old = ""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                old = f.read()
        except OSError:
            old = ""
    # An old-format master (no section markers: the pre-2026-10-19 file that grew with every run) is dropped;
    # every earlier scan is still in reports/<mode>/.
    parts = [p.strip("\n") for p in old.split(_SECTION_MARK) if p.strip()] if _SECTION_MARK in old else []
    kept = [section.strip("\n")] + parts[: max_sections - 1]
    _write(path, _SECTION_MARK + "\n" + ("\n\n" + _SECTION_MARK + "\n").join(kept) + "\n")


def write_scan_outputs(scan_mode: str, result, now: datetime, market_line: str = "", tracked_text: str = "",
                       scoreboard_line: str = "", notes: list[str] | None = None) -> dict:
    """Write every file listed in the module docstring. Returns the paths written."""
    folder = _ensure_dirs(scan_mode)
    t = now.astimezone(session.IST)
    date_str = (result.ref_date or t.date()).isoformat() if not result.live else t.date().isoformat()
    paths = {}

    df = picks_frame(result.picks)
    paths["csv"] = f"scan_results_{scan_mode}.csv"
    paths["csv_dated"] = f"{folder}/scan_results_{scan_mode}_{date_str}.csv"
    df.to_csv(paths["csv"], index=False)
    df.to_csv(paths["csv_dated"], index=False)

    short = render_summary(scan_mode, result, now, market_line, tracked_text, scoreboard_line, notes, detail=False)
    full = render_summary(scan_mode, result, now, market_line, tracked_text, scoreboard_line, notes, detail=True)
    paths["summary"] = f"summary_{scan_mode}.md"
    paths["summary_folder"] = f"{folder}/summary_{scan_mode}.md"
    paths["detail"] = f"{folder}/scan_{date_str}.md"
    _write(paths["summary"], short)
    _write(paths["summary_folder"], short)
    _write(paths["detail"], full)
    prepend_master(short)
    return paths

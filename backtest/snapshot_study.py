"""
Offline study of the BTST snapshot log (data_cache/btst_snapshots.csv.gz, written by data/snapshots.py):
  1. is the afternoon scan's projection of the day's volume right?  (it assumes a share of a normal day's volume has traded by the clock time; see
     scanner/session.py - an estimate that was never measured on this universe)
  2. what did stocks do from the afternoon price to the close, and to the next session's open and close - the picks, and the rest of the watchlist?

WHY (5 Nov 2026): the ten-year replay works on daily candles, so it cannot test the afternoon BTST scan at all. This log is the only way to check it against
real data - and it only starts on the day it was first written, so for a while it will say "too few days".

Usage:   python -m backtest.snapshot_study [data_cache/btst_snapshots.csv.gz] [--write reports/evidence/snapshot_study_<date>.md]

How to read the numbers:
  * "real share" = the volume the quote showed at the scan / the day's final volume. "projected / actual" = (volume so far / the share the scan ASSUMED) / the
    final volume: 1.00 means the projection was right, below 1 means the scan under-projects the day (volume looks smaller than it will turn out), above 1 that
    it over-projects (a breakout looks better confirmed than it is).
  * all the stocks of one day share the day's mood, so the number of DAYS matters far more than the number of stocks: a profile should not be changed on a
    few days. Groups under MIN_ROWS stocks, and verdicts on fewer than MIN_DAYS days, are described but not judged.
  * prices are the quotes the scan SAW; a real buyer's fill differs. Moves that no real stock makes between the snapshot and the next close (a split or bonus
    in between, or bad data) are dropped and counted. Costs of 0.35% for a round trip are shown separately.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from data import snapshots

MIN_ROWS = 30                  # a group of fewer stocks than this is described, not summarised
MIN_DAYS = 5                   # fewer days than this: one unusual day moves every stock together
COST = 0.0035                  # round trip, as in every other study here
IMPLAUSIBLE = 0.35             # a move bigger than +-35% between the snapshot and the next close is a split, bonus or bad data
OFF = 0.10                     # the projection is called off when its median is more than 10% from 1.00


def _bucket(clock: str) -> str:
    """'14:46' -> '14:45': the snapshots of one scan fall within a few minutes of each other."""
    try:
        h, m = (int(x) for x in str(clock).split(":")[:2])
    except ValueError:
        return "?"
    r = int(round((h * 60 + m) / 5.0)) * 5
    return f"{r // 60:02d}:{r % 60:02d}"


def _pct(x: float, signed: bool = False) -> str:
    return "n/a" if not np.isfinite(x) else (f"{x:+.1%}" if signed else f"{x:.0%}")


def volume_section(df: pd.DataFrame, min_rows: int = MIN_ROWS, min_days: int = MIN_DAYS) -> list[str]:
    out = ["== 1. How much of the day's volume had traded when the scan looked =="]
    fin = df[(df["volume"] > 0) & (df["final_volume"] > 0) & (df["assumed_frac"] > 0)].copy()
    if fin.empty:
        return out + ["No finished day yet: the evening run fills in each day's final volume after the close. Come back tomorrow."]
    fin["real"] = fin["volume"] / fin["final_volume"]
    fin["proj"] = fin["volume"] / fin["assumed_frac"] / fin["final_volume"]
    fin["clock"] = fin["time_ist"].map(_bucket)
    out.append(f"{'time':<6}{'days':>5}{'stocks':>8}{'assumed':>9}{'real share: median (middle half)':>36}{'projected/actual: median':>26}{'over 1.25x':>12}{'under 0.80x':>13}")
    groups = [(c, g) for c, g in fin.groupby("clock", sort=True)]
    if len(groups) > 1:
        groups.append(("all", fin))
    verdicts = []
    for clock, g in groups:
        days = g["date"].nunique()
        q1, med, q3 = g["real"].quantile([0.25, 0.5, 0.75])
        pm = float(g["proj"].median())
        out.append(f"{clock:<6}{days:>5}{len(g):>8}{_pct(float(g['assumed_frac'].median())):>9}"
                   f"{_pct(float(med)) + '  (' + _pct(float(q1)) + ' - ' + _pct(float(q3)) + ')':>36}{pm:>26.2f}"
                   f"{_pct(float((g['proj'] > 1.25).mean())):>12}{_pct(float((g['proj'] < 0.80).mean())):>13}")
        if clock != "all":
            verdicts.append((clock, days, len(g), float(g["assumed_frac"].median()), float(med), pm))
    out.append("")
    for clock, days, n, assumed, real, pm in verdicts:
        if days < min_days or n < min_rows:
            out.append(f"{clock}: {n} stocks on {days} day(s) - too few to judge the profile (one unusual day moves every stock together; "
                       f"wait for {min_days}+ days and {min_rows}+ stocks).")
        elif abs(pm - 1.0) <= OFF:
            out.append(f"{clock}: the assumed {_pct(assumed)} is within {OFF:.0%} of what was measured ({_pct(real)} of the day's volume at the median).")
        elif pm < 1.0:
            out.append(f"{clock}: the scan UNDER-projects the day (median projected/actual {pm:.2f}): it assumes {_pct(assumed)} of the volume has traded by "
                       f"then, the median stock had {_pct(real)}. Volume looks smaller than it turns out, so a real volume breakout can be missed.")
        else:
            out.append(f"{clock}: the scan OVER-projects the day (median projected/actual {pm:.2f}): it assumes {_pct(assumed)} of the volume has traded by "
                       f"then, the median stock had {_pct(real)}. A breakout looks better confirmed by volume than it is.")
    mismatch = float((fin["real"] > 1.05).mean())
    if mismatch > 0.10:
        out.append(f"CHECK THE DATA: for {_pct(mismatch)} of the stocks the volume at the scan EXCEEDS the day's final volume - the quote's volume and the "
                   f"candle's volume are probably not counted the same way, so section 1 cannot be trusted until that is explained.")
    return out


def _moves(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Per-stock moves from the snapshot price, with the implausible ones dropped (and counted)."""
    d = df[np.isfinite(df["last"]) & (df["last"] > 0)].copy()
    d["to_close"] = d["final_close"] / d["last"] - 1.0
    d["to_next_open"] = d["next_open"] / d["last"] - 1.0
    d["to_next_close"] = d["next_close"] / d["last"] - 1.0
    dropped = 0
    for c in ("to_close", "to_next_open", "to_next_close"):
        bad = d[c].abs() > IMPLAUSIBLE
        dropped += int(bad.sum())
        d.loc[bad, c] = np.nan
    return d, dropped


def move_section(df: pd.DataFrame, min_rows: int = MIN_ROWS, min_days: int = MIN_DAYS) -> list[str]:
    out = ["== 2. From the afternoon price to the close and to the next session =="]
    d, dropped = _moves(df)
    if not d[["to_close", "to_next_open", "to_next_close"]].notna().any().any():
        return out + ["No finished day yet - the evening run fills in the close, and the evening after that the next session."]
    out.append(f"{'who':<27}{'from the snapshot to':<22}{'stocks':>7}{'days':>6}{'mean':>9}{'median':>9}{'up':>6}{'mean after 0.35% cost':>24}")
    groups = (("the picks", d[d["pick"] == 1]), ("the rest of the watchlist", d[d["pick"] != 1]), ("all", d))
    for who, g in groups:
        for label, col, costed in (("the close", "to_close", False), ("next open", "to_next_open", True), ("next close", "to_next_close", True)):
            x = g[g[col].notna()]
            if len(x) < min_rows:
                out.append(f"{who:<27}{label:<22}{len(x):>7}{x['date'].nunique():>6}{'too few':>9}")
                continue
            net = _pct(float(x[col].mean() - COST), True) if costed else "-"
            out.append(f"{who:<27}{label:<22}{len(x):>7}{x['date'].nunique():>6}{_pct(float(x[col].mean()), True):>9}{_pct(float(x[col].median()), True):>9}"
                       f"{_pct(float((x[col] > 0).mean())):>6}{net:>24}")
    if dropped:
        out.append(f"({dropped} move(s) over +-{IMPLAUSIBLE:.0%} dropped as a split, bonus or bad data.)")
    days = int(d.loc[d["to_next_close"].notna(), "date"].nunique())
    if days < min_days:
        have = "No day has a next session yet" if days == 0 else f"Only {days} day(s) have a next session so far"
        out.append(f"{have}: read nothing into a mean until there are {min_days}+ days - one market-wide up or down day decides it.")
    return out


def analyse(df: pd.DataFrame, min_rows: int = MIN_ROWS, min_days: int = MIN_DAYS) -> list[str]:
    """The study as printable lines."""
    if df is None or df.empty:
        return ["The snapshot log is empty: the live afternoon BTST scan writes it (data_cache/btst_snapshots.csv.gz). Nothing to study yet."]
    out = []
    done = df["final_close"].notna()
    nxt = df["next_close"].notna()
    out.append(f"Snapshots {len(df)} of {df['symbol'].nunique()} stocks on {df['date'].nunique()} day(s), {df['date'].min()} to {df['date'].max()}; "
               f"{int(done.sum())} with the day finished ({df.loc[done, 'date'].nunique()} day(s)), {int(nxt.sum())} with the next session too; "
               f"{int((df['pick'] == 1).sum())} are picks; via live quotes {int((df['via'] == 'quotes').sum())}, candles {int((df['via'] == 'candles').sum())}.")
    out.append("")
    out += volume_section(df, min_rows, min_days)
    out.append("")
    out += move_section(df, min_rows, min_days)
    out.append("")
    out.append("== 3. Read this before relying on any of it ==")
    out.append(f"- Everything here is measured on the days the log has existed; all the stocks of a day move with the market, so {min_days}+ DAYS are needed before "
               f"a profile or a mean means anything - the stock counts overstate how much evidence there is.")
    out.append("- The prices are the quotes the scan saw. A buyer's order fills at a different price and time, and BTST costs (about 0.35% for the round trip) come "
               "on top; the 'after cost' column only subtracts that.")
    out.append("- This study changes nothing by itself: the volume profile (scanner/session.py) and the BTST rules stay as they are until a result here is "
               "clear on enough days, and then they are changed on purpose and recorded in reports/evidence.")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("log", nargs="?", default=snapshots.PATH, help="the snapshot log (default: data_cache/btst_snapshots.csv.gz)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(snapshots.load(args.log))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# BTST snapshot study: is the afternoon volume projection right, and what happened next?\n\nSource: `{os.path.basename(args.log)}`. "
                    f"How to read it: see the top of `backtest/snapshot_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

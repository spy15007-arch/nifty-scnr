"""
Offline study of a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv):

  * does it matter how far BELOW its 50-day high a stock was when it became an "about to break out" signal (0-3% below)?
  * and does the 200-day trend matter among the signals that were a little further below it?

WHY: the swing message's "BUILDING UP" watch list (strong stocks 3%-6% below their 50-day high) shows stocks the replay never traded - it only traded
stocks within 3% of the high - so nobody has measured what happens after buying one 3%-6% below it. The only evidence about "a bit further below the
high" is how the signals that were 0-3% below it did, and that is what this prints. It cannot say anything about 3%-6%.

Usage:   python -m backtest.gap_study reports/output/backtest_swing_yahoo_2026-10-30_trades.csv [--write reports/evidence/gap_study.md]

How to read the numbers: exactly as in backtest/spike_study.py (read the top of that file) - comparisons between groups of the SAME signals, after the
0.35% round-trip cost; "per filled trade" = the average result of orders that triggered, "per signal" = per card, an order that never triggered counting
as 0; the 95% ranges come from a bootstrap that resamples whole calendar months. Every group is a cut looked at AFTER seeing the data, so a "clear"
mark is a hint, not a proof. The distance is measured as the trades file has it: the close against the PRIOR 50-day high (`ext_pct`), which is a
little different from the watch list's own yardstick (the 50-day high including the latest session) - near the edges the two can disagree by a hair.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from backtest import spike_study as ss

STRONG_TREND = ss.STRONG_TREND                       # the 200-day average up at least 5% in 20 sessions
RISING = 0.01                                        # config.TREND_200D_RISING_MIN
SPLIT = 0.015                                        # "nearer half" / "further half" of the 0-3% band
NEEDED = {"ext_pct", "fill_date"}                    # what this study needs on top of the spike study's columns
# (lowest distance, highest distance, label) - distance = how far the close was BELOW the 50-day high, in fractions; negative = already above it
BANDS = ((-9.0, 0.0, "already above the old 50-day high"), (0.0, 0.01, "0%-1% below the 50-day high"), (0.01, 0.02, "1%-2% below the 50-day high"),
         (0.02, 9.0, "2%-3% below the 50-day high"))


def load(path: str) -> pd.DataFrame:
    """The trades file, checked for the columns of the spike study and the distance columns this study adds."""
    df = ss.load(path)
    missing = NEEDED - set(df.columns)
    if missing:
        raise SystemExit(f"{path} lacks the columns {sorted(missing)} - run the Backtest & Calibrate workflow with data_source=yahoo.")
    return df


def groups(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """The groups of 'about to break out' signals the study compares (boolean arrays in the frame's row order)."""
    primed = (df["setup"] == "primed").to_numpy()
    gap = (-df["ext_pct"]).to_numpy(float)           # positive = below the 50-day high
    trend = df["sma200_chg"].to_numpy(float)
    strong, rising = trend >= STRONG_TREND, trend >= RISING
    near, far = primed & (gap < SPLIT), primed & (gap >= SPLIT)
    out = {"primed": primed, "near": near, "far": far}
    for name, half in (("near", near), ("far", far)):
        out[f"{name}_strong"] = half & strong
        out[f"{name}_rising"] = half & rising & ~strong
        out[f"{name}_flat"] = half & ~rising
    for i, (lo, hi, _) in enumerate(BANDS):
        out[f"band{i}"] = primed & (gap >= lo) & (gap < hi)
    return out


def analyse(df: pd.DataFrame, reps: int = ss.B) -> list[str]:
    """The study as printable lines."""
    st = ss.Study(df, reps)
    g = groups(df)
    out: list[str] = []
    add = out.append
    add(f"Signals {len(df)} ({int(g['primed'].sum())} about-to-break-out), {st.months} months, {df['signal_date'].min().date()} to "
        f"{df['signal_date'].max().date()}; buy-stop results: {df['status'].value_counts().to_dict()}")
    add("The replay only traded stocks within 3% of their 50-day high, so these numbers say NOTHING directly about stocks 3%-6% below it.")

    add("\n== 1. About-to-break-out signals by how far below the 50-day high the close was ==")
    for i, (_, _, label) in enumerate(BANDS):
        if g[f"band{i}"].any():
            add(st.row(f"close {label}", g[f"band{i}"]))

    add(f"\n== 2. The further half ({SPLIT:.1%}+ below the high) minus the nearer half ==")
    add(st.row("nearer half", g["near"]))
    add(st.row("further half", g["far"]))
    add(st.drow("further - nearer (per signal)", g["far"], g["near"]))
    add(st.drow("further - nearer (per filled)", g["far"], g["near"], "filled"))
    add(st.drow("further - nearer (per signal, R)", g["far"], g["near"], "signal", "R"))

    add(f"\n== 3. By the 200-day trend (strong = up {STRONG_TREND:.0%}+ in 20 sessions, rising = up {RISING:.0%}+) ==")
    add("-- the further half:")
    for name, key in (("strong uptrend", "far_strong"), ("rising, not strong", "far_rising"), ("not rising", "far_flat")):
        if g[key].any():
            add(st.row(name, g[key]))
    if g["far_strong"].any() and g["far_flat"].any():
        add(st.drow("strong - not rising (per signal)", g["far_strong"], g["far_flat"]))
        add(st.drow("strong - not rising (per filled)", g["far_strong"], g["far_flat"], "filled"))
    if g["far_strong"].any() and g["far_rising"].any():
        add(st.drow("strong - rising, not strong (per filled)", g["far_strong"], g["far_rising"], "filled"))
    add("-- the nearer half:")
    for name, key in (("strong uptrend", "near_strong"), ("rising, not strong", "near_rising"), ("not rising", "near_flat")):
        if g[key].any():
            add(st.row(name, g[key]))
    add("-- the strong-uptrend group of the further half, period by period:")
    for pname, a, b in ss.PERIODS:
        m = g["far_strong"] & ((df["year"] >= a) & (df["year"] <= b)).to_numpy()
        if m.any():
            f, f_lo, f_hi, _ = st.mean(m, "filled")
            s, s_lo, s_hi, n = st.mean(m, "signal")
            add(f"  {pname:<10} signals {n:>4}  per filled {ss.pct(f)} [{ss.pct(f_lo)},{ss.pct(f_hi)}]  per signal {ss.pct(s)} [{ss.pct(s_lo)},{ss.pct(s_hi)}]")

    add("\n== 4. How long a buy order waited before it triggered (closed trades, calendar days from the signal) ==")
    for i, (_, _, label) in enumerate(BANDS):
        rows = df[g[f"band{i}"] & st.closed]
        if len(rows):
            days = (pd.to_datetime(rows["fill_date"]) - rows["signal_date"]).dt.days
            add(f"close {label:<36} {len(rows):>5} trades, median {days.median():.0f} days, average {days.mean():.1f}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(load(args.trades))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Distance below the 50-day high / building-up study\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/gap_study.py` and `backtest/spike_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

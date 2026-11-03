"""
Offline study of a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, from a run made after 3 Nov 2026 so that it
carries the stop-variant columns): should the stop-loss follow the price more closely - or less closely - than the shipped plan?

WHY: the user asked (3 Nov 2026) why the stop of a stock that has rallied a few percent had not moved. The shipped plan is a first stop 2.5 ATR below
the buy price and a trail 5 ATR below the highest CLOSE; the trail only overtakes the first stop once a close is about +1.2R above the buy price, and
then it still sits 5 ATR (11%-22%) below the highest close. Whether following the price more closely would pay was never replayed (fixed targets and
partial profit-taking were - they lowered the average). Every signal was therefore re-run, with the same entry and the same first stop, under five
other rules (backtest/engine.py STOP_VARIANTS, declared BEFORE looking at any result): a 3, a 4 and a 7 ATR trail, and the shipped trail plus a
break-even step (stop at the buy price once a close is +1R) and a lock step (+1R locked once a close is +2R).

Usage:   python -m backtest.stop_study <trades.csv> [--write reports/evidence/stop_study_<date>.md]

How to read the numbers: exactly as in backtest/spike_study.py (read the top of that file). Only signals that are finished under EVERY rule (the trade
closed, or the order never filled) are compared; "per signal" counts an order that never filled as 0; the 95% ranges come from a bootstrap that
resamples whole calendar months; "<-- clear" means the range excludes 0. There are five comparisons with the shipped plan, so about one "clear" mark in
twenty is expected by chance alone; the cuts by period, market and trend describe, they do not prove. The universe is today's NIFTY 500 (survivors),
so absolute levels are flattered - the DIFFERENCES between rules on the same signals are much less affected.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

import config
from backtest import engine
from backtest import spike_study as ss

STRONG_TREND = ss.STRONG_TREND
VARIANTS = engine.STOP_VARIANTS
PLANS = (("", "shipped"),) + tuple((name, name) for name, _, _ in VARIANTS)
NEEDED = {f"{name}_{col}" for name, _, _ in VARIANTS for col in ("status", "exit", "net", "R")}


def label(name: str) -> str:
    """The short name of a plan in the tables ('' = the shipped plan); legend() says what each one is."""
    return name or "shipped"


def legend() -> list[str]:
    """One line per plan, built from the engine's own definitions so the study can never describe a plan differently from how it was run."""
    lines = [f"  shipped  first stop {config.STOP_ATR_MULT:g} ATR below the buy price, trail {config.TRAIL_ATR_MULT:g} ATR below the highest close"]
    for name, trail_mult, steps in VARIANTS:
        text = f"  {name:<8} trail {trail_mult:g} ATR"
        for reach, lock in steps:
            text += f"; once a close is +{reach:g}R the stop is at least " + ("the buy price" if lock == 0 else f"+{lock:g}R above it")
        lines.append(text)
    return lines


def load(path: str) -> pd.DataFrame:
    """The trades file, checked for the spike study's columns and the stop-variant columns."""
    df = ss.load(path)
    missing = NEEDED - set(df.columns)
    if missing:
        raise SystemExit(f"{path} lacks the stop-variant columns {sorted(missing)} - run the Backtest & Calibrate workflow "
                         f"(backtest, swing, data_source=yahoo) with the code of 3 Nov 2026 or later.")
    return df


def col(name: str, what: str) -> str:
    """The trades-file column of a plan ('' = the shipped plan, whose columns carry no prefix and whose exit reason is called exit_reason)."""
    if not name:
        return "exit_reason" if what == "exit" else what
    return f"{name}_{what}"


def finished(df: pd.DataFrame) -> np.ndarray:
    """Signals finished under every plan: the trade closed, or the order never filled."""
    ok = np.ones(len(df), dtype=bool)
    for name, _ in PLANS:
        ok &= df[col(name, "status")].isin(["CLOSED", "UNFILLED"]).to_numpy()
    return ok


def per_signal(df: pd.DataFrame, name: str, what: str = "net") -> np.ndarray:
    """A plan's result per signal (an order that never filled counts as 0)."""
    closed = (df[col(name, "status")] == "CLOSED").to_numpy()
    return np.where(closed, df[col(name, what)].to_numpy(float), 0.0)


def _plan_frame(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """The frame as the spike study's Study class wants it: the plan's own status / net / R in the base columns."""
    if not name:
        return df
    out = df.copy()
    for what in ("status", "net", "R"):
        out[what] = df[col(name, what)]
    return out


def _stars(lo: float, hi: float) -> str:
    return "*" if (lo > 0 or hi < 0) else " "


def analyse(df: pd.DataFrame, reps: int = ss.B) -> list[str]:
    """The study as printable lines."""
    out: list[str] = []
    add = out.append
    fin = finished(df)
    st = ss.Study(df, reps)                           # months and bootstrap draws are the same for every plan
    base_net, base_R = per_signal(df, "", "net"), per_signal(df, "", "R")
    add(f"Signals {len(df)}, finished under every plan {int(fin.sum())}, {st.months} months, {df['signal_date'].min().date()} to "
        f"{df['signal_date'].max().date()}")

    add("\nThe plans (same signals, same entries, same first stop; only the trail differs):")
    out.extend(legend())
    add("\n== 1. Every plan on the same finished signals (after 0.35% costs) ==")
    for name, _ in PLANS:
        add(ss.Study(_plan_frame(df, name), reps).row(label(name), fin))
    add("\nHow the filled trades ended (share of filled trades) and how big the typical loser / winner was:")
    add(f"{'plan':<10} {'first stop':>10} {'trail':>7} {'time limit':>11} {'median':>8} {'avg loser':>10} {'avg winner':>11}")
    filled = fin & (df["status"] == "CLOSED").to_numpy()
    for name, _ in PLANS:
        reason = df[col(name, "exit")].to_numpy()[filled]
        net = df[col(name, "net")].to_numpy(float)[filled]
        first = np.isin(reason, ["stop", "gap_stop", "same_day_stop"]).mean()
        add(f"{label(name):<10} {first:>10.0%} {(reason == 'trail').mean():>7.0%} {(reason == 'time').mean():>11.0%} {np.median(net):>+8.2%} "
            f"{net[net <= 0].mean():>+10.2%} {net[net > 0].mean():>+11.2%}")

    add("\n== 2. Each variant minus the shipped plan, per signal (what a person who follows every card earns per card) ==")
    for name, _ in PLANS[1:]:
        add(st.prow(label(name), per_signal(df, name, "net"), base_net, fin))
    add("-- the same in R (profit as a multiple of the amount risked):")
    for name, _ in PLANS[1:]:
        d, lo, hi, n = st.paired(per_signal(df, name, "R"), base_R, fin)
        add(f"{label(name):<10} n={n:>6}  {d:+6.3f}R [{lo:+6.3f}R,{hi:+6.3f}R]{'  <-- clear' if (lo > 0 or hi < 0) else ''}")

    add("\n== 3. The same difference by period, market condition and trend ('*' = the 95% range excludes 0) ==")
    year = df["year"].to_numpy()
    cuts: list[tuple[str, np.ndarray]] = [(p, (year >= a) & (year <= b)) for p, a, b in ss.PERIODS]
    regime = df["regime"].to_numpy()
    cuts += [(f"{r} market", regime == r) for r in ("healthy", "mixed", "weak")]
    strong = (df["sma200_chg"] >= STRONG_TREND).to_numpy()
    cuts += [("strong uptrend", strong), ("other trends", ~strong)]
    add(f"{'plan':<10}" + "".join(f"{c:>17}" for c, _ in cuts))
    for name, _ in PLANS[1:]:
        cells = []
        for _, m in cuts:
            sub = fin & m
            if int(sub.sum()) < 30:
                cells.append(f"{'n/a':>17}")
                continue
            d, lo, hi, _n = st.paired(per_signal(df, name, "net"), base_net, sub)
            cells.append(f"{d * 100:>+14.2f}%{_stars(lo, hi)} ")
        add(f"{label(name):<10}" + "".join(cells))
    add("signals in each cut: " + ", ".join(f"{c} {int((fin & m).sum())}" for c, m in cuts))

    add("\n== 4. What a rally gives back: filled trades that, under the shipped plan, had a CLOSE at least +1R / +2R / +3R above the buy price ==")
    peak = df["peak_R"].to_numpy(float)
    for k in (1.0, 2.0, 3.0):
        m = filled & np.isfinite(peak) & (peak >= k)
        add(f"-- peak close >= +{k:g}R: {int(m.sum())} trades ({m.sum() / max(filled.sum(), 1):.0%} of filled trades)")
        add(f"{'plan':<10} {'avg result':>11} {'ended below 0':>14} {'below +1R':>10} {'at +3R or more':>15}")
        for name, _ in PLANS:
            r = df[col(name, "R")].to_numpy(float)[m]
            net = df[col(name, "net")].to_numpy(float)[m]
            add(f"{label(name):<10} {net.mean():>+11.2%} {(net < 0).mean():>14.0%} {(r < 1).mean():>10.0%} {(r >= 3).mean():>15.0%}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test (with the stop-variant columns)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(load(args.trades))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Stop-loss study: follow the price more or less closely?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/stop_study.py` and `backtest/spike_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

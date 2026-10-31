"""
Offline study of a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv):

  * does a stock that has ALREADY SPIKED before the signal do better or worse than the rest (the card's warning)?
  * does a rising 200-day trend help?
  * does WAITING FOR A PULL-BACK beat buying at the buy-stop trigger?

Usage:   python -m backtest.spike_study reports/output/backtest_swing_yahoo_2026-10-30_trades.csv [--write reports/evidence/spike_study.md]

How to read the numbers (the rules this file keeps):
  * Every comparison is between two groups of the SAME signals (same days, same stocks, same exit rules), after the 0.35% round-trip cost.
  * "per filled trade" = the average result of trades that actually filled and closed.
    "per signal"       = the group's total result divided by its number of signals, an order that never filled counting as 0 -
                         what a person who follows every card earns per card.
  * Uncertainty is a 95% range from a bootstrap that resamples whole CALENDAR MONTHS, because trades opened in the same weeks move together.
    A range that includes 0 means the data cannot tell the groups apart; a "<-- clear" mark means the range excludes 0.
    Many comparisons are printed, so a few "clear" marks are expected by chance alone: only the pre-declared comparison
    (flagged vs not flagged) decides the warning's cut-offs; the rest describe, they do not prove.
  * The universe is TODAY's NIFTY 500 (survivors) and the score is the price-only one, so absolute levels are flattered; differences between
    groups drawn from the same universe are much less affected - but not immune (a stock that spiked and later disappeared is missing).
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

import config

B = 4000
SEED = 20261030
OLD = {"z": 1.28, "gain": 0.05, "trend": 0.005}          # the warning's cut-offs before 30 Oct 2026 (the shipped ones are in config.py)
Z_EDGES = [(-99, 0), (0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.5), (2.5, 99)]
TREND_EDGES = [(-9, 0), (0, .01), (.01, .02), (.02, .03), (.03, .05), (.05, .08), (.08, 9)]
STRONG_TREND = 0.05                                       # the 200-day average up at least 5% in 20 sessions
WEAK_BREADTH = 0.40
PERIODS = (("2016-2019", 2016, 2019), ("2020-2022", 2020, 2022), ("2023-2026", 2023, 2100))
NEEDED = {"signal_date", "status", "net", "R", "z_run_up", "run_up", "sma200_chg", "setup", "breadth50", "atr_pct", "regime",
          "pb1_status", "pb1_net", "pb2_status", "pb2_net"}


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = NEEDED - set(df.columns)
    if missing:
        raise SystemExit(f"{path} lacks the research columns {sorted(missing)} - run the Backtest & Calibrate workflow with data_source=yahoo.")
    df["signal_date"] = pd.to_datetime(df["signal_date"])
    df["year"] = df["signal_date"].dt.year
    months = df["signal_date"].dt.to_period("M")
    df["mi"] = months.map({m: i for i, m in enumerate(sorted(months.unique()))}).astype(int)
    return df


def flagged(df: pd.DataFrame, z: float, gain: float) -> np.ndarray:
    return ((df["z_run_up"] >= z) & (df["run_up"] >= gain)).to_numpy()


def pct(x: float) -> str:
    return "   n/a" if x is None or not np.isfinite(x) else f"{x * 100:+6.2f}%"


class Study:
    """Month-clustered bootstrap over the signals of one trades file."""

    def __init__(self, df: pd.DataFrame, reps: int = B, seed: int = SEED):
        self.df = df
        self.months = int(df["mi"].max()) + 1
        self.mi = df["mi"].to_numpy()
        self.closed = (df["status"] == "CLOSED").to_numpy()
        self.resolved = df["status"].isin(["CLOSED", "UNFILLED"]).to_numpy()
        self.net = np.where(self.closed, df["net"].to_numpy(float), 0.0)
        self.R = np.where(self.closed, df["R"].to_numpy(float), 0.0)
        self.idx = np.random.default_rng(seed).integers(0, self.months, size=(reps, self.months))

    def _sum(self, values: np.ndarray, mask: np.ndarray) -> np.ndarray:
        return np.bincount(self.mi[mask], weights=values[mask], minlength=self.months)

    def _group(self, mask: np.ndarray, basis: str, value: str):
        val = self.net if value == "net" else self.R
        num = self._sum(val, mask & self.closed)
        den = self._sum(np.ones(len(mask)), mask & (self.closed if basis == "filled" else self.resolved))
        return num, den

    def _ratio(self, num: np.ndarray, den: np.ndarray):
        point = num.sum() / den.sum() if den.sum() else float("nan")
        boot = num[self.idx].sum(1) / np.maximum(den[self.idx].sum(1), 1e-9)
        return point, boot

    def mean(self, mask: np.ndarray, basis: str = "signal", value: str = "net"):
        num, den = self._group(mask, basis, value)
        point, boot = self._ratio(num, den)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        return point, lo, hi, int(den.sum())

    def diff(self, a: np.ndarray, b: np.ndarray, basis: str = "signal", value: str = "net"):
        (na, da), (nb, db) = self._group(a, basis, value), self._group(b, basis, value)
        pa, ba = self._ratio(na, da)
        pb, bb = self._ratio(nb, db)
        boot = ba - bb
        lo, hi = np.percentile(boot, [2.5, 97.5])
        return pa - pb, lo, hi, float((boot > 0).mean())

    def paired(self, a_net: np.ndarray, b_net: np.ndarray, mask: np.ndarray):
        """Average of (a - b) over the masked signals; a and b are per-signal results (unfilled = 0)."""
        num, den = self._sum(a_net - b_net, mask), self._sum(np.ones(len(mask)), mask)
        point, boot = self._ratio(num, den)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        return point, lo, hi, int(den.sum())

    # ---- printable rows -------------------------------------------------------------------------------------------------
    def row(self, label: str, mask: np.ndarray) -> str:
        f, f_lo, f_hi, n_filled = self.mean(mask, "filled")
        s, s_lo, s_hi, n_sig = self.mean(mask, "signal")
        sub = mask & self.closed
        win = float((self.df.loc[sub, "net"] > 0).mean()) if sub.any() else float("nan")
        rate = n_filled / n_sig if n_sig else float("nan")
        return (f"{label:<44} signals {n_sig:>6}  filled {rate:4.0%}  win {win:4.0%}  per filled {pct(f)} [{pct(f_lo)},{pct(f_hi)}]"
                f"  per signal {pct(s)} [{pct(s_lo)},{pct(s_hi)}]")

    def drow(self, label: str, a: np.ndarray, b: np.ndarray, basis: str = "signal", value: str = "net") -> str:
        d, lo, hi, p = self.diff(a, b, basis, value)
        fmt = (lambda x: f"{x:+6.3f}R") if value == "R" else pct
        return f"{label:<44} diff {fmt(d)} [{fmt(lo)},{fmt(hi)}]  P(first better) {p:4.0%}{'  <-- clear' if (lo > 0 or hi < 0) else ''}"

    def prow(self, label: str, a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> str:
        d, lo, hi, n = self.paired(a, b, mask)
        return f"{label:<44} n={n:>6}  {pct(d)} [{pct(lo)},{pct(hi)}]{'  <-- clear' if (lo > 0 or hi < 0) else ''}"


def analyse(df: pd.DataFrame, reps: int = B) -> list[str]:
    """The full study as printable lines."""
    st = Study(df, reps)
    out: list[str] = []
    add = out.append
    everything = np.ones(len(df), bool)
    z, gain, trend, year = df["z_run_up"], df["run_up"], df["sma200_chg"], df["year"]
    cur = flagged(df, config.SPIKE_5D_Z, config.SPIKE_5D_MIN)
    old = flagged(df, OLD["z"], OLD["gain"])
    rising = (trend >= config.TREND_200D_RISING_MIN).to_numpy()
    strong = (trend >= STRONG_TREND).to_numpy()
    weak = ((df["breadth50"] < WEAK_BREADTH) & df["breadth50"].notna()).to_numpy()
    period = {name: ((year >= a) & (year <= b)).to_numpy() for name, a, b in PERIODS}

    add(f"Signals {len(df)}, resolved {int(st.resolved.sum())}, closed {int(st.closed.sum())}, {st.months} months, "
        f"{df['signal_date'].min().date()} to {df['signal_date'].max().date()}; buy-stop results: {df['status'].value_counts().to_dict()}")
    add(f"Warning cut-offs now: gain >= {config.SPIKE_5D_MIN:.0%} and z >= {config.SPIKE_5D_Z:g}; 'rising' = 200-day trend >= {config.TREND_200D_RISING_MIN:.1%}. "
        f"Before 30 Oct 2026: 5% / 1.28 / 0.5%.")
    add("")
    add(st.row("ALL signals (buy-stop at the trigger)", everything))

    add("\n== 1. THE QUESTION: does an 'already spiked' setup do worse? (same signals, flagged vs not flagged) ==")
    for name, m in (("flagged, current cut-offs", cur), ("not flagged, current", ~cur), ("flagged, old cut-offs", old), ("not flagged, old", ~old)):
        add(st.row(name, m))
    add(st.drow("current: flagged - not flagged (per filled)", cur, ~cur, "filled"))
    add(st.drow("current: flagged - not flagged (per signal)", cur, ~cur))
    add(st.drow("current: flagged - not flagged (per signal, R)", cur, ~cur, "signal", "R"))
    add(st.drow("old:     flagged - not flagged (per filled)", old, ~old, "filled"))
    add(st.drow("old:     flagged - not flagged (per signal)", old, ~old))
    add(f"share of signals flagged: current {cur.mean():.0%}, old {old.mean():.0%}")
    add("-- the same, period by period (per signal):")
    for name, m in period.items():
        add(st.drow(f"  {name}", cur & m, ~cur & m))
    add(st.drow("  weak market (breadth < 40%)", cur & weak, ~cur & weak))
    add(st.drow("  healthier market", cur & ~weak, ~cur & ~weak))
    for kind in ("fresh", "primed"):
        m = (df["setup"] == kind).to_numpy()
        add(st.drow(f"  {kind} setups", cur & m, ~cur & m))
    add("-- any other cut-off pair (flagged - not flagged, per signal | per filled):")
    for zz, gg in ((0.5, 0.03), (1.0, 0.04), (1.28, 0.05), (1.5, 0.06), (2.0, 0.08)):
        m = flagged(df, zz, gg)
        add(f"  z>={zz:<4g} & >={gg:.0%}: {m.mean():4.0%} flagged | " + st.drow("", m, ~m).strip() + " | " + st.drow("", m, ~m, "filled").strip())

    add("\n== 2. Gradient by run-up z (the 5-day gain before the signal, in standard deviations) ==")
    for lo, hi in Z_EDGES:
        add(st.row(f"z in [{lo:g}, {hi:g})", ((z >= lo) & (z < hi)).to_numpy()))
    ok = st.closed & z.notna().to_numpy()
    cl = df[ok]
    if len(cl) >= 30:
        rho = cl["z_run_up"].rank().corr(cl["net"].rank())
        parts = {m: g for m, g in cl.groupby("mi")}
        rng = np.random.default_rng(SEED + 1)
        rhos = []
        for _ in range(min(reps, 500)):
            pick = rng.integers(0, st.months, size=st.months)
            s = pd.concat([parts[m] for m in pick if m in parts])
            rhos.append(s["z_run_up"].rank().corr(s["net"].rank()))
        add(f"rank correlation of z with the after-cost return of closed trades: {rho:+.3f} [{np.percentile(rhos, 2.5):+.3f},{np.percentile(rhos, 97.5):+.3f}] "
            f"(a slightly negative number means a slightly lower win rate; the average is not lower when the winners are bigger)")
    else:
        add("rank correlation of z with the after-cost return: too few closed trades")

    add("\n== 3. Swings: are already-spiked trades more volatile? (closed trades) ==")
    for name, m in (("flagged (current)", cur), ("not flagged", ~cur)):
        s = df[st.closed & m]
        q = s["net"].quantile([.1, .25, .5, .75, .9]).to_numpy()
        add(f"{name:<20} n={len(s):>5}  std of net {s['net'].std():.1%}  std of R {s['R'].std():.2f}  10/25/50/75/90th percentile of net: "
            + " ".join(f"{x:+.1%}" for x in q) + f"  average ATR {s['atr_pct'].mean():.2%}")

    add("\n== 4. 200-day trend (change of the 200-day average over 20 sessions) ==")
    for lo, hi in TREND_EDGES:
        add(st.row(f"trend in [{lo:.0%}, {hi:.0%})", ((trend >= lo) & (trend < hi)).to_numpy()))
    add(st.drow(f"rising (>= {config.TREND_200D_RISING_MIN:.1%}) - not rising (per signal)", rising, ~rising))
    add(st.drow(f"rising (>= {config.TREND_200D_RISING_MIN:.1%}) - not rising (per filled)", rising, ~rising, "filled"))
    add(f"-- strong uptrend (>= {STRONG_TREND:.0%}, {strong.mean():.0%} of signals) vs the rest:")
    add(st.row("strong uptrend", strong))
    add(st.row("the rest", ~strong))
    add(st.drow("strong - rest (per signal)", strong, ~strong))
    add(st.drow("strong - rest (per filled)", strong, ~strong, "filled"))
    add(st.drow("strong - rest (per signal, R)", strong, ~strong, "signal", "R"))
    for name, m in period.items():
        add(st.drow(f"  {name}", strong & m, ~strong & m) + f"   (strong signals: {int((strong & m).sum())})")
    add(st.drow("  first half (2016-2021)", strong & (year <= 2021).to_numpy(), ~strong & (year <= 2021).to_numpy()))
    add(st.drow("  second half (2022-2026)", strong & (year >= 2022).to_numpy(), ~strong & (year >= 2022).to_numpy()))
    add(st.drow("  weak market", strong & weak, ~strong & weak) + f"   (strong signals: {int((strong & weak).sum())})")
    add(st.drow("  healthier market", strong & ~weak, ~strong & ~weak))
    sub = df[st.closed & strong]
    add(f"strong-uptrend trades: {len(sub)} closed in {sub['symbol'].nunique() if 'symbol' in sub else '?'} different stocks; "
        f"average ATR {df.loc[strong, 'atr_pct'].mean():.2%} vs {df.loc[~strong, 'atr_pct'].mean():.2%} for the rest "
        f"(they are more volatile, but the gain holds in R, which is measured per unit of risk)")
    by_year = sub.groupby("year")["net"].agg(["count", "mean"])
    add("by year (trades, average): " + ", ".join(f"{y}: {int(r['count'])} / {r['mean']:+.1%}" for y, r in by_year.iterrows()))

    add("\n== 5. Spike and trend together ==")
    for name, m in (("rising, not spiked", rising & ~cur), ("rising, spiked", rising & cur), ("not rising, not spiked", ~rising & ~cur),
                    ("not rising, spiked", ~rising & cur)):
        add(st.row(name, m))
    add(st.drow("rising: spiked - not spiked", rising & cur, rising & ~cur))
    add(st.drow("strong uptrend: spiked - not spiked", strong & cur, strong & ~cur))

    add("\n== 6. WAITING FOR A PULL-BACK vs the buy-stop (only signals that are finished in all three entry styles) ==")
    done = df[["status", "pb1_status", "pb2_status"]].isin(["CLOSED", "UNFILLED"]).all(axis=1).to_numpy()
    stop = st.net
    pb1 = np.where(df["pb1_status"] == "CLOSED", df["pb1_net"].fillna(0).to_numpy(float), 0.0)
    pb2 = np.where(df["pb2_status"] == "CLOSED", df["pb2_net"].fillna(0).to_numpy(float), 0.0)
    fill = {k: float(((df[k] == "CLOSED") & done).sum() / max(int(done.sum()), 1)) for k in ("status", "pb1_status", "pb2_status")}
    add(f"{int(done.sum())} of {len(df)} signals finished in every style. Fill rates: buy-stop {fill['status']:.0%}, "
        f"buy-limit 1 ATR below (5 sessions) {fill['pb1_status']:.0%}, buy-limit 2 ATR below (10 sessions) {fill['pb2_status']:.0%}")
    add(f"{'':<44} {'':>9}   (result per signal after costs: buy-stop | pull-back 1 | pull-back 2)")
    for name, m in (("ALL signals", everything), ("flagged (current)", cur), ("not flagged", ~cur), ("flagged (old)", old),
                    ("flagged and rising", cur & rising), ("strong uptrend", strong)):
        m = m & done
        a, b, c = st.paired(stop, 0 * stop, m), st.paired(pb1, 0 * stop, m), st.paired(pb2, 0 * stop, m)
        add(f"{name:<44} n={a[3]:>6}  {pct(a[0])} | {pct(b[0])} | {pct(c[0])}")
        add(st.prow("    pull-back 1 minus buy-stop", pb1, stop, m))
        add(st.prow("    pull-back 2 minus buy-stop", pb2, stop, m))
    add("-- by run-up z bucket (pull-back 1 minus buy-stop, per signal):")
    for lo, hi in Z_EDGES:
        add(st.prow(f"  z in [{lo:g}, {hi:g})", pb1, stop, ((z >= lo) & (z < hi)).to_numpy() & done))
    add("-- for flagged stocks, period by period (pull-back 1 | pull-back 2, minus buy-stop):")
    for name, m in period.items():
        m = m & cur & done
        d1, d2 = st.paired(pb1, stop, m), st.paired(pb2, stop, m)
        add(f"  {name:<12} n={d1[3]:>5}  {pct(d1[0])} [{pct(d1[1])},{pct(d1[2])}] | {pct(d2[0])} [{pct(d2[1])},{pct(d2[2])}]")
    add("-- what the trades that DID fill looked like (flagged stocks):")
    s = df[cur & done]
    for key, label in (("", "buy-stop"), ("pb1_", "pull-back 1"), ("pb2_", "pull-back 2")):
        c = s[s[f"{key}status"] == "CLOSED"]
        add(f"  {label:<12} filled {len(c):>4} of {len(s)}  average {c[f'{key}net'].mean():+.2%}  win {(c[f'{key}net'] > 0).mean():.0%}  "
            f"share of trades >= +3R {(c[f'{key}R'] >= 3).mean():.1%}")

    add("\n== 7. Level and lumpiness ==")
    cl = df[st.closed]
    add(f"closed {len(cl)}: average {cl['net'].mean():+.2%}, median {cl['net'].median():+.2%}, win rate {(cl['net'] > 0).mean():.0%}, "
        f"{(cl['R'] >= 3).mean():.1%} of trades reach +3R; the best 10% of trades earn {cl['net'].nlargest(max(1, len(cl) // 10)).sum() / cl['net'].sum():.1f}x the total")
    add(f"without 2023: {cl[cl['year'] != 2023]['net'].mean():+.2%};  without 2020 and 2023: {cl[~cl['year'].isin([2020, 2023])]['net'].mean():+.2%}  "
        f"(a few strong years carry the average)")
    add("by market on the signal day (closed trades): " + ", ".join(f"{k} {v['net'].mean():+.2%} (n={len(v)})" for k, v in cl.groupby("regime")))
    add("by year: " + ", ".join(f"{y} {g['net'].mean():+.1%}" for y, g in cl.groupby("year")))
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
            f.write(f"# Spike / trend / pull-back study\n\nSource: `{os.path.basename(args.trades)}`. How to read it: see the top of `backtest/spike_study.py`.\n\n```\n"
                    + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

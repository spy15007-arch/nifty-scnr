"""
Offline study of a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, from a run made after 10 Nov 2026 so that it carries the
hold-limit columns): is the 60-session time limit selling winners too early?

WHY: the trade plan sells whatever is still open after 60 sessions, at that day's close. In the 10-year replay about one filled trade in three (1,501 of 4,457)
ended that way; those trades averaged +2.48R (the stop and trail exits together lost money) and made up 219% of the total profit - and on average they were
still rising on the day they were sold: their weekly marks over the last six weeks read +1.33R, +1.53R, +1.73R, +1.95R, +2.16R, +2.35R. The 60 was never tested
against a longer limit. This study re-runs every signal, with the very same entry, first stop (2.5 ATR) and trail (5 ATR below the highest close), under a 90, a
120, a 180 and a 250-session limit (backtest/engine.py HOLD_VARIANTS, declared BEFORE looking at any result). A trade that ends before session 60 ends the same way
under every limit, so only the trades still open at the limit can differ, and the comparison is between the SAME trades.

THE RULE (written down before any result of these columns was looked at, and committed to GitHub before the run). The limits are judged in turn, each against
the one before it: 90 against 60, then 120 against 90, then 180 against 120 (250 against 180 is shown, not judged). A step compares the trades that were filled
and sold under BOTH limits, made at least (the longer limit + 10) sessions before the file's last signal so that every one of them had the time to end by its limit
(an outcome-free cut). A step is BETTER only if ALL FOUR hold:
  1. PAYS PER TRADE: the average change in R per matched trade (every matched trade counts, the many the limit cannot touch as 0) is at least +0.05R and the lower
     end of its 98% range is above 0 (the range comes from drawing whole calendar months at random, 5,000 times, because the trades of one rally move together).
  2. PAYS FOR THE ROOM IT TAKES: the extra R earned per extra session held (the change in R added up, divided by the extra sessions added up) is above the earlier
     limit's own average R per session held on the same trades, with the lower end of the 98% range of the difference above 0. An account that can hold only a few
     stocks gives every session of a room to someone: a longer limit has to earn more per session than the average trade does, or the room is better used on the
     next signal. (The yardstick is a little strict: the real worth of a room-session is lower, because a room is also used by buy orders that are waiting.)
  3. IN EACH PART OF THE HISTORY: the average change in R is above 0 in each of 2016-19, 2020-22 and 2023-26, with at least 100 matched trades in each.
  4. THE ACCOUNTS: the two reference accounts of backtest/portfolio_study.py (1% risk, up to 8 stocks; 0.5% risk, up to 12) are run on the same signals under the
     two limits, one position per stock (a signal for a stock the account already holds is not listed), the signals made at least (the longer limit + 10) sessions
     before the end of the file only. In BOTH accounts the longer limit's growth a year must be no lower than the earlier limit's by more than the luck band, and its
     worst drawdown no deeper by more than the drawdown's luck band. A luck band is half the spread between the 10th and the 90th percentile of 30 random orders of
     the earlier limit's signals (never less than half a point of growth and 2 points of drawdown): the size of a difference that luck alone makes. (This is a veto,
     not a proof: an account's luck is wide, and rules 1 and 2 are where the evidence comes from.)
  A step whose rule 1 range lies wholly below 0 is WORSE; any other step that is not BETTER is UNCLEAR. THE ANSWER is the longest limit reached by steps that are all
  BETTER in a row (the chain stops at the first step that is not). If the first step is not BETTER, the answer is that the data does not show a longer limit paying
  (UNCLEAR) or shows it losing (WORSE), and nothing changes.
WHAT CHANGES: if a limit is reached, the scanner's time limit (config.MAX_HOLD_SESSIONS) becomes that limit for NEW picks, with the messages, the README and the tests
saying the same and the change announced in plain words; picks already on the list keep the 60 sessions stored with them in the ledger. Both scans share the plan after the
purchase and the BTST scan cannot be replayed on daily candles, so this swing replay is the evidence for both. Any other result: nothing changes.

NOT PART OF THE RULE (shown to explain the result, not to decide it): how the trades the limit changed ended (stop, trail or the new limit), the change by calendar
year, the 250-session step, and the extra R per extra session of money tied up (R per unit of money x sessions).

WHAT IT CANNOT DO: one history of ten years, from today's NIFTY 500 (survivors) - and survivorship flatters a LONG hold more than a short one, because a stock that
rose for a year or two is more likely to be in today's index than one that did not; the quality score is price-only; swing signals only (the BTST scan cannot be
replayed on daily candles); the trades of the last (limit + 10) sessions of the file are left out; and the idea was found by looking at this same data (the table of
exits by reason), so fixing the rule first makes the test honest about what it asks but does not make it independent of the first look. The only new data is what
comes next: the live record, one trade at a time.

Usage:   python -m backtest.hold_limit_study <trades.csv> [--write reports/evidence/hold_limit_study_<date>.md]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

import config
from backtest import busy_day_study as bd
from backtest import engine
from backtest import portfolio_study as ps

# the plans, the bars and the grid: declared before the run
BASE = ""                                           # the shipped plan (config.MAX_HOLD_SESSIONS = 60 sessions): its columns carry no prefix
LIMITS = {BASE: config.MAX_HOLD_SESSIONS, **{name: limit for name, limit in engine.HOLD_VARIANTS}}
STEPS = ((BASE, "hold90"), ("hold90", "hold120"), ("hold120", "hold180"))      # judged in turn, each against the one before it
SHOWN_STEPS = (("hold180", "hold250"),)                                         # shown, not judged
MIN_EDGE = 0.05                                     # rule 1: the average R a trade gains from the longer limit
ALPHA = bd.ALPHA                                    # the lower end of a range = this percentile of the resamples (1%: a 98% range)
BOOT = bd.BOOT                                      # resamples of whole months
SEED = 20261110
MIN_CELL = 100                                      # rule 3: matched trades a part of the history needs to be judged
PERIODS = bd.PERIODS
ACCOUNTS = ps.REFERENCE                             # rule 4: ((risk, most stocks), ...)
SEEDS = ps.SEEDS                                   # rule 4: random orders drawn for the luck bands
GROWTH_FLOOR = 0.005                                # rule 4: a luck band of growth is never narrower than half a point a year
DD_FLOOR = 0.02                                     # rule 4: a luck band of drawdown is never narrower than 2 points
MARGIN = 10                                         # sessions beyond the longer limit a signal must be old by the end of the file to be used
WHAT = ("status", "exit", "net", "R", "held", "exit_date", "path")
BASE_NEEDED = {"symbol", "signal_date", "fill_date", "exit_date", "status", "exit_reason", "net", "R", "held", "path_R", "risk_pct", "regime", "setup",
               "quality_pct", "sma200_chg"}


def col(name: str, what: str) -> str:
    """The trades-file column of a plan ('' = the shipped plan, whose columns carry no prefix; its exit reason is `exit_reason` and its weekly marks `path_R`)."""
    if not name:
        return {"exit": "exit_reason", "path": "path_R"}.get(what, what)
    return f"{name}_{'path_R' if what == 'path' else what}"


NEEDED = BASE_NEEDED | {col(n, w) for n, _ in engine.HOLD_VARIANTS for w in WHAT}


def label(name: str) -> str:
    return f"{LIMITS[name]} sessions"


def load(path: str) -> pd.DataFrame:
    """The trades file, checked for the columns the study needs (a file from before 10 Nov 2026 has none of the hold-limit ones)."""
    df = pd.read_csv(path)
    missing = NEEDED - set(df.columns)
    if missing:
        raise SystemExit(f"{path} lacks the columns {sorted(missing)[:6]}... - run the Backtest & Calibrate workflow (backtest, swing, data_source=yahoo) "
                         f"with the code of 10 Nov 2026 or later.")
    for c in ("signal_date", "fill_date", "exit_date") + tuple(f"{n}_exit_date" for n, _ in engine.HOLD_VARIANTS):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _days(s: pd.Series) -> np.ndarray:
    return s.to_numpy("datetime64[ns]").astype("datetime64[D]")


def cutoff(df: pd.DataFrame, limit: int):
    """The last signal date a step whose longer limit is `limit` may use: the file's last signal date minus (limit + MARGIN) sessions, so every trade of it had
    the time to end by its limit whatever happened to it."""
    end = _days(df["signal_date"]).max()
    return np.busday_offset(end, -(int(limit) + MARGIN), roll="backward")


def matched(df: pd.DataFrame, a: str, b: str) -> np.ndarray:
    """The signals a step compares: filled and sold under both plans, with a result and a number of sessions held in each, made early enough (see cutoff)."""
    ok = np.array(((df[col(a, "status")] == "CLOSED") & (df[col(b, "status")] == "CLOSED")).to_numpy(), dtype=bool)
    for what in ("R", "held"):
        for name in (a, b):
            ok = ok & np.isfinite(df[col(name, what)].to_numpy(float))
    return ok & (_days(df["signal_date"]) <= cutoff(df, LIMITS[b]))


def _ratio(num, den) -> np.ndarray:
    return bd._ratio(num, den)


class Step:
    """One longer limit `b` against the plan `a` before it, on the signals both finished. Everything is added up per calendar month so that the resamples
    (whole months drawn at random) are matrix products: `c` below holds one row per resample and one column per month (all ones = the data as it is)."""

    def __init__(self, df: pd.DataFrame, a: str, b: str, boot: int | None = None, seed: int | None = None):
        boot, seed = BOOT if boot is None else boot, SEED if seed is None else seed       # read at call time, so a test can shrink them
        self.a, self.b = a, b
        m = matched(df, a, b)
        self.mask = m
        self.n = int(m.sum())
        sold_a = np.array(((df[col(a, "status")] == "CLOSED") & (df[col(a, "exit")].astype(str) == "time")).to_numpy(), dtype=bool)
        early = _days(df["signal_date"]) <= cutoff(df, LIMITS[b])
        self.void_n = int((sold_a & early & np.array((df[col(b, "status")] == "VOID").to_numpy(), dtype=bool)).sum())     # left out: a data fault in the extra window
        sig = _days(df["signal_date"])[m]
        self.sig = sig
        self.ra, self.rb = df[col(a, "R")].to_numpy(float)[m], df[col(b, "R")].to_numpy(float)[m]
        self.ha, self.hb = df[col(a, "held")].to_numpy(float)[m], df[col(b, "held")].to_numpy(float)[m]
        self.exit_a, self.exit_b = df[col(a, "exit")].astype(str).to_numpy()[m], df[col(b, "exit")].astype(str).to_numpy()[m]
        self.year = sig.astype("datetime64[Y]").astype(int) + 1970 if self.n else np.array([], dtype=int)
        self.changed = self.exit_a == "time"                # only a trade sold by the earlier limit can differ under the longer one
        self.blocks = 0
        self.c = None
        if self.n:
            ids, self.blocks = bd.month_blocks(sig)
            self.c = bd.resamples(self.blocks, boot, seed)
            take = lambda x: np.bincount(ids, weights=x, minlength=self.blocks)           # noqa: E731
            self.cnt = np.bincount(ids, minlength=self.blocks).astype(float)
            self.d_r, self.d_h = take(self.rb - self.ra), take(self.hb - self.ha)
            self.sum_ra, self.sum_ha = take(self.ra), take(self.ha)

    # the statistics: each takes the matrix of resampling counts and returns one number per row
    def edge(self, c: np.ndarray) -> np.ndarray:
        """Rule 1: the average change in R per matched trade."""
        return _ratio(c @ self.d_r, c @ self.cnt)

    def room(self, c: np.ndarray) -> np.ndarray:
        """Rule 2: the extra R per extra session held, minus the earlier limit's own R per session held."""
        return _ratio(c @ self.d_r, c @ self.d_h) - _ratio(c @ self.sum_ra, c @ self.sum_ha)

    def estimate(self, fn) -> dict | None:
        return bd.estimate(fn, self.c, ALPHA) if self.n and self.c is not None else None

    def rates(self) -> dict:
        """The two rates rule 2 compares, on the data as it is (R per session held)."""
        extra = float((self.rb - self.ra).sum() / max((self.hb - self.ha).sum(), 1e-12)) if self.n and (self.hb - self.ha).sum() > 0 else float("nan")
        own = float(self.ra.sum() / max(self.ha.sum(), 1e-12)) if self.n else float("nan")
        return {"extra": extra, "own": own}

    def periods(self, periods=PERIODS, min_cell: int | None = None) -> list[dict]:
        min_cell = MIN_CELL if min_cell is None else min_cell                  # read at call time, so a test can change it
        rows = []
        for lo, hi in periods:
            inside = (self.year >= lo) & (self.year <= hi)
            n = int(inside.sum())
            d = float((self.rb - self.ra)[inside].mean()) if n else float("nan")
            rows.append({"label": f"{lo}-{str(hi)[2:]}", "n": n, "diff": d, "ok": bool(n >= min_cell and np.isfinite(d) and d > 0.0)})
        return rows


def _band(values, floor: float) -> float:
    """Half the spread between the 10th and the 90th percentile of the random orders' results: the size of a difference that luck alone makes."""
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if len(v) < 2:
        return floor
    return max(float((np.percentile(v, 90) - np.percentile(v, 10)) / 2.0), floor)


def plan_frame(df: pd.DataFrame, name: str, keep: np.ndarray) -> pd.DataFrame:
    """The trades file as an account wants it for the plan `name`: that plan's own status, result, sale date and weekly marks in the base columns, for the
    signals in `keep` only."""
    out = df[keep].copy()
    if name:
        out["status"], out["net"], out["R"] = out[col(name, "status")], out[col(name, "net")], out[col(name, "R")]
        out["exit_date"], out["exit_reason"], out["path_R"] = out[col(name, "exit_date")], out[col(name, "exit")], out[col(name, "path")]
    return out


def accounts(df: pd.DataFrame, a: str, b: str, seeds: int | None = None) -> list[dict]:
    """Rule 4: the reference accounts under the two limits on the same signals (finished the same way under both, made early enough), one position per stock."""
    seeds = SEEDS if seeds is None else seeds
    status_a, status_b = df[col(a, "status")], df[col(b, "status")]
    keep = np.array((((status_a == "CLOSED") & (status_b == "CLOSED")) | ((status_a == "UNFILLED") & (status_b == "UNFILLED"))).to_numpy(), dtype=bool)
    keep = keep & (_days(df["signal_date"]) <= cutoff(df, LIMITS[b]))
    books = {name: ps.Book(plan_frame(df, name, keep)) for name in (a, b)}
    rows = []
    for risk, n in ACCOUNTS:
        res = {}
        for name in (a, b):
            r = ps.simulate(books[name], "shipped", n, risk, lock_same_stock=True)
            res[name] = (r, ps.metrics(r["equity"]))
        (ra, ma), (rb, mb) = res[a], res[b]
        rand = [m for m in (ps.metrics(ps.simulate(books[a], "random", n, risk, seed=s, lock_same_stock=True)["equity"]) for s in range(seeds)) if m]
        band_g = _band([m["cagr"] for m in rand], GROWTH_FLOOR)
        band_d = _band([m["max_dd"] for m in rand], DD_FLOOR)
        ok = bool(ma and mb and mb["cagr"] >= ma["cagr"] - band_g and mb["max_dd"] >= ma["max_dd"] - band_d)
        rows.append({"risk": risk, "stocks": n, "signals": books[a].n, "a": (ra, ma), "b": (rb, mb), "band_growth": band_g, "band_dd": band_d, "ok": ok})
    return rows


def step_result(df: pd.DataFrame, a: str, b: str, boot: int | None = None, judged: bool = True, seeds: int | None = None) -> dict:
    """One step: the four rules, and its verdict (a step that is only shown has no verdict)."""
    st = Step(df, a, b, boot)
    e1 = st.estimate(st.edge)
    e2 = st.estimate(st.room)
    per = st.periods()
    acc = accounts(df, a, b, seeds) if st.n else []
    r1 = bool(e1 is not None and e1["point"] >= MIN_EDGE and e1["lo"] > 0.0)
    r2 = bool(e2 is not None and e2["lo"] > 0.0)
    r3 = bool(all(p["ok"] for p in per))
    r4 = bool(acc and all(x["ok"] for x in acc))
    if not judged:
        kind = "SHOWN"
    elif r1 and r2 and r3 and r4:
        kind = "BETTER"
    elif e1 is not None and e1["hi"] < 0.0:
        kind = "WORSE"
    else:
        kind = "UNCLEAR"
    return {"a": a, "b": b, "step": st, "e1": e1, "e2": e2, "periods": per, "accounts": acc, "rules": (r1, r2, r3, r4), "verdict": kind}


def verdict(results: list[dict]) -> dict:
    """The answer: the longest limit reached by steps that are all BETTER in a row. With none, the first step's own verdict (UNCLEAR or WORSE)."""
    reached = None
    for r in results:
        if r["verdict"] != "BETTER":
            break
        reached = r["b"]
    kind = "BETTER" if reached else (results[0]["verdict"] if results else "UNCLEAR")
    return {"kind": kind, "reached": reached, "limit": LIMITS.get(reached) if reached else None}


def _est(e: dict | None, scale: float = 1.0, nd: int = 3, sign: str = "R") -> str:
    if e is None:
        return "n/a"
    return f"{e['point'] * scale:+.{nd}f}{sign} (98% range {e['lo'] * scale:+.{nd}f}{sign} to {e['hi'] * scale:+.{nd}f}{sign})"


def result_text(v: dict, results: list[dict]) -> str:
    if v["kind"] == "BETTER":
        steps = [r["b"] for r in results]
        k = steps.index(v["reached"])
        names = ", ".join(label(b) for b in steps[:k + 1])
        text = (f"RESULT: BETTER - a longer time limit pays: {v['limit']} sessions instead of {config.MAX_HOLD_SESSIONS}. Every step up to it ({names}) met all four "
                f"rules. By the rule fixed in advance the time limit becomes {v['limit']} sessions for new picks.")
        if k + 1 < len(steps):
            text += f" The next step ({label(steps[k + 1])}) did not meet all four rules, so the chain stops here."
        return text
    if v["kind"] == "WORSE":
        return (f"RESULT: WORSE - a longer limit than {config.MAX_HOLD_SESSIONS} sessions lost money on the same trades (the whole 98% range of the first step lies below 0). "
                f"Nothing changes.")
    return (f"RESULT: UNCLEAR - the data does not show that a longer limit than {config.MAX_HOLD_SESSIONS} sessions pays (the first step did not meet all four rules). "
            f"Nothing changes.")


def exit_table(df: pd.DataFrame) -> list[str]:
    """The plan as shipped: how the filled trades ended, and what each kind of ending earned."""
    f = df[df["status"] == "CLOSED"]
    out = [f"{'ended by':<14} {'trades':>7} {'share':>7} {'average R':>10} {'median R':>9} {'share of all the profit':>24}"]
    total = float(f["R"].sum())
    for reason, g in f.groupby("exit_reason"):
        out.append(f"{reason:<14} {len(g):>7} {len(g) / max(len(f), 1):>7.0%} {g['R'].mean():>+10.2f} {g['R'].median():>+9.2f} {g['R'].sum() / total:>24.0%}")
    return out


def last_marks(df: pd.DataFrame, weeks: int = 6) -> list[str]:
    """How the trades sold by the time limit stood, week by week, up to the day they were sold (their weekly marks in R)."""
    t = df[(df["status"] == "CLOSED") & (df["exit_reason"] == "time")]
    paths = [np.array([float(x) for x in str(p).split(";") if x != ""]) for p in t["path_R"]]
    if not paths:
        return []
    k = int(np.median([len(p) for p in paths]))
    out = []
    for j in range(max(0, k - weeks), k):
        vals = np.array([p[j] for p in paths if len(p) > j])
        out.append(f"  {j * engine.PATH_STEP:>2} sessions after the purchase: {vals.mean():+.2f}R over {len(vals)} trades")
    return out


def analyse(df: pd.DataFrame, boot: int | None = None, seeds: int | None = None) -> list[str]:
    """The study as printable lines."""
    out: list[str] = []
    add = out.append
    filled = df[df["status"] == "CLOSED"]
    add(f"Signals {len(df)}, filled and sold under the shipped plan {len(filled)}, {df['signal_date'].min().date()} to {df['signal_date'].max().date()}; "
        f"time limits tried: {', '.join(str(v) for v in LIMITS.values())} sessions (same entries, same first stop, same trail, same costs)")
    add("\n== 1. The plan as shipped: how the trades end ==")
    out.extend(exit_table(df))
    marks = last_marks(df)
    if marks:
        add("The trades the 60-session limit sold, on average, in their last weeks (R after costs):")
        out.extend(marks)

    results = []
    for k, (a, b) in enumerate(STEPS, start=2):
        r = step_result(df, a, b, boot, seeds=seeds)
        results.append(r)
        st = r["step"]
        add(f"\n== {k}. {label(a)} -> {label(b)}: the trades still open at session {LIMITS[a]} are kept longer ==")
        if not st.n:
            add("no trade was filled and sold under both limits and made early enough: nothing to compare")
            continue
        add(f"matched filled trades {st.n} ({st.blocks} calendar months), signals up to {cutoff(df, LIMITS[b])}; the longer limit changed {int(st.changed.sum())} of "
            f"them ({st.changed.mean():.0%}), the ones the earlier limit had sold")
        add(f"left out of the comparison: {st.void_n} trades the earlier limit sold that the longer limit could not follow, because the data jumps more than "
            f"{engine.VOID_GAP:.0%} in one session inside the extra weeks (the replay treats that as a data fault)")
        untouched = ~st.changed
        add(f"check: of the {int(untouched.sum())} trades the earlier limit did not sell, {int((np.abs(st.rb - st.ra)[untouched] > 1e-9).sum())} have another R under the longer one (expected 0)")
        rates = st.rates()
        add(f"RULE 1 pays per trade: average change {_est(r['e1'])}; needs at least +{MIN_EDGE:.2f}R and the lower end above 0 -> {'met' if r['rules'][0] else 'NOT met'}")
        add(f"RULE 2 pays for the room it takes: the extra {rates['extra']:+.4f}R per extra session held against the earlier limit's own {rates['own']:+.4f}R per session; "
            f"difference {_est(r['e2'], nd=4)} -> {'met' if r['rules'][1] else 'NOT met'}")
        add("RULE 3 in each part of the history (average change per matched trade): " + "; ".join(
            f"{p['label']} {p['diff']:+.3f}R ({p['n']} trades)" for p in r["periods"]) + f" -> {'met' if r['rules'][2] else 'NOT met'}")
        add("RULE 4 the accounts (same signals, one position per stock; growth a year / worst drawdown / stocks held / money in stocks):")
        for x in r["accounts"]:
            (ra, ma), (rb, mb) = x["a"], x["b"]
            if not ma or not mb:
                add(f"  risk {x['risk']:.1%}, up to {x['stocks']}: too few signals")
                continue
            add(f"  risk {x['risk']:.1%}, up to {x['stocks']}: {label(a)} {ma['cagr']:+.1%} / {ma['max_dd']:+.1%} / {ra['held']:.1f} / {ra['invested']:.0%};  "
                f"{label(b)} {mb['cagr']:+.1%} / {mb['max_dd']:+.1%} / {rb['held']:.1f} / {rb['invested']:.0%}  "
                f"(growth {100 * (mb['cagr'] - ma['cagr']):+.1f} points against a luck band of {100 * x['band_growth']:.1f}; drawdown "
                f"{100 * (mb['max_dd'] - ma['max_dd']):+.1f} points against {100 * x['band_dd']:.1f}; "
                f"signals dropped because the stock was still held: {ra['same_stock']} / {rb['same_stock']}) -> {'ok' if x['ok'] else 'NOT ok'}")
        add(f"  -> {'met' if r['rules'][3] else 'NOT met'}")
        add(f"STEP: {r['verdict']}")
        ch = st.changed
        if ch.any():
            add(f"shown only - the {int(ch.sum())} trades the longer limit changed: average R {st.ra[ch].mean():+.2f} -> {st.rb[ch].mean():+.2f}; "
                f"ended by {', '.join(f'{k_} {v:.0%}' for k_, v in pd.Series(st.exit_b[ch]).value_counts(normalize=True).items())}; "
                f"held {st.ha[ch].mean():.0f} -> {st.hb[ch].mean():.0f} sessions; at +3R or more {(st.ra[ch] >= 3).mean():.0%} -> {(st.rb[ch] >= 3).mean():.0%}")
        yrs = " ".join(f"{y}:{(st.rb - st.ra)[st.year == y].mean():+.2f}" for y in sorted(set(st.year.tolist())))
        add(f"shown only - the average change by calendar year (R per matched trade): {yrs}")

    for a, b in SHOWN_STEPS:
        r = step_result(df, a, b, boot, judged=False, seeds=seeds)
        st = r["step"]
        add(f"\n== {len(STEPS) + 2}. Shown only (not judged): {label(a)} -> {label(b)} ==")
        if not st.n:
            add("nothing to compare")
            continue
        rates = st.rates()
        add(f"matched trades {st.n}, changed {int(st.changed.sum())}: average change {_est(r['e1'])}; extra {rates['extra']:+.4f}R per extra session against {rates['own']:+.4f}R per session")

    v = verdict(results)
    add(f"\n== {len(STEPS) + 3}. The answer, by the rule fixed in advance ==")
    add(result_text(v, results))
    add("\n== Read this before relying on any of it ==")
    for line in (
            "One history of ten years, not a forecast; the rule was fixed before the numbers existed, but the idea came from looking at the same data.",
            "The replay's stocks are today's NIFTY 500: survivors. A longer hold gains more than a short one from that, because a stock that kept rising for a year or two is "
            "more likely to be in the index today than one that did not - the live record, one trade at a time, is the real test of a longer limit.",
            "The score is price-only; swing signals only; no taxes and no slippage beyond the 0.35%; the trades of the last (limit + 10) sessions are left out.",
            "A longer limit ties the same money up for longer: rule 2 and the accounts of rule 4 look at that, but a real account's other uses of the money are not in them."):
        add("- " + line)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test (with the hold-limit columns)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(load(args.trades))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Hold-limit study: is the 60-session time limit selling winners too early?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"The rule and how to read it: see the top of `backtest/hold_limit_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

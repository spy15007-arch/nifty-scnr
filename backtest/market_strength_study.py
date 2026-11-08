"""
Offline study of ONE observation, on a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, with the weekly path column
`path_R`): do the filled swing trades made when the market was VERY STRONG (77% or more of the stocks above their 50-day average) earn more than the rest?

WHY (8 Nov 2026): the busy-day study (backtest/busy_day_study.py, item 11 of reports/evidence/README.md) did not find a quiet-day rule, but its explanation
table showed something else: the filled trades made when fewer than 77% of the market stood above its 50-day average (the lower two thirds of the trades) earned
+0.22R each on average, and those made when more did (the top third, 1,487 trades) +0.71R. That table was looked at after the fact, in a table of six groups, and
the cut of 77% is simply where the top third of the trades begins - so it proves nothing yet. It also comes from a few long rallies, and the trades of one rally move together:
the number of independent episodes behind it may be small. This study asks the question again with everything below fixed BEFORE any of its statistics
was calculated (the file is committed to GitHub before the first run).

THE TEST (every R is after the 0.35% round-trip cost; "filled trade" = a signal whose buy order filled and was sold; the swing replay only):
  * Breadth = `breadth50` of the signal day: the share of the liquid universe closing above its 50-day average. STRONG = breadth of STRONG_MIN (77%) or more,
    OTHER = below that. A signal without a breadth is left out.
  * The unit of luck is the calendar month of the signal date: every range below is made by drawing whole months at random with replacement (BOOT resamples,
    fixed seed) and recalculating. "The lower end" is the ALPHA (1%) percentile of those resamples; 1%, not the usual 5%, because the pattern was picked out
    of a table of six groups and the cut was read off that table.
  * RULE 1, the raw gap: the average R of the filled STRONG trades minus that of the filled OTHER trades is at least MIN_EDGE (+0.20R) and its lower end is
    above 0.
  * RULE 2, not a few years: among the calendar years with at least MIN_YEAR_CELL (30) filled trades of BOTH groups (there must be at least MIN_YEARS = 5 such
    years), STRONG beats OTHER in at least two thirds of them (rounded up), AND the gap is still at least LOYO_MIN (+0.15R) after dropping any one of those
    years (every trade of it). A pattern carried by one or two rallies fails here.
  * RULE 3, every part of the history: the gap is at least PART_MIN (+0.10R) in each of 2016-19, 2020-22 and 2023-26, each with at least MIN_CELL (100) filled
    trades of both groups; a part with fewer cannot be judged and counts as a no.
  * RULE 4, not a knife-edge: with the line drawn at 72% and at 82% instead of 77% the raw gap is still at least NEIGHBOUR_MIN (+0.10R) in both.
  * WHAT CHANGES. All four rules met: the swing message's BREADTH block gets one plain sentence on days when breadth is 77% or more (that such markets were the
    best for these picks in the replay, with the numbers). The sizing rules are NOT changed by this test: the account runs below are shown for information
    only, and a bigger size in strong markets (or a smaller one otherwise) would be the user's own decision, made on those numbers. Any rule missed: nothing
    changes, and the pattern is reported as not shown by the data. The BTST message is left alone either way (it cannot be replayed).

SHOWN FOR INFORMATION, NOT PART OF THE RULE: the two kinds of market side by side, the average R in each bucket of breadth (is it a step at 77% or a slope?),
the gap for other lines than 72/77/82%, and three accounts of the portfolio study (1.5x the risk when the market is very strong; half the risk when it is not;
only the signals made in a very strong market), each against SEEDS random accounts that flag as many signals.

WHAT IT CANNOT DO: the observation was found in the same data it is now tested on, and the 77% line was read off that data, so this is not a test on new data:
fixing the rule first makes the test honest about what it asks, not independent of the first look. The only new data is what comes next.

Usage:   python -m backtest.market_strength_study <trades.csv> [--write reports/evidence/market_strength_study_<date>.md]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

from backtest import busy_day_study as bd
from backtest import pick_cap_study as pc
from backtest import portfolio_study as ps

# the groups, the bars and the grid: declared before the run
STRONG_MIN = 0.77                       # breadth at or above this = a very strong market
MIN_EDGE = 0.20                         # rule 1: STRONG must average at least this much R more per filled trade
ALPHA = bd.ALPHA                        # the lower end of a range = this percentile of the resamples
BOOT = bd.BOOT
SEED = 20261108
MIN_YEAR_CELL = 30                      # rule 2: filled trades of EACH group a calendar year needs to be judged
MIN_YEARS = 5                           # rule 2: judgeable years needed
YEAR_SHARE = 2 / 3                      # rule 2: share of the judgeable years in which STRONG must beat OTHER (rounded up)
LOYO_MIN = 0.15                         # rule 2: the gap left after dropping any one judgeable year
PERIODS = bd.PERIODS
MIN_CELL = bd.MIN_CELL                  # rule 3: filled trades of EACH group a part of the history needs to be judged
PART_MIN = 0.10                         # rule 3: the gap each part must show
NEIGHBOURS = (0.72, 0.82)               # rule 4: the line drawn here instead
NEIGHBOUR_MIN = 0.10                    # rule 4: the gap each must still show
THRESHOLDS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.72, 0.75, 0.77, 0.80, 0.82, 0.85, 0.90)       # the gap for other lines: shown only
BUCKET_EDGES = (0.0, 0.30, 0.40, 0.50, 0.60, 0.70, 0.77, 0.85, 1.01)                          # average R by bucket of breadth: shown only
UP, DOWN = 1.5, 0.5                     # the information accounts: risk factor in a very strong market / in the rest
ACCOUNTS = ps.REFERENCE
SEEDS = ps.SEEDS
VARIANTS = (("up", f"{UP:g}x the risk in a very strong market"), ("down", f"{DOWN:g}x the risk when the market is not very strong"),
            ("only", "only the signals made in a very strong market"))


# --------------------------------------------------------------------------- #
def groups(book: ps.Book, cut: float = STRONG_MIN) -> tuple[np.ndarray, np.ndarray]:
    """(strong, other) as flags per signal: breadth at or above the cut / below it. A signal without a breadth is in neither."""
    known = np.isfinite(book.breadth)
    return known & (book.breadth >= cut), known & (book.breadth < cut)


def gap(book: ps.Book, a: np.ndarray, b: np.ndarray) -> float:
    """Average R of the filled trades of group a minus that of group b (NaN when either has none)."""
    fa, fb = book.closed & a, book.closed & b
    return bd._mean(book.R[fa]) - bd._mean(book.R[fb]) if fa.any() and fb.any() else float("nan")


def _years(book: ps.Book) -> np.ndarray:
    return book.sig.astype("datetime64[Y]").astype(int) + 1970


def year_rows(book: ps.Book, strong: np.ndarray, other: np.ndarray, min_cell: int = MIN_YEAR_CELL) -> list[dict]:
    """Rule 2: strong minus other, calendar year by calendar year (the year of the signal date)."""
    year = _years(book)
    rows = []
    for y in np.unique(year):
        inside = year == y
        s, o = book.closed & strong & inside, book.closed & other & inside
        ns, no = int(s.sum()), int(o.sum())
        ms, mo = bd._mean(book.R[s]), bd._mean(book.R[o])
        g = ms - mo if ns and no else float("nan")
        rows.append({"label": str(int(y)), "year": int(y), "n_strong": ns, "n_other": no, "mean_strong": ms, "mean_other": mo, "gap": g,
                     "judgeable": bool(ns >= min_cell and no >= min_cell), "positive": bool(np.isfinite(g) and g > 0.0)})
    return rows


def leave_one_year_out(book: ps.Book, strong: np.ndarray, other: np.ndarray, rows: list[dict]) -> list[dict]:
    """Rule 2: the raw gap on every trade of the other years, for each judgeable year dropped in turn."""
    year = _years(book)
    return [{"year": r["year"], "gap": gap(book, strong & (year != r["year"]), other & (year != r["year"]))} for r in rows if r["judgeable"]]


def years_check(rows: list[dict], loyo: list[dict], min_years: int = MIN_YEARS, share: float = YEAR_SHARE, loyo_min: float = LOYO_MIN) -> dict:
    """Rule 2 on the year rows and the leave-one-out gaps."""
    judge = [r for r in rows if r["judgeable"]]
    need = math.ceil(len(judge) * share - 1e-9)
    positive = sum(1 for r in judge if r["positive"])
    finite = [x for x in loyo if np.isfinite(x["gap"])]
    worst = min(finite, key=lambda x: x["gap"]) if finite else None
    ok = bool(len(judge) >= min_years and positive >= need and worst is not None and len(finite) == len(loyo) and worst["gap"] >= loyo_min)
    return {"judgeable": len(judge), "positive": positive, "need": need, "worst": worst["gap"] if worst else float("nan"),
            "worst_year": worst["year"] if worst else None, "ok": ok}


def part_rows(book: ps.Book, strong: np.ndarray, other: np.ndarray, periods=PERIODS, min_cell: int = MIN_CELL, min_gap: float = PART_MIN) -> list[dict]:
    """Rule 3: strong minus other by part of the history."""
    year = _years(book)
    rows = []
    for lo, hi in periods:
        inside = (year >= lo) & (year <= hi)
        s, o = book.closed & strong & inside, book.closed & other & inside
        ns, no = int(s.sum()), int(o.sum())
        ms, mo = bd._mean(book.R[s]), bd._mean(book.R[o])
        g = ms - mo if ns and no else float("nan")
        rows.append({"label": f"{lo}-{str(hi)[2:]}", "n_strong": ns, "n_other": no, "mean_strong": ms, "mean_other": mo, "gap": g,
                     "ok": bool(ns >= min_cell and no >= min_cell and np.isfinite(g) and g >= min_gap)})
    return rows


def line_rows(book: ps.Book, cuts, min_gap: float = NEIGHBOUR_MIN) -> list[dict]:
    """The raw gap with the line drawn at each of `cuts` (rule 4 uses NEIGHBOURS; a longer list is shown for information)."""
    rows = []
    for cut in cuts:
        s, o = groups(book, cut)
        g = gap(book, s, o)
        rows.append({"cut": float(cut), "n_strong": int((book.closed & s).sum()), "n_other": int((book.closed & o).sum()), "gap": g,
                     "ok": bool(np.isfinite(g) and g >= min_gap)})
    return rows


def bucket_rows(book: ps.Book, ids: np.ndarray, n: int, c: np.ndarray, edges=BUCKET_EDGES) -> list[dict]:
    """Average R of the filled trades by bucket of breadth (shown only): is it a step at the line or a slope?"""
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = np.isfinite(book.breadth) & (book.breadth >= lo) & (book.breadth < hi)
        f = mask & book.closed
        s, k = bd._sums(book, mask, ids, n)
        boot = bd._ratio(c @ s, c @ k)
        boot = boot[np.isfinite(boot)]
        rows.append({"lo": float(lo), "hi": float(min(hi, 1.0)), "days": int(len(np.unique(book.sig[mask]))), "filled": int(f.sum()), "mean_R": bd._mean(book.R[f]),
                     "se": float(boot.std(ddof=1)) if len(boot) > 1 else float("nan"), "net": bd._mean(book.net[f])})
    return rows


def raw_estimate(book: ps.Book, strong: np.ndarray, other: np.ndarray, ids: np.ndarray, n: int, c: np.ndarray) -> dict | None:
    """Rule 1: the raw gap with its standard error and the ends of its 98% range (whole months drawn at random)."""
    return bd.estimate(bd.Cells(book, strong, other, ids, n).pooled, c, ALPHA)


def edge_ok(est: dict | None) -> bool:
    """Rule 1: at least MIN_EDGE, and the lower end of the range above 0."""
    return est is not None and est["point"] >= MIN_EDGE and est["lo"] > 0.0


def verdict(t1: dict | None, years: dict, parts: list[dict], lines: list[dict]) -> dict:
    """The whole rule. `shown` = all four rules met."""
    r1, r2 = edge_ok(t1), bool(years["ok"])
    r3 = bool(parts) and all(p["ok"] for p in parts)
    r4 = bool(lines) and all(x["ok"] for x in lines)
    return {"rule1": r1, "rule2": r2, "rule3": r3, "rule4": r4, "shown": r1 and r2 and r3 and r4}


def result_text(v: dict) -> str:
    """The one-line outcome of the rule, given verdict()'s answer."""
    if v["shown"]:
        return ("RESULT: every rule is met. The pattern is shown by this data: the swing message may add one plain sentence on days when 77% or more of the market is above its "
                "50-day average. The sizing rules are not changed by this test.")
    missed = [str(i) for i, k in enumerate(("rule1", "rule2", "rule3", "rule4"), start=1) if not v[k]]
    which = "rule " + missed[0] if len(missed) == 1 else "rules " + ", ".join(missed[:-1]) + " and " + missed[-1]
    if not v["rule1"]:
        why = "The gap between very strong markets and the rest is too small, or too uncertain, to rely on."
    elif not v["rule2"]:
        why = "The gap is there over all, but it does not hold up across the years: a few of them carry it."
    elif not v["rule3"]:
        why = "The gap holds over all and across the years, but not in every part of the history."
    else:
        why = "The gap depends on exactly where the line is drawn."
    return f"RESULT: the pattern is not shown by this data ({which} not met), so nothing changes in the scanner. {why}"


def account_variants(book: ps.Book, strong: np.ndarray, seeds: int = SEEDS) -> list[dict]:
    """Information only: each reference account as shipped and with the three ways of using the market's strength, each against `seeds` random accounts
    that flag as many signals (the same risk factor on the same number of random signals)."""
    rows = []
    k_strong, k_other = int(strong.sum()), int((~strong).sum())
    plans = {"up": dict(mult=np.where(strong, UP, 1.0)), "down": dict(mult=np.where(strong, 1.0, DOWN)), "only": dict(skip=~strong)}
    for risk, n in ACCOUNTS:
        free = [ps.metrics(ps.simulate(book, "random", n, risk, seed=s)["equity"]).get("cagr", float("nan")) for s in range(seeds)]
        base_run = ps.simulate(book, "shipped", n, risk)
        row = {"risk": risk, "stocks": n, "luck": pc.luck_band(free), "base_run": base_run, "base": ps.metrics(base_run["equity"]), "variants": []}
        for key, label in VARIANTS:
            run = ps.simulate(book, "shipped", n, risk, **plans[key])
            controls = []
            for s in range(seeds):
                rng = np.random.default_rng(s)
                if key == "up":
                    mult = np.ones(book.n)
                    mult[rng.choice(book.n, size=k_strong, replace=False)] = UP
                    kwargs = dict(mult=mult)
                elif key == "down":
                    mult = np.ones(book.n)
                    mult[rng.choice(book.n, size=k_other, replace=False)] = DOWN
                    kwargs = dict(mult=mult)
                else:
                    skip = np.ones(book.n, dtype=bool)
                    skip[rng.choice(book.n, size=k_strong, replace=False)] = False
                    kwargs = dict(skip=skip)
                m = ps.metrics(ps.simulate(book, "shipped", n, risk, **kwargs)["equity"])
                if m:
                    controls.append(m)
            row["variants"].append({"key": key, "label": label, "run": run, "m": ps.metrics(run["equity"]), "controls": controls})
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
def _num(x: float, fmt: str) -> str:
    return format(x, fmt) if np.isfinite(x) else "-"


def _gap_text(est: dict | None) -> str:
    if est is None:
        return "cannot be calculated (too few trades)"
    return f"{est['point']:+.2f}R (standard error {est['se']:.2f}; {1 - 2 * ALPHA:.0%} range {est['lo']:+.2f} to {est['hi']:+.2f})"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def analyse(df: pd.DataFrame, seeds: int = SEEDS, boot: int = BOOT) -> list[str]:
    """The study as printable lines."""
    book = ps.Book(df)
    if not book.n:
        return ["No finished signals in the file."]
    if not np.isfinite(book.breadth).any():
        return ["The file has no breadth (column breadth50): run the Backtest & Calibrate workflow (backtest, swing, data_source=yahoo) and try again."]
    out: list[str] = []
    add = out.append
    years = (book.end - book.start).astype(int) / 365.25
    strong, other = groups(book)
    ids, n_months = bd.month_blocks(book.sig)
    c = bd.resamples(n_months, boot, SEED)
    cut = f"{STRONG_MIN:.0%}"
    add(f"Finished signals {book.n} ({int(book.closed.sum())} filled and sold, {int((~book.closed).sum())} orders that never filled), "
        f"{pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} ({years:.1f} years), {n_months} calendar months.")
    add(f"\nThe question: do the filled trades made when {cut} or more of the market is above its 50-day average (a VERY STRONG market) earn more than the rest?")
    add("The rule (fixed before the run; the full text is at the top of backtest/market_strength_study.py). All four parts must hold:")
    add(f"  1. strong minus other is at least {MIN_EDGE:+.2f}R per filled trade, and luck is ruled out (the lower end of the {1 - 2 * ALPHA:.0%} range is above 0);")
    add(f"  2. not a few years: strong beats other in two thirds of the calendar years that have {MIN_YEAR_CELL}+ filled trades of both (at least {MIN_YEARS} such years), "
        f"and the gap is still {LOYO_MIN:+.2f}R or more after dropping any one of them;")
    add(f"  3. the gap is {PART_MIN:+.2f}R or more in each of the parts of the history ({', '.join(f'{lo}-{str(hi)[2:]}' for lo, hi in PERIODS)}), with {MIN_CELL}+ filled trades of both in it;")
    add(f"  4. with the line drawn at {' and '.join(f'{x:.0%}' for x in NEIGHBOURS)} instead of {cut}, the gap is still {NEIGHBOUR_MIN:+.2f}R or more.")
    add(f"Ranges: whole calendar months drawn at random {boot} times (the trades of one rally move together); 'lower end' = the {ALPHA:.0%} percentile of those draws.")

    # 1 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 1. The two kinds of market ==")
    add(f"{'':<26}{'days':>7}{'signals':>9}{'filled %':>10}{'filled trades':>15}{'average R':>11}{'(+-)':>7}{'average result':>16}{'healthy / mixed / weak':>26}")
    all_days = len(np.unique(book.sig))
    for label, mask in ((f"very strong ({cut}+)", strong), (f"the rest (under {cut})", other)):
        f = mask & book.closed
        s, k = bd._sums(book, mask, ids, n_months)
        sb = bd._ratio(c @ s, c @ k)
        sb = sb[np.isfinite(sb)]
        mix = " / ".join(f"{float((book.regime[mask] == r).mean()):.0%}" for r in bd.REGIMES) if mask.any() else "-"
        add(f"{label:<26}{len(np.unique(book.sig[mask])):>7}{int(mask.sum()):>9}{(f.sum() / mask.sum() if mask.any() else float('nan')):>10.0%}{int(f.sum()):>15}"
            f"{_num(bd._mean(book.R[f]), '+.2f'):>11}{_num(float(sb.std(ddof=1)) if len(sb) > 1 else float('nan'), '.2f'):>7}"
            f"{_num(bd._mean(book.net[f]), '+.2%'):>16}{mix:>26}")
    add(f"(+-) = one standard error of the average R, from drawing whole months at random. Very strong markets were {len(np.unique(book.sig[strong])) / all_days:.0%} of the "
        f"{all_days} days with a signal.")

    # 2 ------------------------------------------------------------------------------------------------------------------------
    t1 = raw_estimate(book, strong, other, ids, n_months, c)
    add("\n== 2. Rule 1: very strong minus the rest, every filled trade ==")
    add(f"{_gap_text(t1)}   needs at least {MIN_EDGE:+.2f}R and the lower end above 0: {_yn(edge_ok(t1))}")
    if t1 is not None:
        add(f"For context only (the rule uses the {ALPHA:.0%} percentile): at the usual 5% the lower end would be {t1['lo5']:+.2f}R, and {t1['p_le0']:.0%} of the resamples "
            f"show a gap of 0 or less.")

    # 3 ------------------------------------------------------------------------------------------------------------------------
    yrows = year_rows(book, strong, other)
    loyo = leave_one_year_out(book, strong, other, yrows)
    yc = years_check(yrows, loyo)
    add("\n== 3. Rule 2: year by year ==")
    add(f"{'year':<8}{'strong trades':>14}{'other trades':>14}{'strong R':>10}{'other R':>9}{'gap':>8}{'without this year':>19}")
    lo_by_year = {x["year"]: x["gap"] for x in loyo}
    for r in yrows:
        left = f"{lo_by_year[r['year']]:+.2f}" if r["year"] in lo_by_year else "-"
        add(f"{r['label']:<8}{r['n_strong']:>14}{r['n_other']:>14}{_num(r['mean_strong'], '+.2f'):>10}{_num(r['mean_other'], '+.2f'):>9}{_num(r['gap'], '+.2f'):>8}{left:>19}"
            + ("" if r["judgeable"] else "   (under 30 trades of one kind: not judged)"))
    add(f"{yc['judgeable']} judgeable years (needs {MIN_YEARS}); strong beats other in {yc['positive']} of them (needs {yc['need']}); the smallest gap left after dropping one year is "
        f"{_num(yc['worst'], '+.2f')}R" + (f" (without {yc['worst_year']})" if yc["worst_year"] is not None else "") + f" (needs {LOYO_MIN:+.2f}R or more): {_yn(yc['ok'])}")

    # 4 ------------------------------------------------------------------------------------------------------------------------
    parts = part_rows(book, strong, other)
    add("\n== 4. Rule 3: in each part of the history ==")
    add(f"{'':<10}{'strong trades':>14}{'other trades':>14}{'strong R':>10}{'other R':>9}{'gap':>8}")
    for p in parts:
        add(f"{p['label']:<10}{p['n_strong']:>14}{p['n_other']:>14}{_num(p['mean_strong'], '+.2f'):>10}{_num(p['mean_other'], '+.2f'):>9}{_num(p['gap'], '+.2f'):>8}   "
            f"{'ok' if p['ok'] else 'not ok'}")
    add(f"needs {PART_MIN:+.2f}R or more in every part, with at least {MIN_CELL} filled trades of each kind in it.")

    # 5 ------------------------------------------------------------------------------------------------------------------------
    lines = line_rows(book, NEIGHBOURS)
    add("\n== 5. Rule 4: is the line a knife-edge? (and the gap for other lines, shown only) ==")
    add(f"{'line at':>9}{'strong trades':>15}{'other trades':>14}{'gap':>9}")
    shown = {r["cut"]: r for r in line_rows(book, sorted(set(THRESHOLDS) | {STRONG_MIN} | set(NEIGHBOURS)))}
    for cut_value in sorted(shown):
        r = shown[cut_value]
        tag = "   <- the rule" if abs(cut_value - STRONG_MIN) < 1e-9 else ("   <- rule 4" if any(abs(cut_value - x) < 1e-9 for x in NEIGHBOURS) else "")
        add(f"{cut_value:>9.0%}{r['n_strong']:>15}{r['n_other']:>14}{_num(r['gap'], '+.2f'):>9}{tag}")
    add(f"needs {NEIGHBOUR_MIN:+.2f}R or more with the line at " + " and at ".join(f"{x:.0%}" for x in NEIGHBOURS) + ": " + _yn(all(x["ok"] for x in lines)))

    # 6 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 6. Average R by bucket of breadth (shown only: a step, or a slope?) ==")
    add(f"{'breadth':<12}{'days':>7}{'filled trades':>15}{'average R':>11}{'(+-)':>7}{'average result':>16}")
    for r in bucket_rows(book, ids, n_months, c):
        add(f"{r['lo']:>4.0%} to {r['hi']:<4.0%}{r['days']:>7}{r['filled']:>15}{_num(r['mean_R'], '+.2f'):>11}{_num(r['se'], '.2f'):>7}{_num(r['net'], '+.2%'):>16}")

    # 7 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 7. What using it would have meant for an account (information only, not part of the rule) ==")
    acc = account_variants(book, strong, seeds)
    for a in acc:
        add(f"\naccount: risk {a['risk']:.1%}, up to {a['stocks']} stocks (luck band +-{a['luck'] * 100:.1f} points of growth a year)")
        add(f"{'':<50}{'growth':>9}{'worst':>11}{'filled':>9}{'average':>10}   random accounts that flag as many signals: growth a year, worst drawdown")
        add(f"{'':<50}{'a year':>9}{'drawdown':>11}{'trades':>9}{'per trade':>10}   (middle; 10th to 90th percentile of {seeds}); this one beat")
        b, br = a["base"], a["base_run"]
        if not b:
            add(f"{'as shipped':<50}{'too short a history to measure':>40}")
            continue
        add(f"{'as shipped':<50}{b['cagr']:>+9.1%}{b['max_dd']:>+11.1%}{br['closed']:>9}{br['sum_R'] / max(br['closed'], 1):>+9.2f}R")
        for v in a["variants"]:
            m, r = v["m"], v["run"]
            if not m:
                add(f"{v['label']:<50}{'too short a history to measure':>40}")
                continue
            tail = ""
            if v["controls"]:
                g = np.array([x["cagr"] for x in v["controls"]])
                d = np.array([x["max_dd"] for x in v["controls"]])
                tail = (f"   {np.median(g):+.1%} ({np.percentile(g, 10):+.1%} to {np.percentile(g, 90):+.1%}), {np.median(d):+.1%} ({np.percentile(d, 10):+.1%} to "
                        f"{np.percentile(d, 90):+.1%}); beat {float((g < m['cagr']).mean()):.0%} of them")
            add(f"{v['label']:<50}{m['cagr']:>+9.1%}{m['max_dd']:>+11.1%}{r['closed']:>9}{r['sum_R'] / max(r['closed'], 1):>+9.2f}R{tail}")
    add("These are single histories: a difference smaller than the luck band cannot be told from luck, and the accounts are path-dependent (a different size changes which "
        "later orders find a free room). They say what the pattern would have been worth if it is real; they do not make it real.")

    # 8 ------------------------------------------------------------------------------------------------------------------------
    v = verdict(t1, yc, parts, lines)
    add("\n== 8. The verdict by the rule fixed above ==")
    add(f"rule 1, raw gap: {_gap_text(t1)}: {_yn(v['rule1'])}")
    add(f"rule 2, not a few years: {yc['positive']} of {yc['judgeable']} judgeable years positive (needs {yc['need']}), smallest gap after dropping a year {_num(yc['worst'], '+.2f')}R: {_yn(v['rule2'])}")
    add("rule 3, each part of the history: " + ", ".join(f"{p['label']} {_num(p['gap'], '+.2f')}R" for p in parts) + f": {_yn(v['rule3'])}")
    add("rule 4, other lines: " + ", ".join(f"{x['cut']:.0%} {_num(x['gap'], '+.2f')}R" for x in lines) + f": {_yn(v['rule4'])}")
    add("")
    add(result_text(v))

    # 9 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 9. Read this before relying on any of it ==")
    for line in (
            "The observation was found in this same data, in a table of six groups nobody had fixed beforehand, and the 77% line is where the top third of the trades begins. The rule "
            "above was written down (and committed to GitHub) before the numbers were calculated, which makes the test honest about what it asks; it does not make it a test on new data.",
            "Very strong markets come in a few long spells (see the year table): the number of independent episodes behind the gap is far smaller than the number of trades, "
            "which is why rules 2 and 3 exist and why the ranges draw whole months.",
            "One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost.",
            "Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether a very strong market helps BTST.",
            "Breadth here is the share of the replay's universe (today's liquid NIFTY 500 stocks with enough history) above their 50-day average; the live message's breadth line is "
            "computed the same way from the live universe, but the two are not the same list of stocks."):
        add("- " + line)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test (with the weekly path column)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(ps.load(args.trades))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Market-strength study: do trades made in a very strong market earn more?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/market_strength_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

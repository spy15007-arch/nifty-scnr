"""
Offline study of ONE feeling, on a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, with the weekly path column `path_R` and
the 12-month return `ret252`): do the swing picks that had ALREADY RISEN A LOT when they were listed earn less than the others?

WHY (8 Nov 2026): looking at the charts of the stocks the scanner suggests, the user felt the suggestions come late - the stock had broken out and "a 50% move had
already taken place" when it was listed - and asked for a look. Two things can be checked. How many of the picks were already far along (counted below, for the replay
and for the live picks), and whether the far-along ones paid less (the test below). The feeling may be right or wrong on either; the scanner's quality score even gives
credit for a strong 12-month return, so a stock that has risen a lot is not an accident of the list but what it asks for. The rule below was written down BEFORE any
of its statistics was calculated (the file is committed to GitHub before the first run).

THE MEASURE. The replay stores for every signal `ret252`: the stock's gain over the last 252 sessions (about 12 months) to the close of the signal day. It is the only
column that says how far a stock had come beyond a week or so. It is NOT the gain since the breakout: a stock up 50% in 12 months may have made all of it last month
or long ago, and a stock that doubled in three months and then went sideways for nine shows a small 12-month number. So this is the nearest measure the file has, not
the user's exact observation (the exact one needs new columns in the replay); the live picks section below measures the candles directly, over several windows.

THE TEST (every R is after the 0.35% round-trip cost; "filled trade" = a signal whose buy order filled and was sold; the swing replay only):
  * EXT = a pick whose ret252 was EXT_MIN (50%) or more at the signal day's close; OTHER = a known ret252 below that. A signal with no ret252 is left out.
  * The unit of luck is the calendar month of the signal date: every range below is made by drawing whole months at random with replacement (BOOT resamples, fixed
    seed) and recalculating. A range is the ALPHA (1%) percentile at each end (a 98% range): the feeling is one-sided, but it could have come out the other way and
    both directions are judged below, each at 1%.
  * The three ways the answer can come out, fixed here:
      WORSE (EXT earned less) needs all three:
        W1. EXT minus OTHER is -MIN_EDGE (-0.10R) or less per filled trade, and the UPPER end of the range is below 0;
        W2. the same inside each calendar month (the months with at least 3 filled trades of both kinds, weighted by n_a n_b / (n_a + n_b)), so the mood of a month
            (strong months have many stocks up a lot, and pay more) cannot make it;
        W3. EXT is below OTHER in each of 2016-19, 2020-22 and 2023-26, each with at least MIN_CELL (100) filled trades of both kinds (a part with fewer cannot be
            judged and counts as a no).
      BETTER (EXT earned more, so the picks that had already run did NOT pay less) needs the mirror image: B1 at least +0.10R with the LOWER end above 0, B2 the same
        inside each month, B3 EXT above OTHER in each part.
      UNCLEAR: neither. It is then said which rules of the nearer side were missed.
  * WHAT CHANGES. WORSE: the swing cards of picks that are up 50% or more in 12 months get one plain note saying so. The ORDER of the list and the SIZE of the
    amounts are not changed by this test: the account lines below are shown for information, so the decision on sizing stays the user's. BETTER or UNCLEAR: nothing
    changes in the scanner and the result is reported as it is. The BTST scan cannot be replayed on daily candles: this study says nothing about it.

SHOWN FOR INFORMATION, NOT PART OF THE RULE: the average R by band of 12-month gain (a step at 50% or a slope?), the gap year by year and inside each setup, market
regime and third of the quality score, the gap for other lines than 50%, what an account would have done with the EXT picks at half the risk or left out (against
random halving), and the live picks measured on their own candles.

WHAT IT CANNOT DO: (1) the feeling was formed on the same market the replay covers, so this is not a test on new data; fixing the rule first makes the test honest about
what it asks, not independent of the first look. (2) The replay's universe is today's NIFTY 500: stocks that rose a lot and then collapsed or were dropped are not in it,
which flatters EXT more than OTHER - a result of "not worse" is weaker than it looks, a result of "worse" is stronger. (3) 12 months is not "since the breakout".

Usage:   python -m backtest.late_pick_study <trades.csv> [--ledger tracking/trades.csv --history data_cache/btst_history.csv.gz] [--write reports/evidence/late_pick_study_<date>.md]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from backtest import busy_day_study as bd
from backtest import portfolio_study as ps
from scanner import footprint as fp

# the groups, the bars and the grid: declared before the run
EXT_MIN = 0.50                          # EXT = a 12-month gain (ret252) of at least this at the signal day's close
MIN_EDGE = 0.10                         # rules 1: the size of the gap that counts, on either side
ALPHA = bd.ALPHA                        # each end of a range = this percentile of the resamples
BOOT = bd.BOOT
SEED = 20261108
MIN_MONTH_CELL = bd.MIN_MONTH_CELL      # rule 2: filled trades of EACH kind a month needs to be used
MIN_CELL = bd.MIN_CELL                  # rule 3: filled trades of EACH kind a part of the history needs to be judged
PERIODS = bd.PERIODS
SEEDS = bd.SEEDS
BANDS = (-9.0, 0.0, 0.25, 0.50, 0.75, 1.0, 1.5, 99.0)       # bands of the 12-month gain: shown only
BAND_NAMES = ("under 0%", "0% to 25%", "25% to 50%", "50% to 75%", "75% to 100%", "100% to 150%", "150% or more")
LINES = (0.25, 0.35, 0.50, 0.75, 1.0, 1.5)                    # the gap for other lines than EXT_MIN: shown only
MIN_YEAR_CELL = 30                      # the year-by-year table: filled trades of each kind a year needs to be judged (shown only)
LIVE_WINDOWS = ((63, "3 months"), (126, "6 months"), (252, "12 months"))
LIVE_LOW_WINDOW = 126                   # "from its low": the lowest low of the last 126 sessions (about 6 months)
LIVE_TOP = 10                           # the most extended live picks listed


# --------------------------------------------------------------------------- #
def groups(book: ps.Book, cut: float = EXT_MIN) -> tuple[np.ndarray, np.ndarray]:
    """(ext, other) as flags per signal: a 12-month gain of the cut or more / a known one below it. A signal without the number is in neither."""
    known = np.isfinite(book.ret252)
    return known & (book.ret252 >= cut), known & (book.ret252 < cut)


def _mean(x: np.ndarray) -> float:
    return bd._mean(x)


def gap(book: ps.Book, a: np.ndarray, b: np.ndarray) -> float:
    """Average R of the filled trades of group a minus that of group b (NaN when either has none)."""
    fa, fb = book.closed & a, book.closed & b
    return _mean(book.R[fa]) - _mean(book.R[fb]) if fa.any() and fb.any() else float("nan")


def worse_ok(est: dict | None) -> bool:
    """Rules W1 and W2: EXT earned at least MIN_EDGE less, and the upper end of the range is below 0."""
    return est is not None and est["point"] <= -MIN_EDGE and est["hi"] < 0.0


def better_ok(est: dict | None) -> bool:
    """Rules B1 and B2: EXT earned at least MIN_EDGE more, and the lower end of the range is above 0."""
    return est is not None and est["point"] >= MIN_EDGE and est["lo"] > 0.0


def part_rows(book: ps.Book, ext: np.ndarray, other: np.ndarray, periods=PERIODS, min_cell: int = MIN_CELL) -> list[dict]:
    """Rules W3 / B3: EXT minus OTHER by part of the history (the year of the signal date). `enough` = both kinds have min_cell filled trades."""
    year = book.sig.astype("datetime64[Y]").astype(int) + 1970
    rows = []
    for lo, hi in periods:
        inside = (year >= lo) & (year <= hi)
        e, o = book.closed & ext & inside, book.closed & other & inside
        ne, no = int(e.sum()), int(o.sum())
        me, mo = _mean(book.R[e]), _mean(book.R[o])
        g = me - mo if ne and no else float("nan")
        enough = bool(ne >= min_cell and no >= min_cell and np.isfinite(g))
        rows.append({"label": f"{lo}-{str(hi)[2:]}", "n_ext": ne, "n_other": no, "mean_ext": me, "mean_other": mo, "gap": g, "enough": enough,
                     "worse": bool(enough and g < 0.0), "better": bool(enough and g > 0.0)})
    return rows


def year_rows(book: ps.Book, ext: np.ndarray, other: np.ndarray, min_cell: int = MIN_YEAR_CELL) -> list[dict]:
    """The gap calendar year by calendar year (shown only), with the share of each year's signals that were EXT."""
    year = book.sig.astype("datetime64[Y]").astype(int) + 1970
    rows = []
    for y in np.unique(year):
        inside = year == y
        e, o = book.closed & ext & inside, book.closed & other & inside
        known = (ext | other) & inside
        ne, no = int(e.sum()), int(o.sum())
        me, mo = _mean(book.R[e]), _mean(book.R[o])
        rows.append({"label": str(int(y)), "signals": int(known.sum()), "share_ext": float((ext & inside).sum() / known.sum()) if known.any() else float("nan"),
                     "n_ext": ne, "n_other": no, "mean_ext": me, "mean_other": mo, "gap": me - mo if ne and no else float("nan"),
                     "judged": bool(ne >= min_cell and no >= min_cell)})
    return rows


def split_rows(book: ps.Book, labels: np.ndarray, names, ext: np.ndarray, other: np.ndarray) -> list[dict]:
    """EXT minus OTHER inside each class of `labels` (a setup, a market regime, a third of the score): shown only."""
    rows = []
    for name in names:
        inside = labels == name
        e, o = book.closed & ext & inside, book.closed & other & inside
        ne, no = int(e.sum()), int(o.sum())
        me, mo = _mean(book.R[e]), _mean(book.R[o])
        rows.append({"label": str(name), "n_ext": ne, "n_other": no, "mean_ext": me, "mean_other": mo, "gap": me - mo if ne and no else float("nan")})
    return rows


def thirds(values: np.ndarray, basis: np.ndarray, names=("lowest third", "middle third", "highest third")) -> tuple[np.ndarray, list[str], list[float]] | None:
    """The third of `values` each signal falls in, cut at the thirds of the `basis` ones (the filled trades). None with fewer than 30 numbers to cut at."""
    ok = basis & np.isfinite(values)
    if ok.sum() < 30:
        return None
    e1, e2 = np.percentile(values[ok], [100.0 / 3.0, 200.0 / 3.0])
    lab = np.where(~np.isfinite(values), "", np.where(values < e1, names[0], np.where(values < e2, names[1], names[2])))
    return lab, list(names), [float(e1), float(e2)]


def band_rows(book: ps.Book, ids: np.ndarray, n: int, c: np.ndarray, edges=BANDS, names=BAND_NAMES) -> list[dict]:
    """Signals, filled trades and the average R by band of the 12-month gain (shown only): a step at the line, or a slope?"""
    rows = []
    known = np.isfinite(book.ret252)
    for lo, hi, name in zip(edges[:-1], edges[1:], names):
        mask = known & (book.ret252 >= lo) & (book.ret252 < hi)
        f = mask & book.closed
        s, k = bd._sums(book, mask, ids, n)
        draws = bd._ratio(c @ s, c @ k)
        draws = draws[np.isfinite(draws)]
        rows.append({"label": name, "signals": int(mask.sum()), "filled": int(f.sum()), "mean_R": _mean(book.R[f]),
                     "se": float(draws.std(ddof=1)) if len(draws) > 1 else float("nan"), "net": _mean(book.net[f]),
                     "fresh": float(book.fresh[mask].mean()) if mask.any() else float("nan")})
    return rows


def line_rows(book: ps.Book, ids: np.ndarray, n: int, c: np.ndarray, cuts=LINES) -> list[dict]:
    """The raw gap, with its range, with the line drawn at each of `cuts` (shown only)."""
    rows = []
    for cut in cuts:
        e, o = groups(book, cut)
        est = bd.estimate(bd.Cells(book, e, o, ids, n).pooled, c, ALPHA)
        rows.append({"cut": float(cut), "n_ext": int((book.closed & e).sum()), "n_other": int((book.closed & o).sum()), "est": est})
    return rows


def verdict(t1: dict | None, t2: dict | None, parts: list[dict]) -> dict:
    """The whole rule. Each side has its three rules; `outcome` = 'worse' (all W met), 'better' (all B met) or 'unclear'."""
    has = bool(parts)
    worse = {"rule1": worse_ok(t1), "rule2": worse_ok(t2), "rule3": has and all(p["worse"] for p in parts)}
    better = {"rule1": better_ok(t1), "rule2": better_ok(t2), "rule3": has and all(p["better"] for p in parts)}
    outcome = "worse" if all(worse.values()) else ("better" if all(better.values()) else "unclear")
    return {"worse": worse, "better": better, "outcome": outcome}


def result_text(v: dict, t1: dict | None = None) -> str:
    """The one-line outcome of the rule, given verdict()'s answer (and the raw gap, to say which side the data leans to when neither is shown)."""
    pct = f"{EXT_MIN:.0%}"
    if v["outcome"] == "worse":
        return (f"RESULT: the picks that had already risen {pct} or more in 12 months earned clearly LESS than the others (every part of the worse-side rule is met): "
                f"the swing cards may carry a plain note on them. The order of the list and the size of the amounts are not changed by this test.")
    if v["outcome"] == "better":
        return (f"RESULT: the picks that had already risen {pct} or more in 12 months did NOT earn less than the others; they earned clearly MORE (every part of the "
                f"better-side rule is met). Nothing changes in the scanner. The replay is flattered by survivors, so this is no promise that late picks do as well from here.")
    if t1 is None:
        return "RESULT: no gap can be calculated (too few filled trades of one kind), so nothing changes in the scanner."
    lean = "worse" if t1["point"] < 0 else "better"
    missed = [str(i) for i, k in enumerate(("rule1", "rule2", "rule3"), start=1) if not v[lean][k]]
    which = "rule " + missed[0] if len(missed) == 1 else "rules " + ", ".join(missed[:-1]) + " and " + missed[-1]
    why = {"1": "that gap is too small, or too uncertain, to rely on.",
           "2": "inside the same calendar month it is not clear enough, so the market's mood may explain part of it.",
           "3": "it does not hold in every part of the history."}[missed[0]]
    side = "less" if lean == "worse" else "more"
    return (f"RESULT: neither side is shown by this data (the {lean}-side {which} not met), so nothing changes in the scanner. The picks that had already risen {pct} or more "
            f"earned {abs(t1['point']):.2f}R {side} per filled trade over all, but {why}")


# --------------------------------------------------------------------------- #
# the live picks, measured on their own candles (descriptive only)
def chart_measures(d: pd.DataFrame, day) -> dict | None:
    """What a stock had done by the close of `day` (or its last session before it), from its own daily candles (indexed by date, with open, high, low, close, volume): the
    gain over the last 63, 126 and 252 sessions, and the gain from the lowest low of the last LIVE_LOW_WINDOW sessions. NaN where the history is too short; None when
    the candles do not reach back to `day` at all."""
    b = fp.clean_bars(d)
    if b is None:
        return None
    pos = int(b.index.searchsorted(pd.Timestamp(day), side="right")) - 1
    if pos < 0:
        return None
    close, low = b["close"].to_numpy(), b["low"].to_numpy()
    out = {"date": b.index[pos]}
    for n, _ in LIVE_WINDOWS:
        out[f"ret{n}"] = float(close[pos] / close[pos - n] - 1.0) if pos >= n else float("nan")
    out["from_low"] = float(close[pos] / low[pos - LIVE_LOW_WINDOW + 1:pos + 1].min() - 1.0) if pos >= LIVE_LOW_WINDOW - 1 else float("nan")
    return out


def live_rows(ledger: pd.DataFrame, history: pd.DataFrame) -> list[dict]:
    """One row per pick of the live ledger that has candles in the history cache."""
    h = history.copy()
    h["date"] = pd.to_datetime(h["date"], errors="coerce")
    h = h.dropna(subset=["date"])
    bars = {sym: g.set_index("date") for sym, g in h.groupby("symbol")}
    rows = []
    for _, r in ledger.iterrows():
        day = pd.to_datetime(r.get("signal_date"), errors="coerce")
        sym = r.get("symbol")
        if pd.isna(day) or sym not in bars:
            continue
        m = chart_measures(bars[sym], day)
        if m is None:
            continue
        rows.append({"symbol": str(sym), "mode": str(r.get("scan_mode", "")), "day": pd.Timestamp(day), "setup": str(r.get("setup", "")), **m})
    return rows


def live_lines(rows: list[dict], total: int) -> list[str]:
    """The live picks section: how many were already far along, by several measures, and the most extended ones."""
    out: list[str] = []
    add = out.append
    if not rows:
        return ["No live pick could be measured (no ledger, or none of its stocks has candles in the history cache)."]
    first, last = min(r["day"] for r in rows).date(), max(r["day"] for r in rows).date()
    add(f"{len(rows)} of the {total} picks in the live ledger ({first} to {last}) have candles in the history cache and are measured on them.")
    sets = (("all picks", rows), ("swing picks", [r for r in rows if r["mode"] == "swing"]), ("BTST picks", [r for r in rows if r["mode"] == "btst"]))
    add(f"\n{'':<56}" + "".join(f"{name:>14}" for name, _ in sets))
    add(f"{'picks measured':<56}" + "".join(f"{len(rs):>14}" for _, rs in sets))
    measures = [(f"ret{n}", f"up {EXT_MIN:.0%}+ in {label}") for n, label in LIVE_WINDOWS] + [("from_low", f"up {EXT_MIN:.0%}+ from the lowest low of the last 6 months")]
    for key, label in measures:
        cells = []
        for _, rs in sets:
            have = [r[key] for r in rs if np.isfinite(r[key])]
            cells.append(f"{sum(1 for x in have if x >= EXT_MIN)} of {len(have)}" if have else "-")
        add(f"{label:<56}" + "".join(f"{x:>14}" for x in cells))
    add("")
    for key, label in (("ret63", "3-month gain"), ("ret126", "6-month gain"), ("ret252", "12-month gain"), ("from_low", "gain from the 6-month low")):
        cells = []
        for _, rs in sets:
            have = [r[key] for r in rs if np.isfinite(r[key])]
            cells.append(f"{np.median(have):+.0%}" if have else "-")
        add(f"{'median ' + label:<56}" + "".join(f"{x:>14}" for x in cells))
    ranked = sorted((r for r in rows if np.isfinite(r["from_low"])), key=lambda r: -r["from_low"])[:LIVE_TOP]
    if ranked:
        add(f"\nThe {len(ranked)} most extended live picks (by the gain from the 6-month low):")
        add(f"{'stock':<14}{'scan':<7}{'picked':<12}{'setup':<8}{'3 months':>10}{'6 months':>10}{'12 months':>11}{'from 6-mo low':>15}")
        for r in ranked:
            f = lambda x: f"{x:+.0%}" if np.isfinite(x) else "-"                       # noqa: E731
            add(f"{r['symbol']:<14}{r['mode']:<7}{str(r['day'].date()):<12}{r['setup']:<8}{f(r['ret63']):>10}{f(r['ret126']):>10}{f(r['ret252']):>11}{f(r['from_low']):>15}")
    return out


# --------------------------------------------------------------------------- #
def _num(x: float, fmt: str) -> str:
    return format(x, fmt) if np.isfinite(x) else "-"


def _gap_text(est: dict | None) -> str:
    if est is None:
        return "cannot be calculated (too few trades)"
    return f"{est['point']:+.2f}R (standard error {est['se']:.2f}; {1 - 2 * ALPHA:.0%} range {est['lo']:+.2f} to {est['hi']:+.2f})"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


SPLIT_HEAD = f"{'':<24}{'EXT trades':>11}{'other trades':>14}{'EXT R':>9}{'other R':>9}{'EXT - other':>13}"


def _split_line(name: str, r: dict) -> str:
    head = f"{name:<24}{r['n_ext']:>11}{r['n_other']:>14}"
    if not np.isfinite(r["gap"]):
        return head + f"{'-':>9}{'-':>9}{'-':>13}"
    return head + f"{r['mean_ext']:>+9.2f}{r['mean_other']:>+9.2f}{r['gap']:>+13.2f}"


def account_lines(acc: list[dict], flagged: int, seeds: int) -> list[str]:
    """Information only: each reference account as shipped, with the EXT picks at half the risk, with them left out, and with as many random signals at half the risk."""
    out: list[str] = []
    add = out.append
    for a in acc:
        add(f"\naccount: risk {a['risk']:.1%}, up to {a['stocks']} stocks (luck band +-{a['luck'] * 100:.1f} points of growth a year)")
        add(f"{'':<50}{'growth':>9}{'worst':>11}{'filled':>9}{'average':>10}")
        add(f"{'':<50}{'a year':>9}{'drawdown':>11}{'trades':>9}{'per trade':>10}")
        for key, label in (("base", "as shipped"), ("half", "EXT picks at half the risk"), ("out", "EXT picks left out")):
            r, m = a["runs"][key], a["m"][key]
            if not m:
                add(f"{label:<50}{'too short a history to measure':>40}")
                continue
            add(f"{label:<50}{m['cagr']:>+9.1%}{m['max_dd']:>+11.1%}{r['closed']:>9}{r['sum_R'] / max(r['closed'], 1):>+9.2f}R")
        if a["rand"]:
            g = np.array([x["cagr"] for x in a["rand"]])
            d = np.array([x["max_dd"] for x in a["rand"]])
            add(f"the same number of signals ({flagged}) chosen at random and taken at half the risk, {len(g)} draws: growth a year middle {np.median(g):+.1%} "
                f"(10th to 90th percentile {np.percentile(g, 10):+.1%} to {np.percentile(g, 90):+.1%}), worst drawdown middle {np.median(d):+.1%} "
                f"(deepest tenth {np.percentile(d, 10):+.1%}).")
        if a["change"] is not None:
            add(f"EXT at half the risk against the account as shipped: growth {a['change']['d_growth'] * 100:+.1f} points a year (luck band +-{a['luck'] * 100:.1f}: smaller than "
                f"that cannot be told from luck), worst drawdown {a['change']['d_dd'] * 100:+.1f} points.")
        h = a["harm"]
        if h is not None and a["m"]["half"]:
            half = a["m"]["half"]
            below = [name for name, ok in (("growth", h["ok_growth"]), ("worst drawdown", h["ok_dd"])) if not ok]
            add(f"against arbitrary halving: growth {half['cagr']:+.1%} against the 10th percentile of the random draws {h['floor_growth']:+.1%}; worst drawdown "
                f"{half['max_dd']:+.1%} against {h['floor_dd']:+.1%}  -> "
                + ("no worse than arbitrary halving" if not below else f"worse than 9 in 10 arbitrary halvings on {' and '.join(below)}"))
    add("These are single histories: a difference smaller than the luck band cannot be told from luck, and the accounts are path-dependent (a different size changes which "
        "later orders find a free room). They are information for a sizing decision, not part of the rule.")
    return out


def analyse(df: pd.DataFrame, seeds: int = SEEDS, boot: int = BOOT, live: list[str] | None = None) -> list[str]:
    """The study as printable lines. `live` = the lines of the live picks section (built by main from the ledger and the candle cache), or None to leave it out."""
    book = ps.Book(df)
    if not book.n:
        return ["No finished signals in the file."]
    if not np.isfinite(book.ret252).any():
        return ["The file has no 12-month return (column ret252): run the Backtest & Calibrate workflow (backtest, swing, data_source=yahoo) and try again."]
    out: list[str] = []
    add = out.append
    years = (book.end - book.start).astype(int) / 365.25
    ext, other = groups(book)
    ids, n_months = bd.month_blocks(book.sig)
    c = bd.resamples(n_months, boot, SEED)
    pct = f"{EXT_MIN:.0%}"
    add(f"Finished signals {book.n} ({int(book.closed.sum())} filled and sold, {int((~book.closed).sum())} orders that never filled), "
        f"{pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} ({years:.1f} years), {n_months} calendar months.")
    add(f"\nThe question: did the picks that had ALREADY RISEN {pct} OR MORE in the 12 months before the signal (EXT) earn less per filled trade than the others?")
    add("The rule (fixed before the run; the full text is at the top of backtest/late_pick_study.py). It can come out three ways:")
    add(f"  WORSE (EXT earned less) needs all of: W1 EXT minus other is {-MIN_EDGE:+.2f}R or less and the upper end of the {1 - 2 * ALPHA:.0%} range is below 0; W2 the same inside each calendar month;")
    add(f"    W3 EXT is below other in each of {', '.join(f'{lo}-{str(hi)[2:]}' for lo, hi in PERIODS)} (with {MIN_CELL}+ filled trades of both kinds in it).")
    add("  BETTER (the late ones did NOT earn less; they earned more) needs the mirror image: B1 +0.10R or more with the lower end above 0, B2 the same inside each month, B3 EXT above other in each part.")
    add("  UNCLEAR: neither. Only WORSE changes anything (a plain note on the swing cards); the order of the list and the size of the amounts are never changed by this test.")
    add(f"Ranges: whole calendar months drawn at random {boot} times (the trades of one rally move together); each end of a range = the {ALPHA:.0%} percentile of those draws.")

    # 1 ------------------------------------------------------------------------------------------------------------------------
    known = ext | other
    share = float(ext.sum() / known.sum()) if known.any() else float("nan")
    add("\n== 1. How far had the picks already come? (12-month gain at the close of the signal day) ==")
    add(f"{'12-month gain':<16}{'signals':>9}{'% of all':>10}{'fresh':>8}{'filled trades':>15}{'average R':>11}{'(+-)':>7}{'average result':>16}")
    for r in band_rows(book, ids, n_months, c):
        add(f"{r['label']:<16}{r['signals']:>9}{(r['signals'] / known.sum() if known.any() else float('nan')):>10.0%}{_num(r['fresh'], '.0%'):>8}{r['filled']:>15}"
            f"{_num(r['mean_R'], '+.2f'):>11}{_num(r['se'], '.2f'):>7}{_num(r['net'], '+.2%'):>16}")
    add("(+-) = one standard error of the average R, from drawing whole months at random; fresh = share of fresh breakouts in the band (the rest are about-to-break stocks).")
    fresh_ext = float(book.fresh[ext].mean()) if ext.any() else float("nan")
    prim_ext = float((~book.fresh)[ext].mean()) if ext.any() else float("nan")
    add(f"Up {pct} or more in 12 months: {int(ext.sum())} of {int(known.sum())} signals ({share:.0%}); {int((book.closed & ext).sum())} of the {int((book.closed & known).sum())} filled trades. "
        f"Median 12-month gain of a signal: {np.nanmedian(book.ret252[known]):+.0%}.")
    if ext.any():
        add(f"Of those, {fresh_ext:.0%} were fresh breakouts and {prim_ext:.0%} about-to-break stocks.")
    near = book.dist_hi[known][np.isfinite(book.dist_hi[known])]
    above = book.ext_pct[known][np.isfinite(book.ext_pct[known])]
    if len(near):
        add(f"Within 5% of their 52-week high on the signal day: {float(np.mean(near >= -0.05)):.0%} of the signals (the list is built from stocks near their highs). "
            + (f"Already above the prior 50-day high: {float(np.mean(above > 0)):.0%}." if len(above) else ""))

    # 2 ------------------------------------------------------------------------------------------------------------------------
    cells = bd.Cells(book, ext, other, ids, n_months)
    t1 = bd.estimate(cells.pooled, c, ALPHA)
    t2 = bd.estimate(cells.within, c, ALPHA)
    add("\n== 2. Rule 1 (W1 / B1): EXT minus the others, every filled trade ==")
    add(f"{_gap_text(t1)}")
    add(f"worse side: needs {-MIN_EDGE:+.2f}R or less and the upper end below 0: {_yn(worse_ok(t1))};   better side: needs {MIN_EDGE:+.2f}R or more and the lower end above 0: {_yn(better_ok(t1))}")
    if t1 is not None:
        add(f"For context only (the rule uses the {ALPHA:.0%} percentile): {t1['p_le0']:.0%} of the resamples show a gap of 0 or less.")

    # 3 ------------------------------------------------------------------------------------------------------------------------
    used = cells.eligible(MIN_MONTH_CELL)
    share_o = float(cells.nb[used].sum() / cells.nb.sum()) if cells.nb.sum() else float("nan")
    share_e = float(cells.na[used].sum() / cells.na.sum()) if cells.na.sum() else float("nan")
    add("\n== 3. Rule 2 (W2 / B2): EXT minus the others inside the same month ==")
    add(f"{int(used.sum())} of {n_months} months have at least {MIN_MONTH_CELL} filled trades of both kinds; they hold {share_o:.0%} of the other trades and {share_e:.0%} of the EXT ones.")
    add(f"{_gap_text(t2)}")
    add(f"worse side: {_yn(worse_ok(t2))};   better side: {_yn(better_ok(t2))}")
    if t1 is not None and t2 is not None:
        add(f"The raw gap is {t1['point']:+.2f}R and the same-month gap {t2['point']:+.2f}R. The difference, {round(t1['point'] - t2['point'], 2) + 0.0:+.2f}R, is what the month "
            f"a trade was made in (the market's mood) explains; the rest belongs to the stock's earlier gain.")

    # 4 ------------------------------------------------------------------------------------------------------------------------
    parts = part_rows(book, ext, other)
    add("\n== 4. Rule 3 (W3 / B3): in each part of the history ==")
    add(SPLIT_HEAD)
    for p in parts:
        verdict_p = "worse side ok" if p["worse"] else ("better side ok" if p["better"] else ("too few trades to judge" if not p["enough"] else "no gap"))
        add(_split_line(p["label"], p) + f"   {verdict_p}")
    add(f"needs EXT below the others in every part (worse) or above them in every part (better), with at least {MIN_CELL} filled trades of each kind in it.")

    # 5 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 5. Year by year (not part of the rule) ==")
    add(f"{'year':<8}{'signals':>9}{'% EXT':>8}{'EXT trades':>12}{'other trades':>14}{'EXT R':>9}{'other R':>9}{'EXT - other':>13}")
    for r in year_rows(book, ext, other):
        add(f"{r['label']:<8}{r['signals']:>9}{_num(r['share_ext'], '.0%'):>8}{r['n_ext']:>12}{r['n_other']:>14}{_num(r['mean_ext'], '+.2f'):>9}{_num(r['mean_other'], '+.2f'):>9}"
            f"{_num(r['gap'], '+.2f'):>13}" + ("" if r["judged"] else f"   (under {MIN_YEAR_CELL} trades of one kind)"))

    # 6 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 6. What could explain it (not part of the rule): the same gap inside each kind of setup, market regime and third of the quality score ==")
    add(SPLIT_HEAD)
    for r in split_rows(book, np.where(book.fresh, "fresh breakout", "about to break"), ("fresh breakout", "about to break"), ext, other):
        add(_split_line(r["label"], r))
    for r in split_rows(book, book.regime, bd.REGIMES, ext, other):
        add(_split_line(r["label"] + " market", r))
    q3 = thirds(book.q, book.closed)
    if q3 is not None:
        lab, names, edges = q3
        for r in split_rows(book, lab, names, ext, other):
            add(_split_line("score, " + r["label"], r))
        add(f"(thirds of the quality score cut at {edges[0]:.0f} and {edges[1]:.0f}; the score gives credit for a strong 12-month return, so EXT picks score higher)")

    # 7 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 7. Would another line do the same? (not part of the rule) ==")
    add(f"{'line at':>9}{'EXT trades':>12}{'other trades':>14}{'EXT - other':>13}{'lower end':>11}{'upper end':>11}")
    for r in line_rows(book, ids, n_months, c, sorted(set(LINES) | {EXT_MIN})):
        e = r["est"]
        tail = f"{e['point']:>+13.2f}{e['lo']:>+11.2f}{e['hi']:>+11.2f}" if e else f"{'-':>13}{'-':>11}{'-':>11}"
        add(f"{r['cut']:>9.0%}{r['n_ext']:>12}{r['n_other']:>14}{tail}" + ("   <- the rule" if abs(r["cut"] - EXT_MIN) < 1e-9 else ""))

    # 8 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 8. What taking the EXT picks at half the risk would have meant for an account (information only) ==")
    acc = bd.account_test(book, ext, seeds)
    for line in account_lines(acc, int(ext.sum()), seeds):
        add(line)

    # 9 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 9. The live picks, measured on their own candles (descriptive only; not part of the rule) ==")
    for line in (live if live is not None else ["(no ledger or candle cache was given)"]):
        add(line)

    # 10 -----------------------------------------------------------------------------------------------------------------------
    v = verdict(t1, t2, parts)
    add("\n== 10. The verdict by the rule fixed above ==")
    add(f"rule 1, raw gap: {_gap_text(t1)}: worse side {_yn(v['worse']['rule1'])}, better side {_yn(v['better']['rule1'])}")
    add(f"rule 2, same-month gap: {_gap_text(t2)}: worse side {_yn(v['worse']['rule2'])}, better side {_yn(v['better']['rule2'])}")
    add("rule 3, each part of the history: " + ", ".join(f"{p['label']} {_num(p['gap'], '+.2f')}R" + ("" if p["enough"] else " (too few)") for p in parts)
        + f": worse side {_yn(v['worse']['rule3'])}, better side {_yn(v['better']['rule3'])}")
    add("")
    add(result_text(v, t1))

    # 11 -----------------------------------------------------------------------------------------------------------------------
    add("\n== 11. Read this before relying on any of it ==")
    for line in (
            "The feeling was formed on the same market the replay covers. The rule above was written down (and committed to GitHub) before the numbers were calculated, which makes "
            "the test honest about what it asks; it does not make it a test on new data. The only new data is what comes next.",
            "The replay's stocks are today's NIFTY 500. A stock that rose a lot and then collapsed or was dropped from the index is not in it, which flatters the picks that had "
            "already risen a lot more than the others: a result of 'not worse' is weaker than it looks, a result of 'worse' is stronger.",
            "12 months is not 'since the breakout'. A stock up 50% in a year may have made it last month or long ago; the replay's file holds no gain-since-the-base column, so the "
            "exact thing seen on a chart is not measured here (section 9 measures it for the live picks, over several windows).",
            "One history, not a forecast; the price-only score, no taxes and no slippage beyond the 0.35% cost.",
            "Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether late BTST picks do worse.",
            "The intervals draw whole calendar months: they allow for the trades of one rally moving together, not for anything longer than a month."):
        add("- " + line)
    return out


def _live_section(ledger_path: str, history_path: str) -> list[str] | None:
    """The live picks section from the files, or None when either is missing."""
    if not (ledger_path and history_path and os.path.exists(ledger_path) and os.path.exists(history_path)):
        return None
    ledger = pd.read_csv(ledger_path)
    history = pd.read_csv(history_path)
    return live_lines(live_rows(ledger, history), len(ledger))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test (with the weekly path column and ret252)")
    ap.add_argument("--ledger", default="tracking/trades.csv", help="the live ledger of picks (for the live picks section; skipped when missing)")
    ap.add_argument("--history", default="data_cache/btst_history.csv.gz", help="the candle cache of the BTST scan (for the live picks section; skipped when missing)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(ps.load(args.trades), live=_live_section(args.ledger, args.history))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Late-pick study: do picks that had already risen a lot earn less?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/late_pick_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

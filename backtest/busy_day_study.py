"""
Offline study of ONE observation, on a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, with the weekly path column
`path_R`): do the filled trades of a BUSY day (many new swing signals) earn more than those of a QUIET day (few)?

WHY (7 Nov 2026): the pick-cap study (backtest/pick_cap_study.py, section 4) grouped the filled trades by how many signals their day had and saw a rise:
+0.22R on days with one signal, +0.56R on days with nine or more. Nobody had fixed that table beforehand, the trades of one day move together and so do
the days of one rally, so it proved nothing - and the obvious explanation is the market's mood: many stocks break out on the same day when the market is
strong, and stocks bought in a strong market earn more whatever the count of the day was. If the day's count carried news of its own, a quiet day would
call for a smaller bet. This study asks the question again, with everything below fixed BEFORE any of its statistics was calculated.

THE TEST (every R is after the 0.35% round-trip cost; "filled trade" = a signal whose buy order filled and was sold; the swing replay only):
  * Day size = the number of signals the file has for the signal date, whatever became of them (the number a scan sees that evening). QUIET = 1 or 2
    signals (QUIET_MAX), BUSY = 4 or more (BUSY_MIN). A day of 3 (the middle day of the file) sits between and is only shown. The cut is at the middle
    day, not read off the table.
  * The unit of luck is the calendar month of the signal date: the trades of one day, of one rally and of one holding period move together. Every interval
    below is made by drawing whole months at random with replacement (BOOT resamples, fixed seed) and recalculating. "The lower end" is the ALPHA percentile
    of those resamples. ALPHA is 1%, not the usual 5%, because the pattern was picked out of a table of six day-size groups: a correction for having looked.
  * RULE 1, the raw difference: the average R of the filled BUSY-day trades minus that of the filled QUIET-day trades is at least MIN_EDGE (+0.10R) and its
    lower end is above 0.
  * RULE 2, the same-month difference: the same difference taken inside each month (the months with at least MIN_MONTH_CELL filled trades of both kinds) and
    averaged over those months with the weights n_busy x n_quiet / (n_busy + n_quiet): at least +0.10R, lower end above 0. The busy and the quiet days of
    one month met the same market, so a month-long mood cannot make this one (it can make rule 1).
  * RULE 3, every part of the history: the raw difference is above 0 in each of PERIODS (2016-19, 2020-22, 2023-26), each with at least MIN_CELL filled
    trades of both kinds; a part with fewer cannot be judged and counts as a no.
  * RULE 4, no harm to an account (it only matters when rules 1-3 hold): the two reference accounts of backtest/portfolio_study.py are run again with every
    QUIET-day signal at HALF the usual risk, and compared with SEEDS (30) accounts that halve the same NUMBER of randomly chosen signals instead. (Halving any
    signals costs a little growth and moves the drawdown about, because the money it frees lets other orders in; the random draws show how much, luck
    included.) In BOTH accounts the growth a year and the worst drawdown of the quiet-day account must each be no worse than the FLOOR_PCT (10th) percentile
    of those draws: halving the quiet days may not be clearly more costly than halving arbitrary signals. This is a veto, not a proof: an account cannot show a
    gain from this (halving trades that earn something on average only lowers what they earn, a little) and its luck is wide. Shown for information only: the
    change against the unchanged account (with the luck band of the pick-cap study) and the same accounts with the quiet-day signals left out.
  * WHAT CHANGES. All four rules met: the swing message calls a day with 1 or 2 new picks a quiet day and asks for half the usual amount on them (the capital
    plan on the dashboard says the same); the BTST message is left alone, since it cannot be replayed. Any rule missed: nothing changes in the scanner, and
    the pattern is reported as not shown by the data.

NOT PART OF THE RULE (shown to explain the result, not to decide it): how the three kinds of day differ in the mix of their signals and in the market around
them, the difference inside each market regime and each third of breadth, and whether other cut-offs of quiet and busy give the same answer.

WHAT IT CANNOT DO: the observation was found in the same data it is now tested on, so this is not a test on new data. The rules were written down before the
new numbers were calculated (and the file was committed to GitHub before the first run), which makes the test honest about what it asks; it does not make
it independent of the first look. The only new data is what comes next, and the BTST scan cannot be replayed on daily candles.

Usage:   python -m backtest.busy_day_study <trades.csv> [--write reports/evidence/busy_day_study_<date>.md]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

from backtest import pick_cap_study as pc
from backtest import portfolio_study as ps

# the groups, the bars and the grid: declared before the run
QUIET_MAX = 2                         # a day with this many new signals or fewer is QUIET
BUSY_MIN = 4                          # a day with this many or more is BUSY (the middle day of the file has 3)
MIN_EDGE = 0.10                       # busy must average at least this much R more per filled trade (rules 1 and 2)
ALPHA = 0.01                          # the lower end of an interval = this percentile of the resamples (1%: a correction for having looked at six groups)
BOOT = 5000                           # resamples of whole months
SEED = 20261107
MIN_MONTH_CELL = 3                    # rule 2: filled trades of EACH kind a month needs to be used
MIN_CELL = 100                        # rule 3: filled trades of EACH kind a part of the history needs to be judged
PERIODS = ((2016, 2019), (2020, 2022), (2023, 2026))
ACCOUNTS = ps.REFERENCE               # rule 4: ((risk, most stocks), ...)
SEEDS = ps.SEEDS                      # rule 4: random draws of the same number of signals at half the risk (and random orders for the luck band)
HALF = 0.5                            # rule 4: the risk taken on a quiet-day signal, as a share of the usual
FLOOR_PCT = 10.0                      # rule 4: the quiet-day account may not be worse than this percentile of the random draws
SPLITS = ((1, 4), (2, 4), (2, 6), (2, 9), (3, 4), (3, 6))          # (quiet up to, busy from): other cut-offs, shown only
REGIMES = ("healthy", "mixed", "weak")
QUIET_LABEL = "1" if QUIET_MAX == 1 else f"1-{QUIET_MAX}"


# --------------------------------------------------------------------------- #
def day_sizes(df: pd.DataFrame, book: ps.Book) -> np.ndarray:
    """For every signal of the book: how many signals the file has for its date, whatever became of them (the number a scan sees that evening)."""
    per_date = df["signal_date"].dt.normalize().value_counts()
    sizes = per_date.reindex(pd.DatetimeIndex(book.sig)).to_numpy(dtype=float)
    return np.nan_to_num(sizes, nan=0.0).astype(int)


def groups(size: np.ndarray, quiet_max: int = QUIET_MAX, busy_min: int = BUSY_MIN) -> tuple[np.ndarray, np.ndarray]:
    """(quiet, busy) as flags per signal; a day in between belongs to neither."""
    size = np.asarray(size)
    return size <= quiet_max, size >= busy_min


def month_blocks(sig: np.ndarray) -> tuple[np.ndarray, int]:
    """The calendar month of each signal date as a block number 0..n-1, and the number of blocks."""
    months, ids = np.unique(sig.astype("datetime64[M]"), return_inverse=True)
    return np.asarray(ids).reshape(-1), len(months)


def resamples(n_blocks: int, boot: int = BOOT, seed: int = SEED) -> np.ndarray:
    """How often each block is drawn in each of `boot` resamples of the blocks (n_blocks draws with replacement per resample)."""
    rng = np.random.default_rng(seed)
    return rng.multinomial(n_blocks, np.full(n_blocks, 1.0 / n_blocks), size=boot).astype(float)


def _ratio(num, den) -> np.ndarray:
    num, den = np.asarray(num, dtype=float), np.asarray(den, dtype=float)
    out = np.full(np.broadcast(num, den).shape, np.nan)
    np.divide(num, den, out=out, where=den > 0)
    return out


def _sums(book: ps.Book, mask: np.ndarray, ids: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Per block: the R of the filled trades of the group added up, and how many there are."""
    m = book.closed & mask
    return np.bincount(ids[m], weights=book.R[m], minlength=n), np.bincount(ids[m], minlength=n).astype(float)


class Cells:
    """The filled trades of two groups (a = the busy ones, b = the quiet ones) added up per calendar month. `c` below is a matrix of resampling counts, one row
    per resample and one column per month (all ones = the data as it is); every method returns one number per row."""

    def __init__(self, book: ps.Book, a: np.ndarray, b: np.ndarray, ids: np.ndarray, n: int):
        self.n = n
        self.sa, self.na = _sums(book, a, ids, n)
        self.sb, self.nb = _sums(book, b, ids, n)

    def mean_a(self, c: np.ndarray) -> np.ndarray:
        return _ratio(c @ self.sa, c @ self.na)

    def mean_b(self, c: np.ndarray) -> np.ndarray:
        return _ratio(c @ self.sb, c @ self.nb)

    def pooled(self, c: np.ndarray) -> np.ndarray:
        """Rule 1: a's average R minus b's, over every filled trade of the months drawn."""
        return self.mean_a(c) - self.mean_b(c)

    def eligible(self, min_cell: int = MIN_MONTH_CELL) -> np.ndarray:
        return (self.na >= min_cell) & (self.nb >= min_cell)

    def within(self, c: np.ndarray, min_cell: int = MIN_MONTH_CELL) -> np.ndarray:
        """Rule 2: the same difference inside each month that has min_cell trades of both kinds, averaged with the weights n_a n_b / (n_a + n_b)."""
        ok = self.eligible(min_cell)
        w = np.where(ok, self.na * self.nb / np.maximum(self.na + self.nb, 1.0), 0.0)
        d = np.where(ok, _ratio(self.sa, self.na) - _ratio(self.sb, self.nb), 0.0)
        return _ratio(c @ (w * d), c @ w)


def estimate(fn, c: np.ndarray, alpha: float = ALPHA) -> dict | None:
    """The statistic fn(c) on the data as it is (one row of ones), with its standard error and the ends of the (1 - 2 alpha) range of the resamples c.
    None when it cannot be calculated."""
    point = float(fn(np.ones((1, c.shape[1])))[0])
    draws = np.asarray(fn(c), dtype=float)
    draws = draws[np.isfinite(draws)]
    if not np.isfinite(point) or len(draws) < 2:
        return None
    lo, hi, lo5 = np.percentile(draws, [100.0 * alpha, 100.0 * (1.0 - alpha), 5.0])
    return {"point": point, "se": float(draws.std(ddof=1)), "lo": float(lo), "hi": float(hi), "draws": int(len(draws)),
            "lo5": float(lo5), "p_le0": float((draws <= 0.0).mean())}          # lo5 and p_le0: for context only, the rule uses `lo`


def edge_ok(est: dict | None) -> bool:
    """Rules 1 and 2: at least MIN_EDGE, and the lower end of the range above 0."""
    return est is not None and est["point"] >= MIN_EDGE and est["lo"] > 0.0


def _mean(x: np.ndarray) -> float:
    return float(np.mean(x)) if len(x) else float("nan")


def period_rows(book: ps.Book, quiet: np.ndarray, busy: np.ndarray, periods=PERIODS, min_cell: int = MIN_CELL) -> list[dict]:
    """Rule 3: busy minus quiet, by part of the history (the year of the signal date)."""
    year = book.sig.astype("datetime64[Y]").astype(int) + 1970
    rows = []
    for lo, hi in periods:
        inside = (year >= lo) & (year <= hi)
        q, b = book.closed & quiet & inside, book.closed & busy & inside
        nq, nb = int(q.sum()), int(b.sum())
        mq, mb = _mean(book.R[q]), _mean(book.R[b])
        diff = mb - mq if nq and nb else float("nan")
        rows.append({"label": f"{lo}-{str(hi)[2:]}", "n_quiet": nq, "n_busy": nb, "mean_quiet": mq, "mean_busy": mb, "diff": diff,
                     "ok": bool(nq >= min_cell and nb >= min_cell and np.isfinite(diff) and diff > 0.0)})
    return rows


def split_rows(book: ps.Book, labels: np.ndarray, names, quiet: np.ndarray, busy: np.ndarray) -> list[dict]:
    """Busy minus quiet inside each class of `labels` (a market regime, a third of breadth): descriptive, not part of the rule. Also the average R of ALL the
    filled trades of the class, whatever kind of day they came on."""
    rows = []
    for name in names:
        inside = labels == name
        q, b = book.closed & quiet & inside, book.closed & busy & inside
        nq, nb = int(q.sum()), int(b.sum())
        mq, mb = _mean(book.R[q]), _mean(book.R[b])
        every = book.closed & inside
        rows.append({"label": str(name), "n_quiet": nq, "n_busy": nb, "mean_quiet": mq, "mean_busy": mb, "diff": mb - mq if nq and nb else float("nan"),
                     "n_all": int(every.sum()), "mean_all": _mean(book.R[every])})
    return rows


def breadth_thirds(book: ps.Book) -> tuple[np.ndarray, list[str], list[float]] | None:
    """Each signal's third of breadth (share of the market above its 50-day average on the signal day), cut at the thirds of the filled trades. None
    when the file has no breadth."""
    b = book.breadth
    ok = book.closed & np.isfinite(b)
    if ok.sum() < 30:
        return None
    e1, e2 = np.percentile(b[ok], [100.0 / 3.0, 200.0 / 3.0])
    names = ["lowest third", "middle third", "highest third"]
    lab = np.where(~np.isfinite(b), "", np.where(b < e1, names[0], np.where(b < e2, names[1], names[2])))
    return lab, names, [float(e1), float(e2)]


def kinds(book: ps.Book, size: np.ndarray, ids: np.ndarray, n: int, c: np.ndarray) -> list[dict]:
    """The three kinds of day side by side: how many, how often the orders filled, what the filled trades earned, and the mix of their signals."""
    quiet, busy = groups(size)
    typical = ~quiet & ~busy
    middle = str(QUIET_MAX + 1) if BUSY_MIN - 1 <= QUIET_MAX + 1 else f"{QUIET_MAX + 1}-{BUSY_MIN - 1}"
    names = {"quiet": f"quiet ({QUIET_LABEL})", "typical": f"typical ({middle})", "busy": f"busy ({BUSY_MIN}+)"}
    rows = []
    for key, mask in (("quiet", quiet), ("typical", typical), ("busy", busy)):
        if not mask.any():
            continue
        f = mask & book.closed
        s, k = _sums(book, mask, ids, n)
        boot = _ratio(c @ s, c @ k)
        boot = boot[np.isfinite(boot)]
        rows.append({
            "label": names[key], "days": int(len(np.unique(book.sig[mask]))), "signals": int(mask.sum()), "filled": int(f.sum()),
            "share": float(f.sum() / mask.sum()), "mean_R": _mean(book.R[f]), "se": float(boot.std(ddof=1)) if len(boot) > 1 else float("nan"),
            "net": _mean(book.net[f]), "mix": [float((book.regime[mask] == r).mean()) for r in REGIMES], "fresh": float(book.fresh[mask].mean()),
            "quality": float(book.q[mask].mean()), "breadth": float(np.nanmean(book.breadth[mask])) if np.isfinite(book.breadth[mask]).any() else float("nan"),
            "stop": float(book.rf[mask].mean())})
    return rows


def no_harm(half: dict, rand: list[dict], pct: float = FLOOR_PCT) -> dict | None:
    """Rule 4 for one account. `half` = metrics of the account with the quiet-day signals at half the risk, `rand` = metrics of the accounts that halve the same
    number of random signals. Its growth a year and its worst drawdown must each be no worse than the `pct`-th percentile of the random ones. None when
    there is nothing to compare."""
    if not half or len(rand) < 2:
        return None
    floor_growth = float(np.percentile([x["cagr"] for x in rand], pct))
    floor_dd = float(np.percentile([x["max_dd"] for x in rand], pct))            # drawdowns are negative: the 10th percentile is the deep end
    ok_growth, ok_dd = bool(half["cagr"] >= floor_growth), bool(half["max_dd"] >= floor_dd)
    return {"floor_growth": floor_growth, "floor_dd": floor_dd, "ok_growth": ok_growth, "ok_dd": ok_dd, "ok": ok_growth and ok_dd}


def account_test(book: ps.Book, quiet: np.ndarray, seeds: int = SEEDS) -> list[dict]:
    """Rule 4 and its information lines: each reference account as shipped, with the quiet-day signals at half the risk, with them left out, and with the
    same number of random signals at half the risk (the comparison of the rule)."""
    rows = []
    k = int(quiet.sum())
    half = np.where(quiet, HALF, 1.0)
    for risk, n in ACCOUNTS:
        free = [ps.metrics(ps.simulate(book, "random", n, risk, seed=s)["equity"]).get("cagr", float("nan")) for s in range(seeds)]
        luck = pc.luck_band(free)
        runs = {"base": ps.simulate(book, "shipped", n, risk), "half": ps.simulate(book, "shipped", n, risk, mult=half),
                "out": ps.simulate(book, "shipped", n, risk, skip=quiet)}
        m = {key: ps.metrics(r["equity"]) for key, r in runs.items()}
        rnd = []
        for s in range(seeds):
            mult = np.ones(book.n)
            mult[np.random.default_rng(s).choice(book.n, size=k, replace=False)] = HALF
            rnd.append(ps.metrics(ps.simulate(book, "shipped", n, risk, mult=mult)["equity"]))
        rnd = [x for x in rnd if x]
        harm = no_harm(m["half"], rnd)
        change = ({"d_growth": float(m["half"]["cagr"] - m["base"]["cagr"]), "d_dd": float(m["half"]["max_dd"] - m["base"]["max_dd"])}
                  if m["base"] and m["half"] else None)
        rows.append({"risk": risk, "stocks": n, "luck": luck, "runs": runs, "m": m, "rand": rnd, "harm": harm, "change": change,
                     "ok": bool(harm is not None and harm["ok"])})
    return rows


def verdict(t1: dict | None, t2: dict | None, periods: list[dict], accounts: list[dict]) -> dict:
    """The whole rule. `supported` = rules 1-3 (the pattern is shown by this data); `adopt` = all four rules."""
    r1, r2 = edge_ok(t1), edge_ok(t2)
    r3 = bool(periods) and all(p["ok"] for p in periods)
    r4 = bool(accounts) and all(a["ok"] for a in accounts)
    return {"rule1": r1, "rule2": r2, "rule3": r3, "rule4": r4, "supported": r1 and r2 and r3, "adopt": r1 and r2 and r3 and r4}


def result_text(v: dict) -> str:
    """The one-line outcome of the rule, given verdict()'s answer."""
    if v["adopt"]:
        return (f"RESULT: every rule is met. The pattern is shown by this data, and an account that takes the quiet days at half the risk comes to no harm: the swing "
                f"message may call a day with {QUIET_LABEL} new picks quiet and ask for half the usual amount on them.")
    if v["supported"]:
        return "RESULT: rules 1-3 are met (the pattern is shown by this data) but rule 4 is not: nothing changes in the scanner."
    missed = [str(i) for i, k in enumerate(("rule1", "rule2", "rule3"), start=1) if not v[k]]
    which = "rule " + missed[0] if len(missed) == 1 else "rules " + ", ".join(missed[:-1]) + " and " + missed[-1]
    if not v["rule1"]:
        why = "The gap between busy and quiet days is too small, or too uncertain, to rely on."
    elif not v["rule2"]:
        why = "Busy days paid more over all, but not clearly more than the quiet days of the same month: the market's mood, not the day's count, explains the gap."
    else:
        why = "Busy days paid more over all and inside the same month, but not in every part of the history."
    return f"RESULT: the pattern is not shown by this data ({which} not met), so nothing changes in the scanner. {why}"


# --------------------------------------------------------------------------- #
def _edge_text(est: dict | None) -> str:
    if est is None:
        return "cannot be calculated (too few trades)"
    return f"{est['point']:+.2f}R (standard error {est['se']:.2f}; {1 - 2 * ALPHA:.0%} range {est['lo']:+.2f} to {est['hi']:+.2f})"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def _row(name: str, row: dict) -> str:
    """One line of a busy-minus-quiet table: the two counts, the two averages and their difference ('-' where one side has no filled trade)."""
    head = f"{name:<22}{row['n_quiet']:>13}{row['n_busy']:>12}"
    if not np.isfinite(row["diff"]):
        return head + f"{'-':>11}{'-':>10}{'-':>13}"
    return head + f"{row['mean_quiet']:>+11.2f}{row['mean_busy']:>+10.2f}{row['diff']:>+13.2f}"


def _row_all(name: str, row: dict) -> str:
    """The same line with the average R of all filled trades of the class (any kind of day) added."""
    return _row(name, row) + (f"{row['n_all']:>13}{row['mean_all']:>+10.2f}" if row["n_all"] else f"{0:>13}{'-':>10}")


SPLIT_HEAD = f"{'':<22}{'quiet trades':>13}{'busy trades':>12}{'quiet R':>11}{'busy R':>10}{'busy - quiet':>13}"
SPLIT_HEAD_ALL = SPLIT_HEAD + f"{'all trades':>13}{'all R':>10}"


def analyse(df: pd.DataFrame, seeds: int = SEEDS, boot: int = BOOT) -> list[str]:
    """The study as printable lines."""
    book = ps.Book(df)
    if not book.n:
        return ["No finished signals in the file."]
    out: list[str] = []
    add = out.append
    years = (book.end - book.start).astype(int) / 365.25
    size = day_sizes(df, book)
    quiet, busy = groups(size)
    ids, n_months = month_blocks(book.sig)
    c = resamples(n_months, boot)
    q_label = QUIET_LABEL
    add(f"Finished signals {book.n} ({int(book.closed.sum())} filled and sold, {int((~book.closed).sum())} orders that never filled), "
        f"{pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} ({years:.1f} years), {n_months} calendar months.")
    add(f"\nThe question: do the filled trades of BUSY days ({BUSY_MIN} or more new signals) earn more than those of QUIET days ({q_label})? A day in between is only shown.")
    add("The rule (fixed before the run; the full text is at the top of backtest/busy_day_study.py). All four parts must hold:")
    add(f"  1. busy minus quiet is at least {MIN_EDGE:+.2f}R per filled trade, and luck is ruled out (the lower end of the {1 - 2 * ALPHA:.0%} range is above 0);")
    add("  2. the same, taken inside each calendar month (so the mood of a month cannot make it);")
    add(f"  3. busy beats quiet in each of the parts of the history ({', '.join(f'{lo}-{str(hi)[2:]}' for lo, hi in PERIODS)}), with at least {MIN_CELL} filled trades of each kind in it;")
    add(f"  4. an account that takes the quiet-day signals at half the risk is no worse (growth a year, worst drawdown) than the {FLOOR_PCT:.0f}th percentile of {seeds} accounts "
        f"that halve as many random signals.")
    add(f"Ranges: whole calendar months drawn at random {boot} times (the trades of one rally move together); 'lower end' = the {ALPHA:.0%} percentile of those draws.")

    # 1 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 1. The three kinds of day ==")
    krows = kinds(book, size, ids, n_months, c)
    add(f"{'kind of day':<16}{'days':>7}{'signals':>9}{'filled %':>10}{'filled trades':>15}{'average R':>11}{'(+-)':>7}{'average result':>16}")
    for g in krows:
        add(f"{g['label']:<16}{g['days']:>7}{g['signals']:>9}{g['share']:>10.0%}{g['filled']:>15}{g['mean_R']:>+11.2f}{g['se']:>7.2f}{g['net']:>+16.2%}")
    add("(+-) = one standard error of the average R, from drawing whole months at random.")
    add(f"\nWhat the signals of each kind of day were like:\n{'kind of day':<16}{'healthy / mixed / weak market':>32}{'fresh':>8}{'quality':>9}{'breadth':>9}{'stop':>7}")
    for g in krows:
        mix = " / ".join(f"{x:.0%}" for x in g["mix"])
        add(f"{g['label']:<16}{mix:>32}{g['fresh']:>8.0%}{g['quality']:>9.0f}{g['breadth']:>9.0%}{g['stop']:>7.1%}")
    add("fresh = share of fresh breakouts (the rest are about-to-break stocks); quality = average quality score; breadth = share of the market above its 50-day average on the "
        "signal day; stop = average distance to the first stop.")

    cells = Cells(book, busy, quiet, ids, n_months)
    t1 = estimate(cells.pooled, c)
    t2 = estimate(cells.within, c)

    # 2 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 2. Rule 1: busy minus quiet, every filled trade ==")
    add(f"{_edge_text(t1)}   needs at least {MIN_EDGE:+.2f}R and the lower end above 0: {_yn(edge_ok(t1))}")
    add("This mixes two things: the day's count, and the market the day happened in (a strong month has busy days and pays more).")
    if t1 is not None:
        add(f"For context only (the rule uses the {ALPHA:.0%} percentile): at the usual 5% the lower end would be {t1['lo5']:+.2f}R, and {t1['p_le0']:.0%} of the resamples "
            f"show a gap of 0 or less.")

    # 3 ------------------------------------------------------------------------------------------------------------------------
    used = cells.eligible()
    share_q = float(cells.nb[used].sum() / cells.nb.sum()) if cells.nb.sum() else float("nan")
    share_b = float(cells.na[used].sum() / cells.na.sum()) if cells.na.sum() else float("nan")
    add("\n== 3. Rule 2: busy minus quiet inside the same month ==")
    add(f"{int(used.sum())} of {n_months} months have at least {MIN_MONTH_CELL} filled trades of both kinds; they hold {share_q:.0%} of the quiet-day trades and {share_b:.0%} of the busy-day ones.")
    add(f"{_edge_text(t2)}   needs at least {MIN_EDGE:+.2f}R and the lower end above 0: {_yn(edge_ok(t2))}")
    if t2 is not None:
        add(f"For context only: at the usual 5% the lower end would be {t2['lo5']:+.2f}R, and {t2['p_le0']:.0%} of the resamples show a gap of 0 or less.")
    if t1 is not None and t2 is not None:
        add(f"The raw gap is {t1['point']:+.2f}R and the same-month gap {t2['point']:+.2f}R. The difference, {t1['point'] - t2['point']:+.2f}R, is what the month a day fell in "
            f"(the market's mood) explains; the rest belongs to the day itself.")

    # 4 ------------------------------------------------------------------------------------------------------------------------
    per = period_rows(book, quiet, busy)
    add("\n== 4. Rule 3: in each part of the history (raw difference) ==")
    add(SPLIT_HEAD)
    for p in per:
        add(_row(p["label"], p) + f"   {'ok' if p['ok'] else 'not ok'}")
    add(f"needs busy above quiet in every part, with at least {MIN_CELL} filled trades of each kind in it.")

    # 5 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 5. What could explain it (not part of the rule): the same difference inside each market regime and each third of breadth ==")
    add(SPLIT_HEAD_ALL)
    for r in split_rows(book, book.regime, REGIMES, quiet, busy):
        add(_row_all(r["label"] + " market", r))
    thirds = breadth_thirds(book)
    if thirds is not None:
        lab, names, edges = thirds
        for r in split_rows(book, lab, names, quiet, busy):
            add(_row_all("breadth, " + r["label"], r))
        add(f"(thirds of breadth cut at {edges[0]:.0%} and {edges[1]:.0%} of the market above its 50-day average; 'all trades' = every filled trade of the class, "
            f"whatever kind of day it came on)")

    # 6 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 6. Would another cut-off do the same? (not part of the rule) ==")
    add(f"{'quiet is up to':<16}{'busy is from':>14}{'quiet trades':>14}{'busy trades':>13}{'rule 1':>10}{'lower end':>11}{'rule 2':>10}{'lower end':>11}")
    for qm, bm in SPLITS:
        qq, bb = groups(size, qm, bm)
        cc = Cells(book, bb, qq, ids, n_months)
        a, b = estimate(cc.pooled, c), estimate(cc.within, c)
        nq, nb = int((book.closed & qq).sum()), int((book.closed & bb).sum())
        fa = f"{a['point']:>+10.2f}{a['lo']:>+11.2f}" if a else f"{'-':>10}{'-':>11}"
        fb = f"{b['point']:>+10.2f}{b['lo']:>+11.2f}" if b else f"{'-':>10}{'-':>11}"
        add(f"{qm:<16}{bm:>14}{nq:>14}{nb:>13}{fa}{fb}" + ("   <- the rule" if (qm, bm) == (QUIET_MAX, BUSY_MIN) else ""))

    # 7 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 7. Rule 4: the two accounts with the quiet-day signals at half the risk ==")
    acc = account_test(book, quiet, seeds)
    for a in acc:
        add(f"\naccount: risk {a['risk']:.1%}, up to {a['stocks']} stocks")
        add(f"{'':<46}{'growth':>9}{'worst':>11}{'filled':>9}{'average':>10}")
        add(f"{'':<46}{'a year':>9}{'drawdown':>11}{'trades':>9}{'per trade':>10}")
        for key, label in (("base", "as shipped"), ("half", "quiet days at half the risk (the rule)"), ("out", "quiet days left out (information only)")):
            r, m = a["runs"][key], a["m"][key]
            if not m:
                add(f"{label:<46}{'too short a history to measure':>40}")
                continue
            add(f"{label:<46}{m['cagr']:>+9.1%}{m['max_dd']:>+11.1%}{r['closed']:>9}{r['sum_R'] / max(r['closed'], 1):>+9.2f}R")
        if a["rand"]:
            g = np.array([x["cagr"] for x in a["rand"]])
            d = np.array([x["max_dd"] for x in a["rand"]])
            add(f"the same number of signals ({int(quiet.sum())}) chosen at random and taken at half the risk, {len(g)} draws: growth a year middle {np.median(g):+.1%} "
                f"(10th to 90th percentile {np.percentile(g, 10):+.1%} to {np.percentile(g, 90):+.1%}), worst drawdown middle {np.median(d):+.1%} "
                f"(deepest tenth {np.percentile(d, 10):+.1%}).")
        if a["change"] is not None:
            add(f"against the account as shipped (information): growth {a['change']['d_growth'] * 100:+.1f} points a year (luck band +-{a['luck'] * 100:.1f}: smaller than that "
                f"cannot be told from luck), worst drawdown {a['change']['d_dd'] * 100:+.1f} points.")
        h = a["harm"]
        if h is not None:
            half = a["m"]["half"]
            add(f"the rule: growth {half['cagr']:+.1%} against the {FLOOR_PCT:.0f}th percentile of the random draws {h['floor_growth']:+.1%}: {_yn(h['ok_growth'])}; "
                f"worst drawdown {half['max_dd']:+.1%} against {h['floor_dd']:+.1%}: {_yn(h['ok_dd'])}  -> {'no harm' if h['ok'] else 'HARM'}")
        else:
            add("the rule: cannot be measured (too short a history): no")
    add("Halving trades that earn something on average can only lower what an account earns, so the account cannot show a gain here; and its luck is wide. "
        "What the rule asks is that halving the QUIET days is not clearly more costly than halving arbitrary signals.")

    # 8 ------------------------------------------------------------------------------------------------------------------------
    v = verdict(t1, t2, per, acc)
    add("\n== 8. The verdict by the rule fixed above ==")
    add(f"rule 1, raw difference: {_edge_text(t1)}: {_yn(v['rule1'])}")
    add(f"rule 2, same-month difference: {_edge_text(t2)}: {_yn(v['rule2'])}")
    add("rule 3, each part of the history: " + ", ".join(f"{p['label']} {p['diff']:+.2f}R" if np.isfinite(p["diff"]) else f"{p['label']} n/a" for p in per) + f": {_yn(v['rule3'])}")
    for a in acc:
        h, half = a["harm"], a["m"]["half"]
        add(f"rule 4, risk {a['risk']:.1%}, up to {a['stocks']}: " + (f"growth {half['cagr']:+.1%} (floor {h['floor_growth']:+.1%}), worst drawdown {half['max_dd']:+.1%} "
                                                                    f"(floor {h['floor_dd']:+.1%}): {_yn(a['ok'])}" if h is not None else "cannot be measured: no"))
    add("")
    add(result_text(v))

    # 9 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 9. Read this before relying on any of it ==")
    for line in (
            "The observation was found in this same data, in a table nobody had fixed beforehand. The rule above was written down (and committed to GitHub) before the new numbers "
            "were calculated, which makes the test honest about what it asks; it does not make it a test on new data. The only new data is what comes next.",
            "One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost.",
            "Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether a quiet or a busy BTST day differs.",
            "Day size here is every signal the replay made that day. The live evening message only lists the NEW picks (those BTST had not already put on the list), so it "
            "would have to count the whole day's signals, not the ones it prints.",
            "The intervals draw whole calendar months: they allow for the trades of one rally moving together, not for anything longer than a month.",
            "After the first run the printout gained a few descriptive lines: the share of resamples at or below 0 and the 5% lower end in sections 2 and 3, and the 'all trades' "
            "columns in section 5. They show how close the calls were and what the market's mood does by itself; the rule, its numbers and its verdict were not touched "
            "(the first run was made with the code of the GitHub commit before this one)."):
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
            f.write(f"# Busy-day study: do busy days pay more than quiet ones, beyond the market's mood?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/busy_day_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

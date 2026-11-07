"""
Offline study of a DAILY CAP on the number of new picks of the swing scan, on a Yahoo back-test trades file
(reports/output/backtest_swing_yahoo_<date>_trades.csv, with the weekly path column `path_R`).

WHY (6 Nov 2026): the list a reader is meant to follow has grown to about 4.5 times what one account can hold (backtest/portfolio_study.py, the capital plan
on the dashboard), and the scan lists up to 15 new picks a day (config.MAX_PICKS). A smaller cap on the picks of one day would shorten the list. That is a
rule change, so it is replayed first: does a cap lose money, does it earn more, and does it make the list holdable?

THE TEST (fixed here, before any result was looked at):
  * The signals of the file are what the scan listed with the shipped limit (15 a day for the swing scan, the same cool-down rule). A cap of K keeps the first
    K signals of each day in the scanner's own order (rank_key: 💪 strong uptrend, fresh breakouts before about-to-break stocks, then quality) and drops the
    rest. Caps tried: CAPS. The list as shipped is the comparison.
  * Each capped list is run through the two reference accounts of backtest/portfolio_study.py (1% risk, up to 8 stocks; 0.5% risk, up to 12 stocks) and
    compared with the same account on the list as shipped. As a check on luck, each cap is also drawn at random (K signals of each day picked at random,
    30 times): that is what the cap would be if the scanner's order carried no information.
  * THE RULE. A cap of K is adopted for the swing scan only if ALL of these hold:
      1. in BOTH accounts its growth a year is higher than the shipped list's by more than that account's luck band (half the spread between the 10th and the
         90th percentile of 30 random orders of the shipped list, never under half a point) - a smaller difference cannot be told from luck;
      2. in BOTH accounts its worst drawdown is not more than 2 points deeper;
      3. the picks it drops (ranked below K) earned less per filled trade than the ones it keeps, by at least 2 standard errors.
    If several caps pass, the largest one (the mildest change). If none passes the list stays as it is: a cap that only shortens the list while earning the
    same would be a matter of taste, not of evidence, and it would slow down the live record (30 sold trades are needed before anyone can judge the scanner).
  * It also states what each cap does to the size of the list (if every listed signal were bought: stocks open at once, share of the capital needed),
    because making the list holdable is the reason to ask.

WHAT IT CANNOT TEST: the BTST scan (its picks are made on the afternoon price, which daily candles do not have; the snapshot log of backtest/snapshot_study.py is
the only way to look at it, and it is days old). A cap on the signals that were already listed is also not a full re-run: a stock dropped by the cap on one
day could have been listed again the next, and the cool-down would then have worked differently. Those second-round effects are left out.

Usage:   python -m backtest.pick_cap_study <trades.csv> [--write reports/evidence/pick_cap_study_<date>.md]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

import config
from backtest import portfolio_study as ps

# the grid and the rule, declared before the run
CAPS = (1, 2, 3, 5, 8)                # most new picks a day; "as shipped" = config.MAX_PICKS["swing"] (None below)
ACCOUNTS = ps.REFERENCE               # ((risk, most stocks), ...): the two accounts every cap is run through
SEEDS = ps.SEEDS                      # random draws for the luck check
LUCK_FLOOR = 0.005                    # the luck band is never narrower than half a point of growth a year
DRAWDOWN_ALLOWED = 0.02               # a capped account may not draw down more than this much deeper
Z_NEEDED = 2.0                        # the dropped picks must do worse than the kept ones by this many standard errors
RANK_GROUPS = ((1, 1), (2, 2), (3, 3), (4, 5), (6, 8), (9, 10 ** 6))
SHIPPED = "as shipped"


def _cap_label(cap: int | None) -> str:
    return SHIPPED if cap is None else str(cap)


def _rank_label(lo: int, hi: int) -> str:
    if hi >= 10 ** 6:
        return f"{lo}th or lower"
    if lo == hi:
        return {1: "1st", 2: "2nd", 3: "3rd"}.get(lo, f"{lo}th")
    return f"{lo}th-{hi}th"


# --------------------------------------------------------------------------- #
def per_day(book: ps.Book) -> np.ndarray:
    """How many signals each day had."""
    return np.array([len(i) for i in book.by_day], dtype=int)


def luck_band(random_growth, floor: float = LUCK_FLOOR) -> float:
    """Half the spread between the 10th and the 90th percentile of the growth a year of the random orders: the size of a difference that luck alone makes."""
    g = np.asarray([x for x in random_growth if np.isfinite(x)], dtype=float)
    if len(g) < 2:
        return floor
    return max(float((np.percentile(g, 90) - np.percentile(g, 10)) / 2.0), floor)


def judge(base: dict, cur: dict, luck: float) -> dict:
    """One account, one cap against the list as shipped: the change in growth and in drawdown, and what the rule makes of them (rules 1 and 2)."""
    dg = float(cur["cagr"] - base["cagr"])
    dd = float(cur["max_dd"] - base["max_dd"])                # positive = a shallower drawdown
    word = "better" if dg > luck else ("worse" if dg < -luck else "same")
    return {"d_growth": dg, "d_dd": dd, "luck": float(luck), "growth": word, "better": dg > luck, "safe": dd >= -DRAWDOWN_ALLOWED}


def kept_vs_dropped(book: ps.Book, ranks: np.ndarray, k: int) -> dict | None:
    """Average R of the filled trades the cap keeps (ranked 1..k of their day) against the ones it drops, and how many standard errors apart they are
    (rule 3). None when either side has fewer than two filled trades. Trades of the same day move together, so the true uncertainty is larger."""
    m = book.closed
    kept, dropped = book.R[m & (ranks <= k)], book.R[m & (ranks > k)]
    if len(kept) < 2 or len(dropped) < 2:
        return None
    se = math.sqrt(float(kept.var(ddof=1)) / len(kept) + float(dropped.var(ddof=1)) / len(dropped))
    z = float((kept.mean() - dropped.mean()) / se) if se > 0 else float("nan")
    return {"n_kept": int(len(kept)), "mean_kept": float(kept.mean()), "n_dropped": int(len(dropped)), "mean_dropped": float(dropped.mean()), "z": z,
            "ok": bool(np.isfinite(z) and z >= Z_NEEDED)}


def passes(judged: list[dict], rank: dict | None) -> bool:
    """The whole rule for one cap: better in every account, no deeper a drawdown in any, and the dropped picks clearly worse than the kept ones."""
    return bool(judged) and all(j["better"] and j["safe"] for j in judged) and rank is not None and bool(rank["ok"])


def decide(passed: dict[int, bool]) -> int | None:
    """The cap to adopt: the largest of those that pass (the mildest change); None when none does."""
    ok = [k for k, v in passed.items() if v]
    return max(ok) if ok else None


# --------------------------------------------------------------------------- #
def rank_table(book: ps.Book, ranks: np.ndarray) -> list[dict]:
    """The filled trades by their place within their day (the scanner's order): how many, how often they filled, what they earned."""
    rows = []
    for lo, hi in RANK_GROUPS:
        inside = (ranks >= lo) & (ranks <= hi)
        f = inside & book.closed
        n = int(f.sum())
        if not inside.any():
            continue
        r = book.R[f]
        se = float(r.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
        rows.append({"label": _rank_label(lo, hi), "signals": int(inside.sum()), "filled": n, "share": n / int(inside.sum()),
                     "mean_R": float(r.mean()) if n else float("nan"), "se": se, "net": float(book.net[f].mean()) if n else float("nan")})
    return rows


def day_size_table(book: ps.Book) -> list[dict]:
    """The same filled trades grouped by how many signals their DAY had (a quiet day or a busy one). The places of section 4 mix this up: a 9th place can only
    exist on a day with nine or more signals."""
    size = np.zeros(book.n, dtype=int)
    for idx in book.by_day:
        size[idx] = len(idx)
    counts = per_day(book)
    rows = []
    for lo, hi in RANK_GROUPS:
        inside = (size >= lo) & (size <= hi)
        f = inside & book.closed
        n = int(f.sum())
        if not inside.any():
            continue
        r = book.R[f]
        label = ("1 signal" if lo == hi == 1 else f"{lo} signals" if lo == hi else f"{lo} or more" if hi >= 10 ** 6 else f"{lo}-{hi} signals")
        rows.append({"label": label, "days": int(((counts >= lo) & (counts <= hi)).sum()), "signals": int(inside.sum()), "filled": n,
                     "mean_R": float(r.mean()) if n else float("nan"), "se": float(r.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan"),
                     "net": float(book.net[f].mean()) if n else float("nan")})
    return rows


def evaluate(book: ps.Book, seeds: int = SEEDS) -> dict:
    """Run every cap through every reference account: the shipped order, and the same cap drawn at random. {account: {cap: {...}}, 'luck': {account: band}}"""
    out: dict = {"luck": {}, "acc": {}}
    for risk, n in ACCOUNTS:
        random_free = [ps.metrics(ps.simulate(book, "random", n, risk, seed=s)["equity"]).get("cagr", float("nan")) for s in range(seeds)]
        out["luck"][(risk, n)] = luck_band(random_free)
        rows = {}
        for cap in (None,) + CAPS[::-1]:
            r = ps.simulate(book, "shipped", n, risk, cap=cap)
            m = ps.metrics(r["equity"])
            rnd = np.array([ps.metrics(ps.simulate(book, "random", n, risk, seed=s, cap=cap)["equity"]).get("cagr", float("nan")) for s in range(seeds)])
            rows[cap] = {"r": r, "m": m, "rand": rnd, "beat": float((rnd < m["cagr"]).mean()) if len(rnd) else float("nan")}
        out["acc"][(risk, n)] = rows
    return out


# --------------------------------------------------------------------------- #
HEAD = (f"{'cap a day':<12}{'growth':>9}{'worst':>11}{'orders':>9}{'filled':>8}{'average':>10}{'100':>8}   {'random picks of that size: growth a year':<42}{'shipped':>8}\n"
        f"{'':<12}{'a year':>9}{'drawdown':>11}{'a year':>9}{'trades':>8}{'per trade':>10}{'became':>8}   {'middle (10th to 90th percentile of 30 draws)':<42}{'beat':>8}")


def _account_row(cap, row: dict, years: float) -> str:
    r, m, rnd = row["r"], row["m"], row["rand"]
    avg = r["sum_R"] / max(r["closed"], 1)
    lo, mid, hi = (np.percentile(rnd, 10), np.median(rnd), np.percentile(rnd, 90)) if len(rnd) else (float("nan"),) * 3
    rand = f"{mid:+.1%} ({lo:+.1%} to {hi:+.1%})"
    return (f"{_cap_label(cap):<12}{m['cagr']:>+9.1%}{m['max_dd']:>+11.1%}{r['placed'] / years:>9.0f}{r['closed']:>8}{avg:>+9.2f}R{m['total'] + 1:>7.1f}x"
            f"   {rand:<42}{row['beat']:>8.0%}")


def analyse(df: pd.DataFrame, seeds: int = SEEDS) -> list[str]:
    """The study as printable lines."""
    book = ps.Book(df)
    if not book.n:
        return ["No finished signals in the file."]
    out: list[str] = []
    add = out.append
    years = (book.end - book.start).astype(int) / 365.25
    shipped_cap = config.MAX_PICKS.get("swing")
    add(f"Finished signals {book.n} ({int(book.closed.sum())} filled and sold, {int((~book.closed).sum())} orders that never filled), "
        f"{pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} ({years:.1f} years). The list as shipped: up to {shipped_cap} new picks a day.")
    add("\nThe test (fixed before the run): keep the first K signals of each day in the scanner's order, drop the rest, and run the two reference accounts of the "
        "portfolio study over what is left. The rule that decides is in section 5.")

    # 1 ------------------------------------------------------------------------------------------------------------------------
    counts = per_day(book)
    add("\n== 1. How many signals a day, and what a cap takes away ==")
    add(f"{len(counts)} days with a signal; signals a day: average {counts.mean():.2f}, middle {np.median(counts):.0f}, most {int(counts.max())}")
    add(f"{'cap a day':<12}{'days it bites':>15}{'signals dropped':>17}{'signals a day after':>21}")
    for cap in (None,) + CAPS[::-1]:
        if cap is None:
            add(f"{SHIPPED:<12}{'-':>15}{'-':>17}{counts.mean():>21.2f}")
            continue
        add(f"{cap:<12}{float((counts > cap).mean()):>15.0%}{float(np.maximum(counts - cap, 0).sum() / counts.sum()):>17.0%}{float(np.minimum(counts, cap).mean()):>21.2f}")

    # 2 ------------------------------------------------------------------------------------------------------------------------
    ev = evaluate(book, seeds)
    add("\n== 2. What the two accounts would have earned with each cap (the scanner's order; 'random picks' = K signals of each day drawn at random) ==")
    for (risk, n), rows in ev["acc"].items():
        add(f"\naccount: risk {risk:.1%}, up to {n} stocks (luck band: +-{ev['luck'][(risk, n)] * 100:.1f} points of growth a year)")
        add(HEAD)
        for cap, row in rows.items():
            add(_account_row(cap, row, years))
    add("worst drawdown is from the highest weekly value to the lowest after it; a cap only changes what an account does on days it would have had a free room for "
        "a signal beyond the K-th - an account that is usually full barely notices it.")

    # 3 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 3. Would the whole list become holdable? (every listed signal bought, at the message's sizing) ==")
    add(f"{'cap a day':<12}{'stocks open at once: average':>30}{'90th percentile':>17}{'peak':>6}{'money needed: average':>24}{'90th percentile':>17}")
    for cap in (None,) + CAPS[::-1]:
        c = ps.concurrency(book, ps.issued_mask(book, cap))
        if not c:
            add(f"{_cap_label(cap):<12}{'nothing filled':>30}")
            continue
        add(f"{_cap_label(cap):<12}{c['avg']:>30.0f}{c['p90']:>17.0f}{c['peak']:>6}{c['money_avg']:>24.0%}{c['money_p90']:>17.0%}")
    add("100% = all of the capital. A list that needs more than that cannot be held whole by anyone; a cap that leaves it far above 100% shortens the list "
        "but does not make it holdable - the limit that does is the number of stocks one holds (the capital plan on the dashboard).")

    # 4 ------------------------------------------------------------------------------------------------------------------------
    ranks = ps.day_ranks(book)
    add("\n== 4. Are the first picks of a day better than the later ones? (filled trades, after costs) ==")
    add(f"{'place in its day':<18}{'signals':>9}{'filled':>8}{'filled %':>10}{'average R':>11}{'(+-)':>8}{'average result':>16}")
    for g in rank_table(book, ranks):
        add(f"{g['label']:<18}{g['signals']:>9}{g['filled']:>8}{g['share']:>10.0%}{g['mean_R']:>+11.2f}{g['se']:>8.2f}{g['net']:>+16.2%}")
    add("(+-) = one standard error of the average R; trades of the same day move together, so the real uncertainty is larger than it shows.")
    add("\nThe same trades by how many signals their DAY had (a 9th place can only exist on a busy day, so the table above mixes the place with the kind of day):")
    sizes = day_size_table(book)
    add(f"{'signals that day':<18}{'days':>7}{'signals':>9}{'filled':>8}{'average R':>11}{'(+-)':>8}{'average result':>16}")
    for g in sizes:
        add(f"{g['label']:<18}{g['days']:>7}{g['signals']:>9}{g['filled']:>8}{g['mean_R']:>+11.2f}{g['se']:>8.2f}{g['net']:>+16.2%}")
    if len(sizes) >= 2 and np.isfinite(sizes[0]["mean_R"]) and np.isfinite(sizes[-1]["mean_R"]):
        add(f"On days with {sizes[0]['label']} a filled trade averaged {sizes[0]['mean_R']:+.2f}R, on days with {sizes[-1]['label']} {sizes[-1]['mean_R']:+.2f}R. "
            f"This was found after looking at the data, in a table nobody fixed beforehand, and the trades of one day move together (there are only "
            f"{sizes[-1]['days']} days in the last row): nothing uses it, and it would need its own test, fixed in advance, before anything did.")

    # 5 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 5. The verdict by the rule fixed above ==")
    passed: dict[int, bool] = {}
    for k in CAPS:
        add(f"\ncap {k} a day")
        judged = []
        for (risk, n), rows in ev["acc"].items():
            j = judge(rows[None]["m"], rows[k]["m"], ev["luck"][(risk, n)])
            judged.append(j)
            add(f"  risk {risk:.1%}, up to {n}: growth {j['d_growth'] * 100:+.1f} points a year (luck band +-{j['luck'] * 100:.1f}): {j['growth']}; "
                f"drawdown {j['d_dd'] * 100:+.1f} points: {'ok' if j['safe'] else 'deeper than allowed'}")
        kd = kept_vs_dropped(book, ranks, k)
        if kd is None:
            add("  the picks it drops against the ones it keeps: too few trades on one side to compare")
        else:
            side = "above" if kd["z"] >= 0 else "below"
            add(f"  the picks it drops average {kd['mean_dropped']:+.2f}R ({kd['n_dropped']} trades), the ones it keeps {kd['mean_kept']:+.2f}R "
                f"({kd['n_kept']} trades): the kept ones are {abs(kd['z']):.1f} standard errors {side} the dropped ones "
                f"(the rule needs them {Z_NEEDED:g} or more above): {'yes' if kd['ok'] else 'no'}")
        passed[k] = passes(judged, kd)
        add(f"  -> {'PASSES' if passed[k] else 'does not pass'}")
    chosen = decide(passed)
    add("")
    if chosen is None:
        add(f"RESULT: no cap passes. The data cannot show that a cap earns more, so the swing list stays at up to {shipped_cap} new picks a day.")
    else:
        add(f"RESULT: a cap of {chosen} a day passes every part of the rule (the largest that does).")

    # 6 ------------------------------------------------------------------------------------------------------------------------
    add("\n== 6. Read this before relying on any of it ==")
    for line in (
            "One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost: the levels "
            "are flattered, the comparison between caps less so.",
            "Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list (its cap is 8 a day): nothing here says "
            "what a cap on BTST would do.",
            "The cap is applied to the signals that were listed anyway; a stock dropped by the cap could have been listed again on a later day and the "
            "cool-down would then have worked differently. A full re-run is not needed unless a cap passes.",
            "Differences smaller than the luck band are noise: which signals came on which days changes an account's yearly growth by about that much.",
            "A cap also slows the live record: fewer picks a day means fewer sold trades to judge the scanner by."):
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
            f.write(f"# Pick-cap study: would fewer new picks a day have earned more, or made the list holdable?\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/pick_cap_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

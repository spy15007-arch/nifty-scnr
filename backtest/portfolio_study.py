"""
Offline study of a Yahoo back-test trades file (reports/output/backtest_swing_yahoo_<date>_trades.csv, from a run made after 4 Nov 2026 so that it
carries the weekly path column `path_R`): what would ONE ACCOUNT, with a fixed amount of capital that can hold only a few stocks, have earned?

WHY: every other study compares groups of signals and reports an average per signal. That average describes someone who buys EVERY signal - in the
10-year replay that is about 58 stocks open at once (peak 174) and several times the capital at the 1%-risk sizing the messages recommend. Nobody
can do that, and a person who holds 10 stocks at a time meets the lumpiness of the results much harder: a few big winners pay for many small losses
(about 1 trade in 8 reaches +3R), so holding fewer stocks can miss them. This study replays the SAME signals as an account.

THE ACCOUNT (the rules are fixed here, before any result was looked at; every number below is after the 0.35% round-trip cost):
  * Start with 100. Each day's new signals are looked at in the scanner's order of preference (rank_key: 💪 strong uptrend, then fresh breakouts
    before about-to-break stocks, then quality), as one would work down the evening message.
  * A signal becomes an order if BOTH rooms exist: fewer than `max_stocks` stocks are bought-or-waiting, and the money committed to bought-or-waiting
    stocks plus this one stays within the account's realised equity (no borrowing). Otherwise it is skipped. An order frees its room when its stock
    is sold, or when the buy order expires unfilled (5 sessions).
  * Size = the message's rule: risk `risk` of the account's realised equity from the buy price to the first stop (half of that in a weak market, as the
    footer says), i.e. money in the stock = risk x equity / (stop distance). Trades are then run exactly as in the replay: the trade's R is the
    account's gain per unit of money risked, so the account gains risk x equity x R when it is sold.
  * The account is marked to market every Friday from the weekly `path_R` marks of the open trades (a file without them gives the realised curve only
    and says so), so drawdowns include open losses at weekly resolution.

HOW TO READ IT: this is ONE history, not a forecast, and the same caveats as every replay apply (today's NIFTY 500 only = survivors, price-only score,
no taxes, no slippage beyond the 0.35%). The order-of-preference test compares the shipped order with 30 random orders on the same signals: if the
shipped order is not clearly above most of them, the data cannot show that the order adds anything once capital is limited.

Usage:   python -m backtest.portfolio_study <trades.csv> [--write reports/evidence/portfolio_study_<date>.md]
"""
from __future__ import annotations

import argparse
import heapq
import os
import sys

import numpy as np
import pandas as pd

import config
from backtest import engine
from backtest import spike_study as ss

# the grid, declared before the run
RISKS = (0.005, 0.01)                 # risk per trade: the footer's rule is 1% (half in a weak market); 0.5% is half of that everywhere
STOCKS = (5, 8, 12, 20)               # most stocks bought-or-waiting at once
REFERENCE = ((0.01, 8), (0.005, 12))  # the two accounts compared across orders of preference: (risk, most stocks)
SEEDS = 30                            # random orders drawn for the comparison
WEEKS_YEAR = 52
PATH_STEP = engine.PATH_STEP
NEEDED = {"signal_date", "status", "net", "R", "risk_pct", "regime", "setup", "quality_pct", "sma200_chg", "fill_date", "exit_date"}


def load(path: str) -> pd.DataFrame:
    """The trades file, checked for the columns the account needs. `path_R` is optional (without it only the realised curve can be drawn)."""
    df = pd.read_csv(path)
    missing = NEEDED - set(df.columns)
    if missing:
        raise SystemExit(f"{path} lacks the columns {sorted(missing)} - run the Backtest & Calibrate workflow (backtest, swing, data_source=yahoo).")
    for c in ("signal_date", "fill_date", "exit_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _parse_path(v) -> np.ndarray | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return np.array([float(x) for x in v.split(";")])
    except ValueError:
        return None


class Book:
    """The finished signals of a trades file (the trade closed, or the order never filled) as plain arrays; the account runs over them many times."""

    def __init__(self, df: pd.DataFrame):
        d = df[df["status"].isin(["CLOSED", "UNFILLED"])].copy()
        bad = (d["status"] == "CLOSED") & (d["fill_date"].isna() | d["exit_date"].isna() | ~np.isfinite(d["R"].to_numpy(float)))
        d = d[~bad & d["signal_date"].notna() & np.isfinite(d["risk_pct"].to_numpy(float)) & (d["risk_pct"] > 0)]
        d = d.sort_values(["signal_date", "symbol"] if "symbol" in d.columns else ["signal_date"]).reset_index(drop=True)
        self.n = len(d)
        self.closed = (d["status"] == "CLOSED").to_numpy()
        day = lambda s: s.to_numpy("datetime64[ns]").astype("datetime64[D]")           # noqa: E731
        self.sig = day(d["signal_date"])
        self.fill = day(d["fill_date"])
        self.exit = day(d["exit_date"])
        # a buy order is live for ENTRY_VALID_SESSIONS sessions after the signal: its room is free again when the last of them has passed
        self.expiry = np.busday_offset(self.sig, config.ENTRY_VALID_SESSIONS, roll="forward")
        self.R = np.where(self.closed, d["R"].to_numpy(float), 0.0)
        self.net = np.where(self.closed, d["net"].to_numpy(float), 0.0)                   # the trade's own result after costs, as a share of its price
        self.rf = d["risk_pct"].to_numpy(float)
        self.weak = (d["regime"] == "weak").to_numpy()
        self.regime = d["regime"].astype(str).to_numpy()                                  # healthy / mixed / weak, as the scan labelled the market that day
        self.breadth = d["breadth50"].to_numpy(float) if "breadth50" in d.columns else np.full(self.n, np.nan)   # share of stocks above their 50-day average
        for name in ("ret252", "ext_pct", "ema21_x", "dist_hi", "rv15"):                             # what the stock had done by the signal day (NaN when the file lacks it)
            setattr(self, name, d[name].to_numpy(float) if name in d.columns else np.full(self.n, np.nan))
        self.strong = (d["sma200_chg"].to_numpy(float) >= ss.STRONG_TREND)
        self.fresh = (d["setup"] == "fresh").to_numpy()
        self.setup = d["setup"].astype(str).to_numpy()                                   # fresh / primed (the shipped rule) or turn (the early turn-up lane of backtest/turn_up_study.py)
        self.symbol = d["symbol"].astype(str).to_numpy() if "symbol" in d.columns else np.array([f"#{k}" for k in range(self.n)])    # for simulate's lock_same_stock
        self.q = d["quality_pct"].to_numpy(float)
        self.tie = np.arange(self.n)                                                      # symbol order inside a day (the frame is sorted by it)
        self.has_path = "path_R" in d.columns
        self.paths = [_parse_path(v) if c else None for v, c in zip(d["path_R"] if self.has_path else [None] * self.n, self.closed)]
        days, first = np.unique(self.sig, return_index=True)
        bounds = list(first) + [self.n]
        self.days = days
        self.by_day = [np.arange(bounds[k], bounds[k + 1]) for k in range(len(days))]
        ends = np.concatenate([self.exit[self.closed], self.sig]) if self.n else self.sig
        self.start, self.end = (days[0], np.max(ends)) if self.n else (None, None)

    def order(self, idx: np.ndarray, how: str, rng) -> np.ndarray:
        """One day's signals (indices) in the order they are worked through."""
        if how == "random":
            return rng.permutation(idx)
        if how == "quality":
            return idx[np.lexsort((self.tie[idx], -self.q[idx]))]
        return idx[np.lexsort((self.tie[idx], -self.q[idx], ~self.fresh[idx], ~self.strong[idx]))]       # shipped = pipeline.rank_key


def _mark(book: Book, i: int, d) -> float:
    """Where open trade i stood, in R after costs, at date d (its latest weekly mark)."""
    p = book.paths[i]
    if p is None or len(p) == 0:
        return 0.0
    b = int(np.busday_count(book.fill[i], d))
    return float(p[min(max(b, 0) // PATH_STEP, len(p) - 1)])


def issued_mask(book: Book, cap: int | None, how: str = "shipped", seed: int = 0) -> np.ndarray:
    """
    Which signals a scan that lists at most `cap` new picks a day would have issued: the first `cap` of each day in the order of `how` ("shipped" =
    the scanner's own order, "random" = `cap` drawn at random, which is what a cap would do if the order carried no information). Every signal when
    `cap` is None. Used by backtest/pick_cap_study.py (a cap on the picks per day is a rule change, so it is replayed before it is considered).
    """
    if cap is None:
        return np.ones(book.n, dtype=bool)
    rng = np.random.default_rng(seed)
    mask = np.zeros(book.n, dtype=bool)
    for idx in book.by_day:
        mask[book.order(idx, how, rng)[:max(int(cap), 0)]] = True
    return mask


def day_ranks(book: Book) -> np.ndarray:
    """Each signal's place (1 = first) among its own day's signals in the scanner's order of preference."""
    rank = np.zeros(book.n, dtype=int)
    for idx in book.by_day:
        for place, i in enumerate(book.order(idx, "shipped", None), start=1):
            rank[i] = place
    return rank


def simulate(book: Book, how: str = "shipped", max_stocks: int = 10, risk: float = 0.01, seed: int = 0, weak_half: bool = True,
             cap: int | None = None, mult: np.ndarray | None = None, skip: np.ndarray | None = None, lock_same_stock: bool = False) -> dict:
    """
    Run the account over the whole file. Returns the weekly marked-to-market equity and the counts.
    `cap` = the scan lists at most that many new picks a day (the first `cap` of the day's signals in the order `how`); the rest never reach the
    account and are counted in out["capped"]. None = every signal of the file, as before.
    `mult` = one factor per signal of the book on the money risked on it (0.5 = half the usual risk, so half the money in the stock; None = 1 for
    all). `skip` = one flag per signal: a flagged signal is one the scan would not have listed, so it never reaches the account and is counted in
    out["skipped"] (before `cap` is applied). Both are used by backtest/busy_day_study.py; left out, every result is what it was.
    `lock_same_stock` = one position per stock, as the scanner's tracker keeps it: a signal for a stock the account already holds or has a buy order
    waiting for is never listed, and is counted in out["same_stock"] (before the rooms are looked at). Used by backtest/hold_limit_study.py, where a
    trade held longer keeps its stock busy for longer; off, every result is what it was.
    """
    for name, arr in (("mult", mult), ("skip", skip)):
        if arr is not None and len(arr) != book.n:
            raise ValueError(f"{name} needs one entry per signal of the book ({book.n}), got {len(arr)}")
    rng = np.random.default_rng(seed)
    equity = 100.0                                   # realised: the start plus the profit of every trade sold so far
    committed = 0.0                                  # money in bought-or-waiting stocks
    active: dict[int, tuple[float, float]] = {}      # trade -> (money risked, money in the stock)
    exits: list[tuple[int, int]] = []                # (day sold, trade) for bought stocks
    expiries: list[tuple[int, int]] = []             # (day the buy order lapses, trade)
    in_book: dict[str, int] = {}                     # stock -> trades of it bought-or-waiting (only kept up to date for lock_same_stock)
    out = {"placed": 0, "closed": 0, "unfilled": 0, "full": 0, "cash": 0, "capped": 0, "skipped": 0, "same_stock": 0, "signals": book.n, "sum_R": 0.0}
    if not book.n:
        return {**out, "equity": pd.Series(dtype=float), "held": 0.0, "invested": 0.0}
    fridays = pd.date_range(pd.Timestamp(book.start), pd.Timestamp(book.end), freq="W-FRI").to_numpy("datetime64[D]")
    weeks = set(fridays.tolist()) | {book.end.item()}
    signal_at = {d.item(): k for k, d in enumerate(book.days)}
    timeline = sorted(weeks | set(signal_at))
    curve: list[tuple] = [(pd.Timestamp(book.start), 100.0)]         # the account at the start
    held_sum, inv_sum, steps = 0.0, 0.0, 0

    def release(upto: int) -> None:
        nonlocal equity, committed
        while exits and exits[0][0] <= upto:
            _, i = heapq.heappop(exits)
            ra, value = active.pop(i)
            equity += ra * book.R[i]
            committed -= value
            out["sum_R"] += book.R[i]
            in_book[book.symbol[i]] = in_book.get(book.symbol[i], 1) - 1
        while expiries and expiries[0][0] <= upto:
            _, i = heapq.heappop(expiries)
            _, value = active.pop(i)
            committed -= value
            in_book[book.symbol[i]] = in_book.get(book.symbol[i], 1) - 1

    for d in timeline:
        dn = np.datetime64(d, "D")
        release(int(dn.astype(np.int64)))
        if d in signal_at:
            day = book.order(book.by_day[signal_at[d]], how, rng)
            if skip is not None:
                kept = day[~np.asarray(skip, dtype=bool)[day]]
                out["skipped"] += len(day) - len(kept)
                day = kept
            if cap is not None and len(day) > cap:
                out["capped"] += len(day) - max(int(cap), 0)
                day = day[:max(int(cap), 0)]
            for i in day:
                if lock_same_stock and in_book.get(book.symbol[i], 0) > 0:
                    out["same_stock"] += 1
                    continue
                if len(active) >= max_stocks:
                    out["full"] += 1
                    continue
                ra = risk * (0.5 if (weak_half and book.weak[i]) else 1.0) * (1.0 if mult is None else float(mult[i])) * equity
                value = ra / book.rf[i]
                if committed + value > equity + 1e-9:
                    out["cash"] += 1
                    continue
                active[i] = (ra, value)
                in_book[book.symbol[i]] = in_book.get(book.symbol[i], 0) + 1
                committed += value
                out["placed"] += 1
                if book.closed[i]:
                    heapq.heappush(exits, (int(book.exit[i].astype(np.int64)), i))
                    out["closed"] += 1
                else:
                    heapq.heappush(expiries, (int(book.expiry[i].astype(np.int64)), i))
                    out["unfilled"] += 1
        if d in weeks:
            unreal, n_held, invested = 0.0, 0, 0.0
            for i, (ra, value) in active.items():
                if book.closed[i] and book.fill[i] <= dn:
                    n_held += 1
                    invested += value
                    unreal += ra * _mark(book, i, dn)
            marked = equity + unreal
            curve.append((pd.Timestamp(dn), marked))
            held_sum += n_held
            inv_sum += invested / marked if marked > 0 else 0.0
            steps += 1
    release(2 ** 40)
    eq = pd.Series([v for _, v in curve], index=pd.DatetimeIndex([t for t, _ in curve]))
    return {**out, "equity": eq, "realised": equity, "held": held_sum / max(steps, 1), "invested": inv_sum / max(steps, 1)}


def metrics(eq: pd.Series) -> dict:
    """The numbers one wants from a weekly equity curve."""
    if len(eq) < 3:
        return {}
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0) if eq.iloc[-1] > 0 else -1.0
    under = eq / eq.cummax() - 1.0
    rolling = (eq / eq.shift(WEEKS_YEAR) - 1.0).dropna()
    spell = longest = 0
    for u in under.to_numpy():
        spell = spell + 1 if u < -1e-9 else 0
        longest = max(longest, spell)
    return {"years": years, "total": total, "cagr": cagr, "max_dd": float(under.min()), "longest_under_weeks": int(longest),
            "w12_worst": float(rolling.min()) if len(rolling) else float("nan"), "w12_median": float(rolling.median()) if len(rolling) else float("nan"),
            "w12_neg": float((rolling < 0).mean()) if len(rolling) else float("nan"), "n_w12": int(len(rolling))}


def by_year(eq: pd.Series) -> dict:
    """Calendar-year return of the marked-to-market curve (a part-year is a part-year: the first and the last year are marked with *)."""
    out = {}
    last = eq.groupby(eq.index.year).last()
    first = eq.groupby(eq.index.year).first()
    prev = None
    for y in last.index:
        base = prev if prev is not None else first[y]
        out[int(y)] = float(last[y] / base - 1.0)
        prev = float(last[y])
    return out


def concurrency(book: Book, mask: np.ndarray | None = None) -> dict:
    """If EVERY filled signal were bought: how many stocks would be open at once, and how much money the message's sizing (1% risk, half in a weak
    market) would put in them as a share of the capital. `mask` = only the signals a scan with a cap would have issued (issued_mask)."""
    m = book.closed if mask is None else (book.closed & mask)
    if not m.any():
        return {}
    days = np.arange(book.fill[m].min().astype(np.int64), book.exit[m].max().astype(np.int64) + 2)
    f = book.fill[m].astype(np.int64) - days[0]
    e = book.exit[m].astype(np.int64) - days[0]
    cnt = np.zeros(len(days) + 1)
    money = np.zeros(len(days) + 1)
    w = (config.RISK_PER_TRADE_PCT / 100.0) * np.where(book.weak[m], 0.5, 1.0) / book.rf[m]
    np.add.at(cnt, f, 1)
    np.add.at(cnt, e + 1, -1)
    np.add.at(money, f, w)
    np.add.at(money, e + 1, -w)
    cnt, money = np.cumsum(cnt)[:-1], np.cumsum(money)[:-1]
    weekday = np.is_busday(days.astype("datetime64[D]")) & (cnt > 0)
    c, mo = cnt[weekday], money[weekday]
    return {"avg": float(c.mean()), "median": float(np.median(c)), "p90": float(np.percentile(c, 90)), "peak": int(c.max()),
            "money_avg": float(mo.mean()), "money_p90": float(np.percentile(mo, 90))}


def _row(label: str, r: dict, m: dict, years: float) -> str:
    return (f"{label:<20} {r['held']:>6.1f} {r['invested']:>8.0%} {r['placed'] / years:>6.0f} {r['placed'] / max(r['signals'], 1):>7.0%} "
            f"{m['cagr']:>+7.1%} {m['max_dd']:>+9.1%} {m['w12_worst']:>+9.1%} {m['w12_neg']:>9.0%} {m['total'] + 1:>5.1f}x")


HEAD = (f"{'account':<20} {'stocks':>6} {'money in':>8} {'orders':>6} {'of all':>7} {'growth':>7} {'worst':>9} {'worst':>9} {'12 months':>9} {'100':>6}\n"
        f"{'':<20} {'held':>6} {'stocks':>8} {'a year':>6} {'signals':>7} {'a year':>7} {'drawdown':>9} {'12 months':>9} {'below 0':>9} {'became':>6}")


def analyse(df: pd.DataFrame, seeds: int = SEEDS) -> list[str]:
    """The study as printable lines."""
    out: list[str] = []
    add = out.append
    book = Book(df)
    if not book.n:
        return ["No finished signals in the file."]
    years = (book.end - book.start).astype(int) / 365.25
    add(f"Finished signals {book.n} ({int(book.closed.sum())} filled and sold, {int((~book.closed).sum())} orders that never filled), "
        f"{pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} ({years:.1f} years)")
    if not book.has_path:
        add("NOTE: this file has no weekly path column (path_R): the accounts below are marked at sale prices only, so open losses are NOT in their "
            "drawdowns. Re-run the back-test with the code of 4 Nov 2026 or later.")

    add("\nThe account (fixed before the run): start 100; work down each day's signals in the scanner's order; buy an order only if a room is free "
        "(fewer than N stocks bought-or-waiting) and the money fits the account (no borrowing); risk R% of the account to the first stop (half in a weak "
        "market); trades run exactly as in the replay; marked to market every Friday.")

    add("\n== 1. If every signal were bought: what that would take ==")
    c = concurrency(book)
    add(f"stocks open at once: average {c['avg']:.0f}, median {c['median']:.0f}, 90th percentile {c['p90']:.0f}, peak {c['peak']}")
    add(f"money in them at the message's sizing ({config.RISK_PER_TRADE_PCT:g}% risk, half in a weak market): average {c['money_avg']:.0%} of the capital, "
        f"90th percentile {c['money_p90']:.0%}")
    add("so the per-signal averages in the other studies describe an account that cannot exist; the rest of this page is about accounts that can.")

    add("\n== 2. Accounts that can hold only a few stocks (the scanner's order of preference) ==")
    add(HEAD)
    cache: dict[tuple, dict] = {}
    for risk in RISKS:
        for n in STOCKS:
            r = simulate(book, "shipped", n, risk)
            cache[(risk, n)] = r
            add(_row(f"risk {risk:.1%}, up to {n}", r, metrics(r["equity"]), years))
    add("stocks held = the average number bought at once (a buy order that is still waiting also takes a room); money in stocks = what they cost as a "
        "part of the account; 'worst 12 months' and 'below 0' look at every rolling 52-week window; growth is a compound yearly rate; worst drawdown is "
        "from the highest weekly value to the lowest after it.")
    add("where 'stocks held' stops growing as the cap rises (look at the 1% rows), the money limit - not the cap - is what holds the account back, and "
        "the different results of those rows are the luck of which signals came when.")

    add("\n== 3. Does the order of preference matter once capital is limited? (the same signals; random = 30 random orders) ==")
    add(f"{'account':<26} {'order':<10} {'growth/yr':>11} {'worst drawdown':>15} {'orders/yr':>10}")
    for risk, n in REFERENCE:
        res = {"shipped": simulate(book, "shipped", n, risk), "quality": simulate(book, "quality", n, risk)}
        rnd = [simulate(book, "random", n, risk, seed=s) for s in range(seeds)]
        mt = {k: metrics(v["equity"]) for k, v in res.items()}
        mr = [metrics(v["equity"]) for v in rnd]
        label = f"risk {risk:.1%}, up to {n}"
        for k in ("shipped", "quality"):
            add(f"{label:<26} {k:<10} {mt[k]['cagr']:>+11.1%} {mt[k]['max_dd']:>+15.1%} {res[k]['placed'] / years:>10.0f}")
        g = np.array([m["cagr"] for m in mr])
        d = np.array([m["max_dd"] for m in mr])
        add(f"{label:<26} {'random':<10} {np.median(g):>+11.1%} {np.median(d):>+15.1%} {np.mean([v['placed'] for v in rnd]) / years:>10.0f}"
            f"   (growth {np.percentile(g, 10):+.1%} to {np.percentile(g, 90):+.1%}, drawdown {np.percentile(d, 10):+.1%} to {np.percentile(d, 90):+.1%}, "
            f"10th to 90th percentile)")
        beat = float((g < mt["shipped"]["cagr"]).mean())
        add(f"{'':<26} -> the shipped order beat {beat:.0%} of the {seeds} random orders on growth and {float((d < mt['shipped']['max_dd']).mean()):.0%} "
            f"of them had a deeper drawdown")

    add("\n== 4. Year by year (the shipped order; '*' = a part-year) ==")
    first_year = pd.Timestamp(book.start).year
    last_year = pd.Timestamp(book.end).year
    ys = None
    cols = []
    for risk, n in REFERENCE:
        r = cache.get((risk, n)) or simulate(book, "shipped", n, risk)
        cols.append((f"risk {risk:.1%}, up to {n}", by_year(r["equity"])))
        ys = sorted(set(ys or []) | set(cols[-1][1]))
    add(f"{'year':<8}" + "".join(f"{lab:>26}" for lab, _ in cols))
    for y in ys:
        part = "*" if y in (first_year, last_year) else " "
        add(f"{y:<7}{part}" + "".join(f"{(col.get(y, float('nan'))):>+26.1%}" for _, col in cols))

    add("\n== 5. How well do the accounts' trades do against the average signal? ==")
    allm = book.closed
    add(f"all signals: average {book.R[allm].mean():+.2f}R per filled trade ({int(allm.sum())} trades)")
    for risk, n in REFERENCE:
        r = cache.get((risk, n)) or simulate(book, "shipped", n, risk)
        add(f"risk {risk:.1%}, up to {n}: {r['closed']} filled trades taken, average {r['sum_R'] / max(r['closed'], 1):+.2f}R; "
            f"{r['full']} signals skipped because every room was taken, {r['cash']} because the money did not fit")

    add("\n== 6. Read this before relying on any of it ==")
    never = float((~book.closed).mean())
    for line in (
            "One history, not a forecast: the year-by-year table shows how much of the growth came from a few years.",
            "Today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost: the LEVELS are flattered; "
            "the comparison between accounts and between orders is less affected.",
            "Open trades are marked from weekly points, so a fall inside a week is not seen; the real drawdown can be deeper than shown.",
            f"Rooms count bought stocks AND buy orders still waiting ({never:.0%} of the orders never fill), which is cautious; sizes follow the "
            "account's realised value, not its marked value.",
            "Which of a day's signals an account takes changes its yearly growth by about the spread of the random orders in section 3: differences "
            "between two accounts that are smaller than that are noise."):
        add("- " + line)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", help="the *_trades.csv written by the Yahoo back-test (with the weekly path column)")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    lines = analyse(load(args.trades))
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"# Portfolio study: what an account that can hold only a few stocks would have earned\n\nSource: `{os.path.basename(args.trades)}`. "
                    f"How to read it: see the top of `backtest/portfolio_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

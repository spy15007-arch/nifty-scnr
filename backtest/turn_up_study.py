"""
Offline study of ONE idea, on two Yahoo back-test trades files: would an EARLY TURN-UP lane - buying the first strong close back above the moving averages after a pullback, while the stock is
still well below its old high - have earned more per trade than the shipped picks?

WHY (9 Nov 2026): the user looked at the charts of PAYTM and NH, two stocks the Sunday 8 Nov BTST scan listed, and said the scanner should have caught them earlier (PAYTM at 1,719, NH at 1,750)
"to catch maximum profit". He is right about the facts. The shipped rule can only list a stock that has just closed above its prior 50-day high on volume (FRESH) or sits within 3% below it
(PRIMED), so it buys the SECOND leg of a recovery, not the first: on the days he points at, PAYTM (3 Nov, closed 1,720) was 7.3% below that high and NH (2 Nov, opened 1,751, closed 1,834) 5.9% below
it. Both were listed only on 8 Nov, at 1,800 and 1,925.6 (buy orders at 1,859.25 and 1,956.85). Whether an earlier entry pays IN GENERAL is another question: two winners are two winners, and a stock
turns up on a chart far more often than it follows through. The rule below was written down BEFORE any of its statistics was calculated (the file is committed to GitHub before the first run).

THE LANE (a family of signals the shipped scanner does not have; judged at the close, like every replay signal):
  T1. The stock closes more than PRIMED_NEAR_PCT (3%) and not more than PULLBACK_MAX (15%) below its prior 50-day high (the shipped bands end exactly where this one starts).
  T2. It closes above BOTH its 21-day exponential average and its 50-day average, and the day before it did not (the first close back above them).
  T3. The day is up at least MIN_GAIN (3%) and closes in the top 40% of its range (the shape of a fresh breakout day). There is NO volume condition: PAYTM's day had normal volume.
      The split by relative volume is shown below, not used.
  T4. The same quality gate as every pick: the top half of the liquid stocks that day.
  T5. The shipped trade plan, unchanged: a buy-stop at the signal day's high x 1.001, valid 5 sessions, skipped if the stock gaps more than 3% past it, stop 2.5 ATR below the fill, trail 5 ATR
      below the highest close, 60 sessions at most, 0.35% round-trip costs, the same 15-session cool-down, one trade per stock at a time and the same cap on picks a day.
  The replay runs the lane on the same bars in the same run and writes it to its own file (reports/output/backtest_swing_turnup_yahoo_<date>_trades.csv) next to the shipped one.

THE TEST (every R is after costs; "filled trade" = a signal whose buy order filled and was sold; the swing replay only):
  * The unit of luck is the calendar month of the signal date: every range below is made by drawing whole months at random with replacement (BOOT resamples, fixed seed) and recalculating. A range is
    the ALPHA (1%) percentile at each end (a 98% range).
  * L1, THE LANE PAYS ON ITS OWN: its filled trades average at least MIN_PAYS (+0.20R) and the LOWER end of the range is above 0.
  * THE LANE AGAINST THE SHIPPED PICKS, lane minus shipped per filled trade. BETTER needs all three:
      G1. at least +MIN_EDGE (+0.10R), and the lower end of the range is above 0;
      G2. the same inside each calendar month (months with at least 3 filled trades of both kinds, weighted by n_a n_b / (n_a + n_b)), so the mood of a month cannot make it;
      G3. the lane is above the shipped picks in each of 2016-19, 2020-22 and 2023-26, each with at least MIN_CELL (100) filled trades of both kinds (a part with fewer counts as a no).
    WORSE is the mirror image (G1 at most -0.10R with the UPPER end below 0, G2 the same, G3 below in each part). Neither is UNCLEAR.
  * WHAT CHANGES. L1 met AND BETTER: the lane is worth adding. It is then PROPOSED to the user as a clearly labelled swing-message section with its own buy orders; it is not switched on without his
    yes, because it changes which orders he places and how much capital they need. Anything else: nothing changes in the scanner and the result is reported as it is (WORSE, or L1 missed, means the
    later entry is not what costs profit; UNCLEAR means the data cannot say). The BTST scan cannot be replayed on daily candles: this study says nothing about it.

SHOWN FOR INFORMATION, NOT PART OF THE RULE: the lane year by year, by depth of the pullback, by relative volume (1.5x or more) and by market regime; and the matched episodes - for each lane signal, the
next shipped signal on the same stock within EPISODE_DAYS calendar days: how much higher its buy price was, and how many lane signals the shipped rule never listed (the stock never got back near its high).

WHAT IT CANNOT DO: (1) the idea came from two charts that worked, and the thresholds were chosen by reading those two charts and no outcome statistic; fixing the rule first makes the test honest about
what it asks, not independent of the first look. (2) The replay's universe is today's NIFTY 500: stocks that turned up and then fell out of the index are missing, which flatters an early entry (more of
its trades end badly) more than it flatters the shipped picks - a BETTER result is weaker than it looks, a WORSE one stronger. (3) The quality score is the price-only one. (4) Orders are judged on daily
candles: what a stock did inside a day cannot be replayed.

A FIRST LOOK, ALSO NOT PART OF THE RULE (`--cache`): both lanes on the 150 stocks whose candles the BTST scan saves (about 15 months), one stock at a time and WITHOUT the quality gate, since 12 months of history are needed
to score. Those 150 were all near their 50-day highs on 8 Nov, which flatters any buy-the-turn rule, so it only shows how often the rule fires and how its trades end. Nothing is decided on it.

Usage:   python -m backtest.turn_up_study <shipped_trades.csv> <turnup_trades.csv> [--write reports/evidence/turn_up_study_<date>.md]
         python -m backtest.turn_up_study --cache data_cache/btst_history.csv.gz [--stocks PAYTM,NH] [--write reports/evidence/turn_up_first_look_<date>.md]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

import numpy as np
import pandas as pd

import config
from backtest import busy_day_study as bd
from backtest import engine
from backtest import late_pick_study as lp
from backtest import portfolio_study as ps
from scanner import footprint as fp
from scanner import tradesim as ts
from scanner.pipeline import MAX_RISK_PCT, tick_up

# the rule and the bars, declared before the run
LANE = "turn"                                   # the setup name the lane's trades carry in the file
PULLBACK_MAX = 0.15                             # T1: at most this far below the prior 50-day high
MIN_GAIN = 0.03                                 # T3: the signal day's gain
MIN_CLOSE_POS = config.FRESH_MIN_CLOSE_POSITION  # T3: closes in the top 40% of the day's range
TRIGGER_MULT = 1.001                            # T5: the buy-stop sits at the signal day's high x this (what a fresh breakout uses)
MIN_PAYS = 0.20                                 # L1: the lane's average R per filled trade
MIN_EDGE = lp.MIN_EDGE                          # G1 / G2: the gap that counts, on either side
ALPHA = bd.ALPHA
BOOT = bd.BOOT
SEED = 20261109
MIN_MONTH_CELL = bd.MIN_MONTH_CELL
MIN_CELL = bd.MIN_CELL
PERIODS = bd.PERIODS
EPISODE_DAYS = 60                               # shown only: how long after a lane signal the shipped rule may list the same stock
VOLUME_SPLIT = 1.5                              # shown only
DEPTHS = (0.03, 0.06, 0.10, 0.15)               # shown only: bands of the pullback depth
FIRST_LOOK_MIN_BARS = 120


# --------------------------------------------------------------------------- #
# the rule
def turn_flags(close, piv_incl, gain1d, close_pos, ema21_x, sma50, prev_close, prev_ema21_x, prev_sma50) -> np.ndarray:
    """T1-T3 on arrays (one entry per stock-day; `prev_*` = the previous session's values). The quality gate (T4) and the plan (T5) are applied by the caller."""
    with np.errstate(all="ignore"):
        gap = np.asarray(close, float) / np.asarray(piv_incl, float) - 1.0
        today = (np.asarray(close, float) > np.asarray(sma50, float)) & (np.asarray(ema21_x, float) > 0.0)
        yesterday = (np.asarray(prev_close, float) > np.asarray(prev_sma50, float)) & (np.asarray(prev_ema21_x, float) > 0.0)
        known = np.isfinite(np.asarray(prev_close, float)) & np.isfinite(np.asarray(prev_ema21_x, float)) & np.isfinite(np.asarray(prev_sma50, float))
        return ((gap < -config.PRIMED_NEAR_PCT) & (gap >= -PULLBACK_MAX) & today & known & ~yesterday
                & (np.asarray(gain1d, float) >= MIN_GAIN) & (np.asarray(close_pos, float) >= MIN_CLOSE_POS))


def candidates(ti: int, js, feats: pd.DataFrame, scored: pd.DataFrame, M: dict, syms: list, gate: float) -> list:
    """The lane's signals for session `ti`, in the form backtest.engine.run_backtest(candidates_fn=...) takes: [(rank, -quality, symbol, setup dict), ...]."""
    if ti < 1 or feats is None or len(feats) == 0:
        return []
    col = lambda k: feats[k].to_numpy(float)                                     # noqa: E731
    prev = lambda k: np.asarray(M[k][ti - 1][js], dtype=float)                   # noqa: E731
    hit = turn_flags(col("close"), col("piv_incl"), col("gain1d"), col("close_pos"), col("ema21_x"), col("sma50"), prev("close"), prev("ema21_x"), prev("sma50"))
    out = []
    for r in np.nonzero(hit)[0]:
        q = scored["quality_pct"].iloc[r]
        if not (isinstance(q, (float, np.floating)) and math.isfinite(q)) or q < gate:
            continue
        close, piv, high = float(feats["close"].iloc[r]), float(feats["piv_incl"].iloc[r]), float(feats["high"].iloc[r])
        out.append((0, -float(q), feats.index[r], {"setup": LANE, "ext_pct": close / piv - 1.0, "trigger": high * TRIGGER_MULT, "above_pivot": False}))
    return out


# --------------------------------------------------------------------------- #
# the test
def load(shipped_path: str, turn_path: str) -> pd.DataFrame:
    """Both trades files as one frame (the setup column tells them apart: fresh / primed = shipped, turn = the lane)."""
    return pd.concat([ps.load(shipped_path), ps.load(turn_path)], ignore_index=True)


def groups(book: ps.Book) -> tuple[np.ndarray, np.ndarray]:
    """(lane, shipped) as flags per signal."""
    return book.setup == LANE, (book.setup == "primed") | (book.setup == "fresh")


def single_estimate(book: ps.Book, mask: np.ndarray, ids: np.ndarray, n: int, c: np.ndarray) -> dict | None:
    """The average R per filled trade of one group, with its standard error and the ends of its range (whole months drawn at random)."""
    s, k = bd._sums(book, mask, ids, n)
    return bd.estimate(lambda cc: bd._ratio(cc @ s, cc @ k), c, ALPHA)


def pays_ok(est: dict | None) -> bool:
    """Rule L1: the lane averages at least MIN_PAYS and the lower end of its range is above 0."""
    return est is not None and est["point"] >= MIN_PAYS and est["lo"] > 0.0


def episodes(df: pd.DataFrame, days: int = EPISODE_DAYS) -> dict:
    """Shown only. For each lane signal, the next shipped signal on the same stock within `days` calendar days (the stock got back near its high and was listed)."""
    need = ["symbol", "signal_date", "trigger"]
    t = df[df["setup"] == LANE][need].rename(columns={"signal_date": "t_date", "trigger": "t_trigger"})
    s = df[df["setup"].isin(["primed", "fresh"])][need].rename(columns={"signal_date": "s_date", "trigger": "s_trigger"})
    out = {"n": int(len(t)), "listed": 0, "share": float("nan"), "median_premium": float("nan"), "mean_premium": float("nan"), "median_days": float("nan")}
    if t.empty or s.empty:
        return out
    m = pd.merge_asof(t.sort_values("t_date"), s.sort_values("s_date"), left_on="t_date", right_on="s_date", by="symbol", direction="forward",
                      allow_exact_matches=False, tolerance=pd.Timedelta(days=days))
    found = m["s_date"].notna()
    out["listed"] = int(found.sum())
    out["share"] = float(found.mean()) if len(m) else float("nan")
    if found.any():
        prem = (m.loc[found, "s_trigger"] / m.loc[found, "t_trigger"] - 1.0).to_numpy(float)
        out["median_premium"], out["mean_premium"] = float(np.median(prem)), float(prem.mean())
        out["median_days"] = float(np.median((m.loc[found, "s_date"] - m.loc[found, "t_date"]).dt.days))
    return out


def verdict(l1: dict | None, t1: dict | None, t2: dict | None, parts: list[dict]) -> dict:
    """The whole rule. `pays` = L1; `outcome` = 'better' / 'worse' / 'unclear' for the lane against the shipped picks; `add` = L1 and better."""
    v = lp.verdict(t1, t2, parts)
    pays = pays_ok(l1)
    return {"pays": pays, "worse": v["worse"], "better": v["better"], "outcome": v["outcome"], "add": bool(pays and v["outcome"] == "better")}


def result_text(v: dict, l1: dict | None, t1: dict | None) -> str:
    """The one-line outcome of the rule, given verdict()'s answer."""
    if l1 is None or t1 is None:
        return "RESULT: the lane or the shipped picks have too few filled trades to compare, so nothing changes in the scanner."
    own = f"The lane averaged {l1['point']:+.2f}R per filled trade ({'meeting' if v['pays'] else 'missing'} the {MIN_PAYS:+.2f}R bar with its range above 0)"
    gap = f"and {'-' if t1['point'] < 0 else '+'}{abs(t1['point']):.2f}R {'less' if t1['point'] < 0 else 'more'} than the shipped picks."
    if v["add"]:
        return (f"RESULT: {own} {gap} Every part of the BETTER rule is met: the early lane is worth adding. It is PROPOSED to the user (a labelled section with its own buy orders); "
                f"nothing is switched on without his yes. The replay is flattered by survivors, so this is no promise.")
    if v["outcome"] == "worse":
        return (f"RESULT: {own} {gap} Every part of the WORSE rule is met: the early entries earned clearly less than the shipped picks, so the later entry is not what costs profit. "
                f"Nothing changes in the scanner.")
    if v["outcome"] == "better":
        return (f"RESULT: {own} {gap} The lane beat the shipped picks on every part of the BETTER rule but its own bar was missed, so nothing changes in the scanner.")
    lean = "worse" if t1["point"] < 0 else "better"
    missed = [str(i) for i, k in enumerate(("rule1", "rule2", "rule3"), start=1) if not v[lean][k]]
    which = "G" + missed[0] if len(missed) == 1 else "G" + ", G".join(missed[:-1]) + " and G" + missed[-1]
    return (f"RESULT: {own} {gap} Neither side is shown (the {lean}-side rule {which} not met), so nothing changes in the scanner and the data cannot say whether entering "
            f"at the turn-up beats the shipped entry.")


def _num(x: float, fmt: str) -> str:
    return format(x, fmt) if np.isfinite(x) else "-"


def _yn(flag: bool) -> str:
    return "yes" if flag else "no"


def _lane_stats(book: ps.Book, mask: np.ndarray) -> dict:
    filled = book.closed & mask
    return {"signals": int(mask.sum()), "filled": int(filled.sum()), "R": lp._mean(book.R[filled]), "win": float((book.net[filled] > 0).mean()) if filled.any() else float("nan"),
            "net": lp._mean(book.net[filled]), "stopped": float((book.R[filled] < 0).mean()) if filled.any() else float("nan")}


def _stat_line(name: str, s: dict) -> str:
    return (f"{name:<22}{s['signals']:>9}{s['filled']:>9}{_num(s['R'], '+.2f'):>11}{_num(s['win'], '.0%'):>9}{_num(s['stopped'], '.0%'):>12}{_num(s['net'], '+.2%'):>16}")


STAT_HEAD = f"{'':<22}{'signals':>9}{'filled':>9}{'avg R':>11}{'won':>9}{'lost money':>12}{'avg result':>16}"


def _band_lines(book: ps.Book, lane: np.ndarray) -> list[str]:
    out = [f"{'pullback below the 50-day high':<34}{'signals':>9}{'filled':>9}{'avg R':>11}"]
    depth = -book.ext_pct
    for lo, hi in zip(DEPTHS[:-1], DEPTHS[1:]):
        m = lane & (depth > lo) & (depth <= hi)
        s = _lane_stats(book, m)
        out.append(f"{f'{lo:.0%} to {hi:.0%}':<34}{s['signals']:>9}{s['filled']:>9}{_num(s['R'], '+.2f'):>11}")
    return out


def analyse(df: pd.DataFrame, boot: int = BOOT) -> list[str]:
    """The study as printable lines (df = load(): both trades files in one frame)."""
    book = ps.Book(df)
    lane, shipped = groups(book)
    if not lane.any() or not shipped.any():
        return ["One of the two files has no finished signals (the lane or the shipped picks): run the Backtest & Calibrate workflow (backtest, swing, data_source=yahoo) and try again."]
    out: list[str] = []
    add = out.append
    ids, n_months = bd.month_blocks(book.sig)
    c = bd.resamples(n_months, boot, SEED)
    years = (book.end - book.start).astype(int) / 365.25
    add(f"Finished signals: {int(lane.sum())} of the early turn-up lane and {int(shipped.sum())} shipped picks, {pd.Timestamp(book.start).date()} to {pd.Timestamp(book.end).date()} "
        f"({years:.1f} years), {n_months} calendar months.")
    add("\nThe question: would buying the FIRST STRONG CLOSE BACK ABOVE THE AVERAGES (a stock 3-15% below its 50-day high, up 3%+ on the day) have earned more per filled trade than the shipped picks?")
    add("The rule (fixed before the run; the full text is at the top of backtest/turn_up_study.py):")
    add(f"  L1  the lane pays on its own: its average is {MIN_PAYS:+.2f}R or more and the lower end of the {1 - 2 * ALPHA:.0%} range is above 0.")
    add(f"  BETTER than the shipped picks needs all of: G1 lane minus shipped is {MIN_EDGE:+.2f}R or more with the lower end above 0; G2 the same inside each calendar month;")
    add(f"    G3 the lane is above the shipped picks in each of {', '.join(f'{lo}-{str(hi)[2:]}' for lo, hi in PERIODS)} (with {MIN_CELL}+ filled trades of both kinds in it). WORSE is the mirror image.")
    add("  Lane worth adding = L1 met AND BETTER; it is then proposed to the user, never switched on by itself. Anything else: nothing changes in the scanner.")
    add(f"Ranges: whole calendar months drawn at random {boot} times; each end of a range = the {ALPHA:.0%} percentile of those draws.")

    add("\n== 1. What each kind of signal did (filled and sold trades, after costs) ==")
    add(STAT_HEAD)
    add(_stat_line("early turn-up lane", _lane_stats(book, lane)))
    add(_stat_line("shipped picks", _lane_stats(book, shipped)))
    add("(lost money = share of filled trades that ended below their buy price after costs; signals include orders that never filled.)")

    l1 = single_estimate(book, lane, ids, n_months, c)
    cells = bd.Cells(book, lane, shipped, ids, n_months)
    t1, t2 = bd.estimate(cells.pooled, c, ALPHA), bd.estimate(cells.within, c, ALPHA)
    parts = lp.part_rows(book, lane, shipped)
    v = verdict(l1, t1, t2, parts)

    add("\n== 2. Rule L1: does the lane pay on its own? ==")
    add(lp._gap_text(l1) if l1 else "cannot be calculated (too few trades)")
    add(f"needs {MIN_PAYS:+.2f}R or more and the lower end above 0: {_yn(v['pays'])}")
    add("\n== 3. Rule G1: lane minus shipped picks, every filled trade ==")
    add(lp._gap_text(t1))
    add(f"better side: needs {MIN_EDGE:+.2f}R or more and the lower end above 0: {_yn(lp.better_ok(t1))};   worse side: {_yn(lp.worse_ok(t1))}")
    used = cells.eligible(MIN_MONTH_CELL)
    add("\n== 4. Rule G2: the same inside each calendar month ==")
    add(f"{int(used.sum())} of {n_months} months have at least {MIN_MONTH_CELL} filled trades of both kinds.")
    add(lp._gap_text(t2))
    add(f"better side: {_yn(lp.better_ok(t2))};   worse side: {_yn(lp.worse_ok(t2))}")
    add("\n== 5. Rule G3: in each part of the history ==")
    add(f"{'':<12}{'lane trades':>12}{'shipped':>10}{'lane R':>9}{'shipped R':>11}{'gap':>9}")
    for p in parts:
        add(f"{p['label']:<12}{p['n_ext']:>12}{p['n_other']:>10}{_num(p['mean_ext'], '+.2f'):>9}{_num(p['mean_other'], '+.2f'):>11}{_num(p['gap'], '+.2f'):>9}"
            + ("" if p["enough"] else "   (too few trades to judge)"))
    add(f"lane above the shipped picks in every part: {_yn(bool(parts) and all(p['better'] for p in parts))};   below in every part: {_yn(bool(parts) and all(p['worse'] for p in parts))}")

    add("\n== 6. The answer ==")
    add(result_text(v, l1, t1))

    add("\n== 7. For information only (not part of the rule) ==")
    add("The lane year by year (shipped picks for comparison):")
    add(f"{'year':<8}{'lane signals':>14}{'lane filled':>13}{'lane R':>9}{'shipped filled':>16}{'shipped R':>11}{'gap':>9}")
    year = book.sig.astype("datetime64[Y]").astype(int) + 1970
    for y in np.unique(year):
        inside = year == y
        a, b = _lane_stats(book, lane & inside), _lane_stats(book, shipped & inside)
        gap = a["R"] - b["R"] if a["filled"] and b["filled"] else float("nan")
        add(f"{int(y):<8}{a['signals']:>14}{a['filled']:>13}{_num(a['R'], '+.2f'):>9}{b['filled']:>16}{_num(b['R'], '+.2f'):>11}{_num(gap, '+.2f'):>9}")
    add("\nThe lane by how deep the pullback was:")
    out.extend(_band_lines(book, lane))
    rv = book.rv15
    if np.isfinite(rv).any():
        add(f"\nThe lane by the day's volume against the 15-day average:")
        add(STAT_HEAD)
        add(_stat_line(f"volume {VOLUME_SPLIT:.1f}x or more", _lane_stats(book, lane & (rv >= VOLUME_SPLIT))))
        add(_stat_line(f"volume under {VOLUME_SPLIT:.1f}x", _lane_stats(book, lane & (rv < VOLUME_SPLIT))))
    add("\nThe lane by the market regime of the signal day:")
    add(STAT_HEAD)
    for name in ("healthy", "mixed", "weak"):
        add(_stat_line(name, _lane_stats(book, lane & (book.regime == name))))
    ep = episodes(df)
    add(f"\nMatched episodes: of {ep['n']} lane signals, the shipped rule listed the same stock within {EPISODE_DAYS} calendar days after {ep['listed']} ({_num(ep['share'], '.0%')}); "
        f"the rest never got back near their old high in that time (or were not listed).")
    if ep["listed"]:
        add(f"For those, the shipped buy price was {ep['median_premium']:+.1%} above the lane's (median; mean {ep['mean_premium']:+.1%}), {ep['median_days']:.0f} calendar days later (median).")
        add("(Only the stocks that did recover appear here, so this is the cost of the later entry on the winners; the lane's own failures are in section 1.)")
    return out


# --------------------------------------------------------------------------- #
# a first look on the saved candles
def cache_look(hist: pd.DataFrame, cooldown: int | None = None) -> pd.DataFrame:
    """
    Both lanes on the saved candles of the BTST watchlist (columns symbol, date, open, high, low, close, volume), one stock at a time and WITHOUT the quality gate. One row per signal:
    symbol, lane ('turn' / 'primed' / 'fresh'), signal_date, status, net, R, risk_pct, trigger. The same cool-down, buy-stop, stops, costs and one-trade-at-a-time rule as the replay.
    """
    cooldown = config.COOLDOWN_SESSIONS if cooldown is None else cooldown
    rows: list[dict] = []
    for sym, g in hist.groupby("symbol"):
        d = fp.clean_bars(g.assign(date=pd.to_datetime(g["date"])).set_index("date")[["open", "high", "low", "close", "volume"]].sort_index())
        if d is None or len(d) < FIRST_LOOK_MIN_BARS:
            continue
        d = d[~d.index.duplicated(keep="last")]
        f = fp.feature_frame(d)
        prev = f.shift(1)
        liquid = ((f["close"] >= config.MIN_PRICE) & (f["turn20"] >= config.MIN_AVG_DOLLAR_VOLUME)).to_numpy()
        turn = turn_flags(f["close"], f["piv_incl"], f["gain1d"], f["close_pos"], f["ema21_x"], f["sma50"], prev["close"], prev["ema21_x"], prev["sma50"]) & liquid
        near = (f["close"] >= (1.0 - config.PRIMED_NEAR_PCT) * f["piv_incl"]).to_numpy() & liquid
        dates = d.index
        for which, flags in (("turn", turn), ("shipped", near)):
            last_sig, busy = -10 ** 9, -1
            for i in np.nonzero(flags)[0]:
                if i < last_sig + cooldown or i < busy:
                    continue
                if which == "turn":
                    su = {"setup": LANE, "trigger": float(f["high"].iloc[i]) * TRIGGER_MULT}
                else:
                    su = fp.detect_setup({k: float(f[k].iloc[i]) for k in engine._NEED})
                    if su["setup"] is None:
                        continue
                atr = float(f["atr_pct"].iloc[i])
                if not (math.isfinite(atr) and atr > 0):
                    continue
                plan = ts.TradePlan("stop", tick_up(su["trigger"]), atr)
                if plan.risk_pct > MAX_RISK_PCT:
                    continue
                last_sig = int(i)
                st, oc, exit_pos, void = engine._simulate(plan, d, int(i))
                status = "VOID" if void else st.status
                rows.append({"symbol": sym, "lane": su["setup"], "signal_date": dates[i].date().isoformat(), "status": status, "risk_pct": round(plan.risk_pct, 4),
                             "trigger": plan.trigger, "net": oc["net"] if (oc is not None and not void) else np.nan, "R": oc["R"] if (oc is not None and not void) else np.nan})
                if void:
                    busy = int(i)
                elif st.status in (ts.PENDING, ts.OPEN):
                    busy = 10 ** 9
                elif st.status == ts.CLOSED and exit_pos is not None:
                    busy = int(exit_pos)
    return pd.DataFrame(rows)


def first_look_lines(look: pd.DataFrame, stocks: tuple[str, ...] = ()) -> list[str]:
    """The first look as printable lines."""
    out = ["A FIRST LOOK on the saved candles of the BTST watchlist (no quality gate; every one of these stocks was near its 50-day high on 8 Nov, which flatters buy-the-turn rules; nothing is decided on it).", ""]
    if look is None or look.empty:
        return out + ["No signals."]
    out += [f"{'':<24}{'signals':>9}{'filled':>9}{'still open':>12}{'avg R':>9}{'won':>8}{'lost money':>12}{'avg result':>13}"]
    for name, mask in (("early turn-up lane", look["lane"] == LANE), ("shipped (fresh+primed)", look["lane"].isin(["primed", "fresh"]))):
        x = look[mask]
        done = x[x["status"] == "CLOSED"]
        out.append(f"{name:<24}{len(x):>9}{len(done):>9}{int((x['status'] == 'OPEN').sum()):>12}{_num(float(done['R'].mean()) if len(done) else float('nan'), '+.2f'):>9}"
                   f"{_num(float((done['net'] > 0).mean()) if len(done) else float('nan'), '.0%'):>8}{_num(float((done['R'] < 0).mean()) if len(done) else float('nan'), '.0%'):>12}"
                   f"{_num(float(done['net'].mean()) if len(done) else float('nan'), '+.2%'):>13}")
    out.append("(filled = bought and sold; still open = bought, not sold yet; avg R and the rest are over the filled trades only.)")
    for s in stocks:
        x = look[(look["symbol"] == s) & (look["lane"] == LANE)]
        out.append(f"\n{s}: the early rule fired {len(x)} times in the saved history:")
        for _, r in x.iterrows():
            res = f"{r['R']:+.2f}R ({r['net']:+.1%})" if np.isfinite(r["R"]) else "never bought or still open"
            out.append(f"  {r['signal_date']}  {r['status']:<9} {res}")
    return out


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("trades", nargs="*", help="the shipped *_trades.csv and the turn-up *_trades.csv written by the Yahoo back-test (in that order)")
    ap.add_argument("--cache", default="", help="a first look on the saved BTST candles (data_cache/btst_history.csv.gz) instead of the two trades files")
    ap.add_argument("--stocks", default="", help="with --cache: stocks whose lane signals are listed one by one, e.g. PAYTM,NH")
    ap.add_argument("--write", default="", help="also save the study to this markdown file")
    args = ap.parse_args(argv)
    if args.cache:
        lines = first_look_lines(cache_look(pd.read_csv(args.cache)), tuple(s.strip() for s in args.stocks.split(",") if s.strip()))
        head, src = "# Early turn-up lane: a first look on the saved candles", os.path.basename(args.cache)
    elif len(args.trades) == 2:
        lines = analyse(load(*args.trades))
        head, src = "# Early turn-up study: would buying the first strong close back above the averages have paid more?", " and ".join(os.path.basename(p) for p in args.trades)
    else:
        ap.error("give the two trades files (shipped, then turn-up), or --cache")
        return 2
    print("\n".join(lines))
    if args.write:
        os.makedirs(os.path.dirname(args.write) or ".", exist_ok=True)
        with open(args.write, "w", encoding="utf-8") as f:
            f.write(f"{head}\n\nSource: `{src}`. How to read it: see the top of `backtest/turn_up_study.py`.\n\n```\n" + "\n".join(lines) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

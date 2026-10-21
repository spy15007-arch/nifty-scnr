"""
Backtest of TODAY'S selection rule on whatever daily bars the broker returns, plus the live-scoreboard report.

The backtest is built from the very same parts as the live scan, so it cannot drift away from it:
  * features / setups / quality score : scanner.footprint  (feature_frame = every bar at once)
  * ranking, cool-down, pick cap      : the same rules as scanner.pipeline.select_picks
  * trade management                  : scanner.tradesim  (the state machine the scoreboard uses)

NO LOOK-AHEAD: on signal day t only bars up to and including t are used to decide; the trade then runs on
bars t+1 onward. Every trade is a buy-stop order for the next sessions (an end-of-day scan; the live
"buy near the close" BTST entry needs intraday data and cannot be replayed from daily bars).

LIMITS (read these before trusting a number):
  * Broker daily candles are NOT adjusted for splits / bonuses. A trade is dropped (VOID) if an overnight gap
    beyond +/-35% happens while it is unfinished, as the live scoreboard does; features around a split are
    distorted for that stock for a while, which can create a few false signals.
  * The quality score here is the 7-factor price-only version (delivery-% history is not available).
  * The market's liquidity ranking is judged on the stocks passed in, so the result describes that universe.
  * Costs are the 0.35% round trip used everywhere; real slippage on thin stocks can be larger.
The 7-year research on adjusted NSE data (reports/evidence/) remains the reference; this is a live-data cross-check.
"""
from __future__ import annotations

import logging
import math
import os
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

import config
from scanner import footprint as fp
from scanner import tradesim as ts
from scanner.pipeline import MAX_RISK_PCT, tick_up

logger = logging.getLogger(__name__)

VOID_GAP = 0.35
_NEED = ["close", "high", "turn20", "piv50", "piv_incl", "gain1d", "close_pos", "rv15", "dist_hi", "sma200_chg",
         "ret252", "atr_pct", "ema21_x", "ret5", "sma50", "sma200"]


def _naive_dates(index) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _prepare(bars: dict, min_bars: int):
    """Per symbol: cleaned bars on a plain-date index, and its feature frame."""
    clean, frames = {}, {}
    for sym, df in bars.items():
        d = fp.clean_bars(df)
        if d is None or len(d) < min_bars:
            continue
        d = d.copy()
        d.index = _naive_dates(d.index)
        d = d[~d.index.duplicated(keep="last")].sort_index()
        clean[sym] = d
        frames[sym] = fp.feature_frame(d)
    return clean, frames


def _simulate(plan: ts.TradePlan, d: pd.DataFrame, pos: int):
    """Run one trade over the symbol's bars after position `pos`. Returns (state, outcome, exit_pos, void)."""
    o = d["open"].to_numpy()[pos + 1:]
    h = d["high"].to_numpy()[pos + 1:]
    l = d["low"].to_numpy()[pos + 1:]
    c = d["close"].to_numpy()[pos + 1:]
    if len(c) == 0:
        return ts.new_state(plan), None, None, False
    prev = np.concatenate([[d["close"].to_numpy()[pos]], c[:-1]])
    with np.errstate(all="ignore"):
        gaps = o / prev - 1.0
    bad = np.nonzero(np.isfinite(gaps) & (np.abs(gaps) > VOID_GAP))[0]
    kv = int(bad[0]) if len(bad) else None
    n = kv if kv is not None else len(c)
    st = ts.replay(plan, zip(o[:n], h[:n], l[:n], c[:n]))
    if kv is not None and st.status in (ts.PENDING, ts.OPEN):
        return st, None, None, True
    last_close = float(c[n - 1]) if n else float(plan.trigger)
    oc = ts.outcome(plan, st, mark_px=last_close)
    exit_pos = pos + st.exit_n if st.status == ts.CLOSED else None
    return st, oc, exit_pos, False


def run_backtest(bars: dict, scan_mode: str = "swing", test_days: int = 250, cooldown: int | None = None,
                 max_picks: int | None = None, min_bars: int = 120, dlv: pd.DataFrame | None = None,
                 top_n_liquid: int | None = None) -> list[dict]:
    """
    Replay the rule on each of the last `test_days` sessions. Returns one dict per signal (including the ones
    that never filled or are still open - summarise_backtest separates them).

    dlv           optional DataFrame (index = dates, columns = symbols) of the lagged 20-day average delivery %;
                  with it the 8-factor score is used, without it the 7-factor price-only score
    top_n_liquid  keep only the N most-traded stocks of each day (the live scan's universe is the NIFTY 500);
                  leave None when `bars` already is the universe
    """
    cooldown = config.COOLDOWN_SESSIONS if cooldown is None else cooldown
    max_picks = max_picks or config.MAX_PICKS.get(scan_mode, 10)
    clean, frames = _prepare(bars, min_bars)
    if len(clean) < 30:
        logger.warning(f"Only {len(clean)} usable stocks - too few for a meaningful backtest")
        return []

    syms = sorted(clean)
    all_dates = sorted(set().union(*[set(d.index) for d in clean.values()]))
    T, S = len(all_dates), len(syms)
    date_pos = {d: i for i, d in enumerate(all_dates)}
    M = {k: np.full((T, S), np.nan) for k in _NEED}
    DL = np.full((T, S), np.nan)
    if dlv is not None and len(dlv):
        dl = dlv.copy()
        dl.index = _naive_dates(dl.index)
        dl = dl[~dl.index.duplicated(keep="last")]
        dl = dl.reindex(index=pd.DatetimeIndex(all_dates), columns=syms)
        DL = dl.to_numpy(dtype=float)
    own_pos: list[dict] = []
    for j, s in enumerate(syms):
        fr = frames[s]
        ix = np.array([date_pos[d] for d in fr.index])
        for k in _NEED:
            M[k][ix, j] = fr[k].to_numpy()
        own_pos.append({d: i for i, d in enumerate(clean[s].index)})

    test_idx = list(range(max(0, T - test_days), T))
    last_sig: dict[str, int] = {}
    busy_until: dict[str, int] = {}       # global date index when an unfinished trade ends (inf while still going)
    trades: list[dict] = []
    gate = config.QUALITY_GATE_PCT

    for ti in test_idx:
        close = M["close"][ti]
        ok = np.isfinite(close) & (close >= config.MIN_PRICE) & (M["turn20"][ti] >= config.MIN_AVG_DOLLAR_VOLUME) \
            & np.isfinite(M["piv50"][ti])
        if ok.sum() < 50:
            continue
        js = np.nonzero(ok)[0]
        if top_n_liquid and len(js) > top_n_liquid:
            js = js[np.argsort(-M["turn20"][ti][js], kind="stable")[:top_n_liquid]]
            js.sort()
        feats = pd.DataFrame({k: M[k][ti][js] for k in _NEED}, index=[syms[j] for j in js])
        dl_t = pd.Series(DL[ti][js], index=feats.index) if np.isfinite(DL[ti][js]).any() else None
        scored, _ = fp.score_universe(feats, dl_t, min_delivery_coverage=config.DELIVERY_MIN_COVERAGE)
        breadth = fp.market_breadth(feats)
        regime = fp.regime_label(breadth.get("breadth50"))
        near = (feats["close"] >= (1.0 - config.PRIMED_NEAR_PCT) * feats["piv_incl"]).to_numpy()

        cands = []
        for r in np.nonzero(near)[0]:
            sym = feats.index[r]
            q = scored["quality_pct"].iloc[r]
            if not (isinstance(q, (float, np.floating)) and math.isfinite(q)) or q < gate:
                continue
            su = fp.detect_setup({k: float(feats[k].iloc[r]) for k in _NEED})
            if su["setup"] is None:
                continue
            cands.append((0 if su["setup"] == "fresh" else 1, -float(q), sym, su))
        cands.sort(key=lambda t: (t[0], t[1], t[2]))

        picked = 0
        for _, negq, sym, su in cands:
            if picked >= max_picks:
                break
            if ti < last_sig.get(sym, -10**9) + cooldown or ti < busy_until.get(sym, -1):
                continue
            j = syms.index(sym)
            atr = float(feats.at[sym, "atr_pct"])
            if not (math.isfinite(atr) and atr > 0):
                continue
            plan = ts.TradePlan("stop", tick_up(su["trigger"]), atr)
            if plan.risk_pct > MAX_RISK_PCT:
                continue
            picked += 1
            last_sig[sym] = ti
            d = clean[sym]
            pos = own_pos[j].get(all_dates[ti])
            if pos is None:
                continue
            st, oc, exit_pos, void = _simulate(plan, d, pos)
            dates = d.index
            status = "VOID" if void else st.status
            rec = {
                "symbol": sym, "signal_date": all_dates[ti].date().isoformat(), "setup": su["setup"],
                "quality_pct": round(-negq, 1), "atr_pct": round(atr, 4), "risk_pct": round(plan.risk_pct, 4),
                "trigger": plan.trigger, "regime": regime, "status": status,
                "fill_date": "", "exit_date": "", "exit_reason": st.exit_reason, "net": np.nan, "R": np.nan, "peak_R": np.nan,
            }
            if st.fill_n >= 1 and pos + st.fill_n < len(dates):
                rec["fill_date"] = dates[pos + st.fill_n].date().isoformat()
            if st.status == ts.CLOSED and exit_pos is not None and exit_pos < len(dates):
                rec["exit_date"] = dates[exit_pos].date().isoformat()
            if oc is not None and not void:
                rec["net"], rec["R"] = oc["net"], oc["R"]
                rec["peak_R"] = ts.peak_r(plan, st)
            # block the symbol while the trade is unfinished (the live scan does the same)
            if void:
                busy_until[sym] = ti
            elif st.status in (ts.PENDING, ts.OPEN):
                busy_until[sym] = 10**9
            elif st.status == ts.CLOSED and exit_pos is not None:
                busy_until[sym] = date_pos.get(dates[exit_pos], ti)
            trades.append(rec)
    logger.info(f"Backtest ({scan_mode}): {len(trades)} signals over {len(test_idx)} sessions on {len(syms)} stocks")
    return trades


# --------------------------------------------------------------------------- #
def _group_stats(net: np.ndarray, r: np.ndarray) -> dict:
    n = int(len(net))
    if n == 0:
        return {"n": 0}
    wins = net > 0
    losses_sum = float(net[~wins].sum())
    return {
        "n": n, "win_rate": float(wins.mean()), "avg_net": float(net.mean()), "median_net": float(np.median(net)),
        "avg_R": float(np.nanmean(r)), "best_R": float(np.nanmax(r)), "worst_R": float(np.nanmin(r)),
        "profit_factor": float(net[wins].sum() / -losses_sum) if losses_sum < 0 else float("inf"),
    }


def _bucket(q: float) -> str:
    return "50-65" if q < 65 else "65-80" if q < 80 else "80-90" if q < 90 else "90-100"


def summarize_backtest(trades: list[dict]) -> dict:
    """Closed trades only count toward win rate / R; unfilled, still-open and void ones are counted separately."""
    df = pd.DataFrame(trades)
    out: dict = {"signals": int(len(df))}
    if df.empty:
        out.update({"closed": 0, "open": 0, "pending": 0, "unfilled": 0, "void": 0, "all": {"n": 0}})
        return out
    for key, name in (("closed", "CLOSED"), ("open", "OPEN"), ("pending", "PENDING"), ("unfilled", "UNFILLED"), ("void", "VOID")):
        out[key] = int((df["status"] == name).sum())
    closed = df[df["status"] == "CLOSED"].copy()
    out["all"] = _group_stats(closed["net"].to_numpy(float), closed["R"].to_numpy(float)) if len(closed) else {"n": 0}
    if len(closed):
        closed["year"] = closed["signal_date"].str[:4]
        closed["bucket"] = closed["quality_pct"].map(_bucket)
        for label, col in (("by_setup", "setup"), ("by_regime", "regime"), ("by_quality", "bucket"), ("by_year", "year")):
            out[label] = {str(k): _group_stats(g["net"].to_numpy(float), g["R"].to_numpy(float))
                          for k, g in closed.groupby(col)}
        out["avg_hold_sessions"] = None
    return out


def _fmt_stats(s: dict) -> str:
    if not s.get("n"):
        return "n=0"
    pf = "inf" if math.isinf(s["profit_factor"]) else f"{s['profit_factor']:.2f}"
    return (f"n={s['n']}, win rate {s['win_rate']:.0%}, avg {s['avg_net']:+.2%} per trade after costs "
            f"({s['avg_R']:+.2f}R), median {s['median_net']:+.2%}, profit factor {pf}, best {s['best_R']:+.1f}R, worst {s['worst_R']:+.1f}R")


def _table(title: str, groups: dict) -> list[str]:
    if not groups:
        return []
    lines = [f"### {title}", "", "| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |", "| :-- | --: | --: | --: | --: | --: |"]
    for k in sorted(groups):
        s = groups[k]
        pf = "inf" if math.isinf(s["profit_factor"]) else f"{s['profit_factor']:.2f}"
        lines.append(f"| {k} | {s['n']} | {s['win_rate']:.0%} | {s['avg_net']:+.2%} | {s['avg_R']:+.2f} | {pf} |")
    return lines + [""]


def write_backtest_report(trades: list[dict], stats: dict, scan_mode: str, n_stocks: int = 0,
                          out_dir: str = "./reports/output") -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    md_path = f"{out_dir}/backtest_{scan_mode}_{stamp}.md"
    csv_path = f"{out_dir}/backtest_{scan_mode}_{stamp}_trades.csv"
    pd.DataFrame(trades).to_csv(csv_path, index=False)
    lines = [f"# Backtest - {scan_mode} rule, replayed on broker daily bars ({stamp})", "",
             f"{n_stocks} stocks, {stats.get('signals', 0)} signals: {stats.get('closed', 0)} closed, {stats.get('open', 0)} still open, "
             f"{stats.get('pending', 0)} still waiting for the buy-stop, {stats.get('unfilled', 0)} never triggered, "
             f"{stats.get('void', 0)} dropped (suspected split/bonus gap).", "",
             "**Closed trades:** " + _fmt_stats(stats.get("all", {"n": 0})), ""]
    for title, key in (("By setup", "by_setup"), ("By market regime on the signal day", "by_regime"),
                       ("By quality percentile", "by_quality"), ("By year", "by_year")):
        lines += _table(title, stats.get(key, {}))
    lines += ["---",
              "Entry is a buy-stop at the trigger (valid 5 sessions), stop 2.5 ATR below the fill, trailing stop 5 ATR below the highest close, "
              "60-session time limit, 0.35% round-trip costs. Only decisions available at the signal day's close are used. "
              "Daily candles from the broker are not split/bonus adjusted, the quality score here is the 7-factor price-only version, "
              "and a rule that worked on a past window is no promise about the next one.", ""]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return md_path


# --------------------------------------------------------------------------- #
def live_scoreboard_report(ledger: pd.DataFrame, scan_mode: str | None = None,
                           out_dir: str = "./reports/output") -> tuple[str, str]:
    """What the tracked picks actually did, split by quality bucket / setup / mode / market regime."""
    from tracking import outcomes
    df = ledger if scan_mode is None else ledger[ledger["scan_mode"] == scan_mode]
    os.makedirs(out_dir, exist_ok=True)
    path = f"{out_dir}/live_scoreboard.md"
    s = outcomes.scoreboard(df)
    lines = ["# Live scoreboard (picks as sent, followed exactly by the plan, after costs)", "",
             f"{s['total']} picks logged since {s['first_signal'] or 'the start'}: {s['closed']} closed, {s['open']} open, "
             f"{s['pending']} waiting for their buy-stop, {s['unfilled']} never triggered, {s['void']} dropped (split/bonus gap).", ""]
    closed = df[df["status"] == "CLOSED"].copy()
    if len(closed):
        closed["net"] = closed["net"].astype(float)
        closed["R"] = closed["R"].astype(float)
        closed["bucket"] = closed["quality_pct"].astype(float).map(_bucket)
        lines += ["**All closed trades:** " + _fmt_stats(_group_stats(closed["net"].to_numpy(), closed["R"].to_numpy())), ""]
        if len(closed) < 30:
            lines += ["*Fewer than 30 closed trades: too few to judge. Treat every number below as noise until there are 30+.*", ""]
        for title, col in (("By quality percentile (does a higher rank do better?)", "bucket"), ("By setup", "setup"),
                           ("By scan", "scan_mode"), ("By market regime on the signal day", "regime")):
            groups = {str(k): _group_stats(g["net"].to_numpy(), g["R"].to_numpy()) for k, g in closed.groupby(col) if str(k)}
            lines += _table(title, groups)
    else:
        lines += ["No trade has closed yet - results appear here as the tracked trades are stopped out or reach the time limit.", ""]
    open_rows = df[df["status"] == "OPEN"]
    if len(open_rows):
        lines += [f"Open positions: {len(open_rows)}, average mark-to-market {open_rows['R'].astype(float).mean():+.2f}R", ""]
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, text

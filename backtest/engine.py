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
from scanner.pipeline import MAX_RISK_PCT, tick_down, tick_up

logger = logging.getLogger(__name__)

VOID_GAP = 0.35
# RESEARCH: pull-back entries tried for EVERY signal next to the real buy-stop entry, to measure whether waiting for a dip beats buying
# the breakout. (column prefix, ATRs below the trigger the buy-limit sits, sessions the order stays live). They change nothing else.
PULLBACKS = (("pb1", 1.0, 5), ("pb2", 2.0, 10))
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


def _research_columns(feats: pd.DataFrame, sym: str, su: dict, stats_t: dict, breadth: dict) -> dict:
    """
    What the stock had done BEFORE the signal, recorded with every trade so the result can be sliced afterwards (research only; nothing
    here changes a trade). `run_up` is the gain over the 5 sessions before the signal session for a fresh breakout (its own session IS
    the signal) and the full 5-session gain otherwise - exactly what the card's "already spiked" warning measures; `z_run_up` is that
    gain in standard deviations above the average stock's 5-day gain on the signal day.
    """
    def val(col: str) -> float:
        v = feats.at[sym, col]
        return float(v) if pd.notna(v) else float("nan")

    ret5, gain1d = val("ret5"), val("gain1d")
    run_up = fp.gain_before_latest_session(ret5, gain1d) if su["setup"] == "fresh" else ret5
    b50 = breadth.get("breadth50") if breadth else None
    out = {
        "ret5": ret5, "gain1d": gain1d, "run_up": run_up, "z_run_up": fp.z_score(run_up, stats_t, "ret5"),
        "sma200_chg": val("sma200_chg"), "ret252": val("ret252"), "dist_hi": val("dist_hi"), "ema21_x": val("ema21_x"),
        "rv15": val("rv15"), "ext_pct": float(su["ext_pct"]), "breadth50": float(b50) if b50 is not None and math.isfinite(b50) else float("nan"),
    }
    return {k: (round(v, 5) if isinstance(v, float) and math.isfinite(v) else v) for k, v in out.items()}


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
        if int(np.isfinite(M["ret252"][ti][js]).sum()) < 50:
            continue                    # too early in the data: the score's 12-month look-back is not there yet (a long Yahoo history starts here)
        if top_n_liquid and len(js) > top_n_liquid:
            js = js[np.argsort(-M["turn20"][ti][js], kind="stable")[:top_n_liquid]]
            js.sort()
        feats = pd.DataFrame({k: M[k][ti][js] for k in _NEED}, index=[syms[j] for j in js])
        dl_t = pd.Series(DL[ti][js], index=feats.index) if np.isfinite(DL[ti][js]).any() else None
        scored, stats_t = fp.score_universe(feats, dl_t, min_delivery_coverage=config.DELIVERY_MIN_COVERAGE)
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
            rec.update(_research_columns(feats, sym, su, stats_t, breadth))
            for name, atrs, valid in PULLBACKS:
                pb = ts.TradePlan("limit", tick_down(plan.trigger * (1.0 - atrs * atr)), atr, valid_sessions=valid)
                pst, poc, _, pvoid = _simulate(pb, d, pos)
                rec[f"{name}_status"] = "VOID" if pvoid else pst.status
                rec[f"{name}_net"], rec[f"{name}_R"] = (poc["net"], poc["R"]) if (poc is not None and not pvoid) else (np.nan, np.nan)
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


def _pullback_table(df: pd.DataFrame) -> list[str]:
    """
    Buy-stop at the trigger (what the cards say) versus waiting for a pull-back, for the SAME signals: only signals that are finished
    in every entry style (the trade closed, or the order expired / was skipped) are counted, so no style carries unfinished trades or
    a head start. The last column is the average result per signal, counting an order that never filled as 0.
    """
    variants = [("", "Buy-stop at the trigger (what the cards say)")] + [
        (name, f"Buy-limit {atrs:g} ATR below the trigger, live {valid} sessions") for name, atrs, valid in PULLBACKS]

    def col(prefix: str, name: str) -> str:
        return f"{prefix}_{name}" if prefix else name

    status_cols = [col(p, "status") for p, _ in variants]
    if df.empty or any(c not in df.columns for c in status_cols):
        return []
    sub = df[df[status_cols].isin(["CLOSED", "UNFILLED"]).all(axis=1)]
    n_sig = int(len(sub))
    if n_sig == 0:
        return []
    lines = ["| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |",
             "| :-- | --: | --: | --: | --: | --: | --: |"]
    for prefix, label in variants:
        closed = sub[col(prefix, "status")] == "CLOSED"
        net, rr = sub.loc[closed, col(prefix, "net")].to_numpy(float), sub.loc[closed, col(prefix, "R")].to_numpy(float)
        s = _group_stats(net, rr)
        if not s.get("n"):
            lines.append(f"| {label} | {n_sig} | 0% | - | - | - | +0.00% |")
            continue
        pf = "inf" if math.isinf(s["profit_factor"]) else f"{s['profit_factor']:.2f}"
        lines.append(f"| {label} | {n_sig} | {s['n'] / n_sig:.0%} | {s['avg_net']:+.2%} | {s['avg_R']:+.2f} | {pf} | {float(net.sum()) / n_sig:+.2%} |")
    return lines + [""]


def research_tables(trades: list[dict]) -> list[str]:
    """
    Extra tables for the research columns (empty when the trades do not carry them or there are too few closed trades):
    what the stock had done before the signal, whether the 200-day trend was rising, and whether waiting for a pull-back paid.
    """
    df = pd.DataFrame(trades)
    if df.empty or "z_run_up" not in df.columns:
        return []
    closed = df[df["status"] == "CLOSED"].copy()
    if len(closed) < 30:
        return []

    def groups(frame: pd.DataFrame, parts: list[tuple[str, pd.Series]]) -> dict:
        out = {}
        for label, mask in parts:
            sub = frame[mask.reindex(frame.index, fill_value=False)]
            if len(sub):
                out[label] = _group_stats(sub["net"].to_numpy(float), sub["R"].to_numpy(float))
        return out

    z, gain = closed["z_run_up"], closed["run_up"]
    spiked_now = (z >= config.SPIKE_5D_Z) & (gain >= config.SPIKE_5D_MIN)
    spiked_old = (z >= 1.28) & (gain >= 0.05)
    t = closed["sma200_chg"]
    rising = t >= config.TREND_200D_RISING_MIN
    lines = ["## Research: does it matter what the stock did before the signal?", "",
             "z = the stock's 5-day gain before the signal, in standard deviations above the average stock's 5-day gain that day "
             "(a fresh breakout's own day is left out).", ""]
    lines += _table("Run-up before the signal (z)", groups(closed, [
        ("a) below 0 (weaker than the average stock)", z < 0), ("b) 0 to 0.5", (z >= 0) & (z < 0.5)), ("c) 0.5 to 1", (z >= 0.5) & (z < 1)),
        ("d) 1 to 1.5", (z >= 1) & (z < 1.5)), ("e) 1.5 to 2.5", (z >= 1.5) & (z < 2.5)), ("f) 2.5 and up", z >= 2.5)]))
    lines += _table(f"The card's warning: gain at least {config.SPIKE_5D_MIN:.0%} and z at least {config.SPIKE_5D_Z:g} (current) versus 5% and 1.28 (before)", groups(closed, [
        ("a) flagged by the current cut-offs", spiked_now), ("b) not flagged by the current cut-offs", ~spiked_now),
        ("c) flagged by the old cut-offs", spiked_old), ("d) not flagged by the old cut-offs", ~spiked_old)]))
    lines += _table(f"200-day trend (change of the 200-day average over 20 sessions; 'rising' = at least {config.TREND_200D_RISING_MIN:.1%})", groups(closed, [
        ("a) falling (below 0)", t < 0), ("b) 0 to 0.5%", (t >= 0) & (t < 0.005)), ("c) 0.5% to 1%", (t >= 0.005) & (t < 0.01)),
        ("d) 1% to 2%", (t >= 0.01) & (t < 0.02)), ("e) 2% and up", t >= 0.02)]))
    lines += _table("Spike and trend together", groups(closed, [
        ("a) rising trend, not spiked", rising & ~spiked_now), ("b) rising trend, already spiked", rising & spiked_now),
        ("c) trend not rising, not spiked", ~rising & ~spiked_now), ("d) trend not rising, already spiked", ~rising & spiked_now)]))
    lines += _table("Fresh breakout or about to break out, spiked or not", groups(closed, [
        ("a) fresh, not spiked", (closed["setup"] == "fresh") & ~spiked_now), ("b) fresh, already spiked", (closed["setup"] == "fresh") & spiked_now),
        ("c) about to break, not spiked", (closed["setup"] == "primed") & ~spiked_now),
        ("d) about to break, already spiked", (closed["setup"] == "primed") & spiked_now)]))
    if "pb1_status" in df.columns:
        flag_all = (df["z_run_up"] >= config.SPIKE_5D_Z) & (df["run_up"] >= config.SPIKE_5D_MIN)
        for title, part in (("Waiting for a pull-back, for the stocks the card flags as already spiked", df[flag_all]),
                            ("Waiting for a pull-back, for the stocks that were not flagged", df[~flag_all])):
            table = _pullback_table(part)
            if table:
                lines += [f"### {title}", ""] + table
    return lines


def write_backtest_report(trades: list[dict], stats: dict, scan_mode: str, n_stocks: int = 0,
                          out_dir: str = "./reports/output", tag: str = "", data_note: str = "") -> str:
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    name = f"backtest_{scan_mode}_{tag}_{stamp}" if tag else f"backtest_{scan_mode}_{stamp}"
    md_path = f"{out_dir}/{name}.md"
    csv_path = f"{out_dir}/{name}_trades.csv"
    pd.DataFrame(trades).to_csv(csv_path, index=False)
    lines = [f"# Backtest - {scan_mode} rule, replayed on {'Yahoo' if tag == 'yahoo' else 'broker'} daily bars ({stamp})", "",
             *([data_note, ""] if data_note else []),
             f"{n_stocks} stocks, {stats.get('signals', 0)} signals: {stats.get('closed', 0)} closed, {stats.get('open', 0)} still open, "
             f"{stats.get('pending', 0)} still waiting for the buy-stop, {stats.get('unfilled', 0)} never triggered, "
             f"{stats.get('void', 0)} dropped (suspected split/bonus gap).", "",
             "**Closed trades:** " + _fmt_stats(stats.get("all", {"n": 0})), ""]
    for title, key in (("By setup", "by_setup"), ("By market regime on the signal day", "by_regime"),
                       ("By quality percentile", "by_quality"), ("By year", "by_year")):
        lines += _table(title, stats.get(key, {}))
    lines += research_tables(trades)
    lines += ["---",
              "Entry is a buy-stop at the trigger (valid 5 sessions), stop 2.5 ATR below the fill, trailing stop 5 ATR below the highest close, "
              "60-session time limit, 0.35% round-trip costs. Only decisions available at the signal day's close are used. "
              "Daily candles from the broker are not split/bonus adjusted, the quality score here is the 7-factor price-only version, "
              "and a rule that worked on a past window is no promise about the next one.", ""]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return md_path


# --------------------------------------------------------------------------- #
def _signal_tables(closed: pd.DataFrame) -> list[str]:
    """
    The "strong uptrend" tag and the "already spiked" warning judged on REAL closed trades. Picks logged since 2026-10-31 carry them; older
    ones have the columns blank ("not recorded") and are left out, never counted as "no". Both started as hints from the 10-year replay, so
    this is where they get confirmed or rejected: a group of fewer than 30 closed trades is too small to say anything.
    """
    if not {"strong_trend", "spiked"} <= set(closed.columns):
        return []
    strong = pd.to_numeric(closed["strong_trend"], errors="coerce")
    spiked = pd.to_numeric(closed["spiked"], errors="coerce")
    if not (strong.notna().any() or spiked.notna().any()):
        return []

    def groups(flag: pd.Series, parts: list[tuple[str, bool]]) -> dict:
        out = {}
        for label, want in parts:
            sub = closed[flag == (1 if want else 0)]
            if len(sub):
                out[label] = _group_stats(sub["net"].to_numpy(float), sub["R"].to_numpy(float))
        return out

    lines = ["## Do the card's new hints hold up on real trades?", ""]
    by_trend = groups(strong, [("a) 💪 strong uptrend (200-day average up 5%+ in 20 sessions)", True), ("b) all other picks", False)])
    by_spike = groups(spiked, [("a) warned: already spiked", True), ("b) not warned", False)])
    lines += _table("By 200-day trend when the pick was made", by_trend)
    lines += _table("By run-up before the signal", by_spike)
    missing = int((strong.isna() & spiked.isna()).sum())
    if missing:
        lines += [f"*{missing} of the {len(closed)} closed trades were logged before these were recorded and are left out of the two tables above.*", ""]
    small = [k for g in (by_trend, by_spike) for k, s in g.items() if s["n"] < 30]
    if small:
        lines += ["*A group with fewer than 30 closed trades is too small to judge, so read these as noise for now. Until then the only evidence is the "
                  "10-year replay of 30 Oct 2026 (reports/evidence/README.md, section 7): strong-uptrend stocks made +3.95% per card after costs against "
                  "+1.29% for the rest, but the 5% line was chosen after seeing the data and the gap was smaller in 2022-26. Spiked stocks did not do "
                  "measurably worse or better there.*", ""]
    return lines


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
        lines += _signal_tables(closed)
    else:
        lines += ["No trade has closed yet - results appear here as the tracked trades are stopped out or reach the time limit.", ""]
    open_rows = df[df["status"] == "OPEN"]
    if len(open_rows):
        lines += [f"Open positions: {len(open_rows)}, average mark-to-market {open_rows['R'].astype(float).mean():+.2f}R", ""]
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path, text

"""
From a universe of daily bars to a short list of ranked, ready-to-trade picks.

This is the whole selection logic in one place, with no network access and no
file writes, so the live scan, the intraday watchlist re-check and the tests
all run exactly the same code.

THE RULE (every part of it was validated on 7 years of real NSE data - see
reports/evidence/):

  1. Liquid stocks only: price >= MIN_PRICE and 20-day average turnover >=
     MIN_AVG_DOLLAR_VOLUME. There is NO upper price cap.
  2. The stock must be in a SETUP right now
        FRESH  - first/continuing close above its prior 50-day high on heavy
                 volume (see scanner.footprint.detect_setup)
        PRIMED - within 3% below that high
  3. Its QUALITY score (scanner.footprint) must be in the top half of the
     liquid universe that day.
  4. Ranked by quality; FRESH setups listed before PRIMED ones.
  5. Every pick comes with a complete trade plan (entry, stop, trailing stop,
     size at 1% risk) built on the same numbers the research tested.

Entry style depends on whether the market is open when the scan runs:
  * market open  -> FRESH breakouts are "buy now near the close" (entry at the
                    current price); PRIMED stocks get a buy-stop for the next
                    sessions
  * market closed-> both get a buy-stop for the next sessions
"""
from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import pandas as pd

import config
from scanner import footprint as fp
from scanner import session
from scanner import tradesim as ts

logger = logging.getLogger(__name__)

TICK = 0.05
MAX_RISK_PCT = 0.30          # a stop further than 30% away means a pathological stock: never picked


def tick_up(x: float, tick: float = TICK) -> float:
    return round(math.ceil(x / tick - 1e-9) * tick, 2)


def tick_down(x: float, tick: float = TICK) -> float:
    return round(math.floor(x / tick + 1e-9) * tick, 2)


def fmt_price(x: float | None) -> str:
    if x is None or not math.isfinite(x):
        return "-"
    return f"{x:,.1f}" if x >= 1000 else f"{x:,.2f}"


# --------------------------------------------------------------------------- #
@dataclass
class Pick:
    symbol: str
    setup: str                        # 'fresh' | 'primed'
    stage_label: str
    scan_mode: str
    signal_date: str                  # ISO date of the bar the signal was computed on
    signal_time_ist: str
    live_bar: bool                    # True when the signal bar was still forming (market open)
    entry_mode: str                   # 'close' (buy now) | 'stop' (buy-stop order)
    price: float                      # last price
    entry_price: float                # close mode: the price to pay; stop mode: the buy-stop trigger
    max_entry: float | None           # stop mode: skip the trade if it opens above this
    stop: float
    risk_pct: float
    atr_pct: float
    trail_pct: float
    valid_sessions: int
    max_hold: int
    r1: float
    r2: float
    r3: float
    quality_pct: float
    score: float
    grade: str
    pivot: float                      # the prior 50-day high (the level that matters)
    ext_pct: float                    # price vs that level (+ = above)
    gain_pct: float
    vol_ratio: float
    close_pos: float
    dist_hi_pct: float
    ret252_pct: float
    dlv20: float | None
    days_since_break: int | None
    first_break: bool
    sector: str | None
    qty: int
    position_value: float
    size_note: str
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    def plan(self) -> ts.TradePlan:
        return ts.TradePlan(entry_mode=self.entry_mode, trigger=self.entry_price, atr_pct=self.atr_pct,
                            valid_sessions=self.valid_sessions, max_hold=self.max_hold)

    def to_row(self) -> dict:
        """Flat dict for the scan CSV and the ledger."""
        return {
            "symbol": self.symbol, "setup": self.setup, "stage": self.stage_label, "grade": self.grade,
            "quality_pct": round(self.quality_pct, 1), "entry_mode": self.entry_mode,
            "price": round(self.price, 2), "entry_price": round(self.entry_price, 2),
            "max_entry": round(self.max_entry, 2) if self.max_entry else None,
            "stop_loss": round(self.stop, 2), "risk_pct": round(self.risk_pct, 4),
            "trail_pct": round(self.trail_pct, 4), "atr_pct": round(self.atr_pct, 4),
            "plus_1R": round(self.r1, 2), "plus_2R": round(self.r2, 2), "plus_3R": round(self.r3, 2),
            "pivot_50d_high": round(self.pivot, 2), "ext_pct": round(self.ext_pct, 4),
            "gain_pct": round(self.gain_pct, 4), "vol_ratio": round(self.vol_ratio, 2),
            "dist_52w_high_pct": round(self.dist_hi_pct, 4), "ret_12m_pct": round(self.ret252_pct, 4)
            if math.isfinite(self.ret252_pct) else None,
            "delivery_20d_pct": round(self.dlv20, 1) if self.dlv20 is not None and math.isfinite(self.dlv20) else None,
            "days_since_break": self.days_since_break, "first_break": self.first_break,
            "sector": self.sector, "qty": self.qty, "position_value": round(self.position_value, 0),
            "valid_sessions": self.valid_sessions, "signal_date": self.signal_date,
            "signal_time_ist": self.signal_time_ist, "scan_mode": self.scan_mode,
        }


@dataclass
class SelectionResult:
    picks: list[Pick]
    repeats: list[tuple[str, str]]            # (symbol, setup) that qualified but are already on the list
    funnel: dict
    breadth: dict
    regime: str
    stats: dict | None
    feats: pd.DataFrame                        # universe features + scores (watchlist source)
    notes: list[str]
    ref_date: date | None
    live: bool
    vol_fraction: float
    used_delivery: bool


# --------------------------------------------------------------------------- #
def _bar_date(df: pd.DataFrame) -> date | None:
    try:
        ts_ = df.index.max()
        return ts_.date() if hasattr(ts_, "date") else pd.Timestamp(ts_).date()
    except Exception:
        return None


def _ref_date(dates: list[date]) -> date | None:
    """Newest bar date that a reasonable share of the universe has (ignores a stray odd date)."""
    if not dates:
        return None
    counts = Counter(dates)
    need = max(3, int(0.10 * len(dates))) if len(dates) >= 30 else 1
    for d in sorted(counts, reverse=True):
        if counts[d] >= need:
            return d
    return max(counts)


def reference_date(bars: dict) -> date | None:
    """The session date most of `bars` end on (the signal date of a scan run on them)."""
    dates = []
    for df in bars.values():
        if df is not None and len(df) > 0:
            d = _bar_date(df)
            if d is not None:
                dates.append(d)
    return _ref_date(dates)


def _stage_label(setup: str, f: dict, su: dict) -> str:
    if setup == "fresh":
        if f.get("first_break"):
            return "Fresh breakout (first close above the 50-day high)"
        dsb = f.get("days_since_break")
        return f"Breakout continuing (day {int(dsb) + 1} above the 50-day high)" if dsb is not None else "Breakout continuing"
    if su.get("above_pivot"):
        return "At the breakout level (volume not confirmed yet)"
    gap = 1.0 - f["close"] / f["piv_incl"]
    return f"About to break out ({gap:.1%} below the 50-day high)"


def _size(entry: float, risk_pct: float, size_factor: float) -> tuple[int, float, str]:
    """Shares for CAPITAL_INR at RISK_PER_TRADE_PCT risk (x size_factor), capped at MAX_POSITION_PCT of capital."""
    capital = config.CAPITAL_INR
    risk_amt = capital * (config.RISK_PER_TRADE_PCT / 100.0) * size_factor
    per_share = entry * risk_pct
    if per_share <= 0:
        return 0, 0.0, ""
    qty_risk = int(risk_amt // per_share)
    qty_cap = int((capital * config.MAX_POSITION_PCT / 100.0) // entry)
    qty = max(0, min(qty_risk, qty_cap))
    note = ""
    if qty == 0:
        note = f"one share is more than the {config.MAX_POSITION_PCT:.0f}% position cap at this capital"
    elif qty == qty_cap and qty_cap < qty_risk:
        note = f"capped at {config.MAX_POSITION_PCT:.0f}% of capital"
    return qty, qty * entry, note


def _reasons_and_cautions(setup: str, f: dict, su: dict, row: pd.Series, dlv: float | None,
                          live: bool, regime: str, breadth50: float | None) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    cautions: list[str] = []
    piv, piv_incl = f["piv50"], f["piv_incl"]
    if setup == "fresh":
        verb = "Trading" if live else "Closed"
        reasons.append(
            f"{verb} {su['ext_pct']:+.1%} above its 50-day high (₹{fmt_price(piv)}) on {f['rv15']:.1f}x normal volume, "
            f"up {f['gain1d']:.1%} on the day"
        )
    else:
        gap = 1.0 - f["close"] / piv_incl
        if su.get("above_pivot"):
            reasons.append(f"Sitting {su['ext_pct']:+.1%} around its 50-day high (₹{fmt_price(piv)}) - volume has not confirmed a breakout yet")
        else:
            reasons.append(f"{gap:.1%} below its 50-day high (₹{fmt_price(piv_incl)}) - coiled just under resistance")
    reasons += [r[0].upper() + r[1:] for r in fp.top_reasons(row, k=3)]
    if dlv is not None and math.isfinite(dlv):
        reasons.append(f"20-day average delivery ~{dlv:.0f}% of traded shares")

    cautions += [w[0].upper() + w[1:] for w in fp.weak_points(row, k=2)]
    if setup == "fresh" and su["ext_pct"] > 0.03:
        cautions.append(f"Already {su['ext_pct']:.1%} above the breakout level - a pullback to it is common")
    if f.get("gain1d", 0) and f["gain1d"] > 0.08:
        cautions.append("Very large one-day move - pullbacks after such days are common")
    if f.get("atr_pct", 0) and f["atr_pct"] > 0.05:
        cautions.append(f"Volatile stock (daily range ~{f['atr_pct']:.1%}) - the stop is wide, so size is small")
    if live and setup == "fresh":
        cautions.append("Price is live - only buy if it still holds above the breakout level near the close")
    if regime == "weak" and breadth50 is not None and math.isfinite(breadth50):
        cautions.append(f"Weak market: only {breadth50:.0%} of stocks are above their 50-day average - use half size")
    return reasons, cautions


# --------------------------------------------------------------------------- #
def select_picks(
    scan_mode: str,
    bars: dict[str, pd.DataFrame],
    now: datetime | None = None,
    dlv20: pd.Series | None = None,
    sector_map: dict | None = None,
    stats: dict | None = None,
    exclude: set[str] | None = None,
    max_picks: int | None = None,
    skip_symbols: set[str] | None = None,
    breadth_hint: dict | None = None,
) -> SelectionResult:
    """
    bars          {symbol: daily OHLCV}, newest bar last (the newest may be today's still-forming bar)
    dlv20         optional Series (index = symbol) of the lagged 20-day average delivery %
    stats         stored universe statistics from the evening run; given -> the bars are a WATCHLIST
                  and are scored against the whole universe's yardstick instead of each other
    exclude       symbols already on the list within the cool-down (reported as repeats, not re-picked)
    skip_symbols  never candidates (e.g. the benchmark ETF)
    breadth_hint  stored breadth from the evening run (used when `stats` is given, because a
                  100-name watchlist is not a representative sample of the market)
    """
    now = now or session.now_ist()
    exclude = exclude or set()
    skip_symbols = skip_symbols or set()
    max_picks = max_picks or config.MAX_PICKS.get(scan_mode, 10)
    funnel = {"loaded": 0, "stale": 0, "too_short": 0, "illiquid": 0, "scored": 0, "fresh": 0, "primed": 0,
              "passed_quality": 0, "repeats": 0, "picks": 0}
    notes: list[str] = []

    usable = {s: d for s, d in bars.items() if s not in skip_symbols and d is not None and len(d) > 0}
    funnel["loaded"] = len(usable)
    last_dates = {s: _bar_date(d) for s, d in usable.items()}
    ref_date = _ref_date([d for d in last_dates.values() if d is not None])

    live = bool(ref_date) and session.session_state(now) == "open" and ref_date == session.ist_date(now)
    vol_fraction = session.volume_fraction(now) if live else 1.0

    rows: dict[str, dict] = {}
    for sym, df in usable.items():
        ld = last_dates[sym]
        if ref_date is not None and (ld is None or ld < ref_date):
            funnel["stale"] += 1
            continue
        frac = session.effective_volume_fraction(ld, now)
        f = fp.compute_features(df, frac)
        if f is None:
            funnel["too_short"] += 1
            continue
        if not (f["close"] >= config.MIN_PRICE) or not (f["turn20"] >= config.MIN_AVG_DOLLAR_VOLUME):
            funnel["illiquid"] += 1
            continue
        rows[sym] = f

    feats = pd.DataFrame.from_dict(rows, orient="index") if rows else pd.DataFrame()
    if feats.empty:
        return SelectionResult([], [], funnel, {"breadth50": float("nan"), "breadth200": float("nan"), "n": 0},
                               "unknown", stats, feats, notes, ref_date, live, vol_fraction, False)

    # ---- regime ---------------------------------------------------------------
    if stats is not None and breadth_hint:
        breadth = dict(breadth_hint)
    else:
        breadth = fp.market_breadth(feats)
    regime = fp.regime_label(breadth.get("breadth50"))
    size_factor = 0.5 if regime == "weak" else 1.0

    # ---- quality score ----------------------------------------------------------
    scored, out_stats = fp.score_universe(feats, dlv20, stats=stats, min_delivery_coverage=config.DELIVERY_MIN_COVERAGE)
    used_delivery = out_stats.get("spec") == "dlv"
    funnel["scored"] = int(scored["score"].notna().sum())
    feats = feats.join(scored)

    # ---- candidates -------------------------------------------------------------
    candidates: list[tuple[float, str, str, dict]] = []
    repeats: list[tuple[str, str]] = []
    for sym, f in rows.items():
        su = fp.detect_setup(f)
        setup = su["setup"]
        if setup is None:
            continue
        funnel[setup] += 1
        q = scored.at[sym, "quality_pct"]
        if q is None or not math.isfinite(q) or q < config.QUALITY_GATE_PCT:
            continue
        funnel["passed_quality"] += 1
        if sym in exclude:
            funnel["repeats"] += 1
            repeats.append((sym, setup))
            continue
        candidates.append((q, sym, setup, su))

    # FRESH before PRIMED, then best quality first
    candidates.sort(key=lambda t: (0 if t[2] == "fresh" else 1, -t[0], t[1]))

    sig_date = ref_date.isoformat() if ref_date else ""
    sig_time = now.astimezone(session.IST).strftime("%H:%M") if live else ""
    picks: list[Pick] = []
    for q, sym, setup, su in candidates:
        if len(picks) >= max_picks:
            break
        f = rows[sym]
        atr = f["atr_pct"]
        if not (isinstance(atr, float) and math.isfinite(atr) and atr > 0):
            continue
        plan_probe = ts.TradePlan("stop", 1.0, atr)
        if plan_probe.risk_pct > MAX_RISK_PCT:
            continue

        if live and setup == "fresh":
            entry_mode, entry = "close", tick_up(f["close"])
            max_entry = None
        else:
            entry_mode, entry = "stop", tick_up(su["trigger"])
            max_entry = tick_down(entry * (1.0 + config.MAX_CHASE_PCT))
        risk = plan_probe.risk_pct
        stop = tick_down(entry * (1.0 - risk))
        qty, value, size_note = _size(entry, risk, size_factor)
        if size_factor < 1.0:
            size_note = ("half size: weak market" + ("; " + size_note if size_note else ""))
        row = scored.loc[sym]
        dlv = None
        if dlv20 is not None and sym in dlv20.index and pd.notna(dlv20[sym]):
            dlv = float(dlv20[sym])
        reasons, cautions = _reasons_and_cautions(setup, f, su, row, dlv, live, regime, breadth.get("breadth50"))
        picks.append(Pick(
            symbol=sym, setup=setup, stage_label=_stage_label(setup, f, su), scan_mode=scan_mode,
            signal_date=sig_date, signal_time_ist=sig_time, live_bar=live, entry_mode=entry_mode,
            price=f["close"], entry_price=entry, max_entry=max_entry, stop=stop, risk_pct=risk, atr_pct=atr,
            trail_pct=config.TRAIL_ATR_MULT * atr, valid_sessions=config.ENTRY_VALID_SESSIONS,
            max_hold=config.MAX_HOLD_SESSIONS,
            r1=tick_up(entry * (1 + risk)), r2=tick_up(entry * (1 + 2 * risk)), r3=tick_up(entry * (1 + 3 * risk)),
            quality_pct=float(q), score=float(row["score"]), grade=fp.grade_for_quality(q),
            pivot=f["piv50"], ext_pct=su["ext_pct"], gain_pct=f["gain1d"], vol_ratio=f["rv15"],
            close_pos=f["close_pos"], dist_hi_pct=f["dist_hi"], ret252_pct=f["ret252"], dlv20=dlv,
            days_since_break=f.get("days_since_break"), first_break=bool(f.get("first_break")),
            sector=(sector_map or {}).get(sym), qty=qty, position_value=value, size_note=size_note,
            reasons=reasons, cautions=cautions,
        ))
    funnel["picks"] = len(picks)

    # ---- sector concentration (information only: it never changes the ranking) -----
    sectors = Counter(p.sector for p in picks if p.sector)
    for sec, n in sectors.items():
        if n >= 3:
            notes.append(f"{n} of the {len(picks)} picks are in the same sector ({sec}) - they tend to move together, "
                         f"so treat them as ONE position when sizing")
    return SelectionResult(picks, repeats, funnel, breadth, regime, out_stats, feats, notes,
                           ref_date, live, vol_fraction, used_delivery)


# --------------------------------------------------------------------------- #
def build_watchlist(result: SelectionResult, dlv20: pd.Series | None = None,
                    max_n: int | None = None) -> dict | None:
    """
    The short list the afternoon BTST run re-checks: stocks of decent quality
    that are within striking distance of their 50-day high (or just above it).
    Built from a FULL-universe result (the evening run). None if there is nothing to save.
    """
    feats = result.feats
    if feats is None or feats.empty or not result.stats or not result.stats.get("score_q"):
        return None
    max_n = max_n or config.WATCHLIST_SIZE
    lo, hi = config.WATCHLIST_BAND
    dist = feats["close"] / feats["piv_incl"] - 1.0
    ok = (feats["quality_pct"] >= config.WATCHLIST_MIN_QUALITY) & (dist >= lo) & (dist <= hi)
    cand = feats[ok].copy()
    if cand.empty:
        return None
    cand["_dist"] = (cand["close"] / cand["piv_incl"] - 1.0).abs()
    cand = cand.sort_values(["_dist", "quality_pct"], ascending=[True, False]).head(max_n)
    syms = [str(s) for s in cand.index]
    d = {}
    if dlv20 is not None:
        for s in syms:
            if s in dlv20.index and pd.notna(dlv20[s]):
                d[s] = round(float(dlv20[s]), 2)
    return {
        "version": 1,
        "as_of": result.ref_date.isoformat() if result.ref_date else "",
        "built_ist": session.now_ist().strftime("%Y-%m-%d %H:%M"),
        "symbols": syms,
        "stats": result.stats,
        "breadth": {k: (None if (isinstance(v, float) and not math.isfinite(v)) else v) for k, v in result.breadth.items()},
        "dlv20": d,
    }

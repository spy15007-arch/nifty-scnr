"""
Overall market direction readout - "which way is the market leaning?".

ADDED 2026-10-17. Index-options strike alerts were dropped (by the time a
scheduled scan reports, the strike has already moved), but a read on the
overall market direction is still wanted: on demand (`python main.py
market`, the "Market Direction (Manual)" workflow) and as a one-line
header on the scheduled BTST/Swing Telegram messages.

WHAT IT IS - a transparent scorecard of trend and backdrop, not a
forecast. Six checks, each scoring +1 (supports longs), -1 (against) or
0 (neutral):

    Today   Nifty's move in the latest session      >= +0.3% / <= -0.3%
    20d     Nifty vs its 20-day EMA                 beyond +/-0.3%
    50d     Nifty vs its 50-day EMA                 beyond +/-0.5% (the same
            dead zone compute_index_trend_bias uses for the scan multiplier)
    200d    Nifty vs its 200-day average            beyond +/-0.5%
            (skipped when fewer than 200 bars are available)
    RSI     Nifty's 14-day RSI (Wilder)             >= 55 / <= 45
    Global  overnight US / Asia / crude / dollar    the existing
            compute_market_regime_bias (supportive / neutral / hostile)

The total is divided by the number of checks that were actually
available, and that ratio picks the label - so a missing input (say
Yahoo is down) shrinks the denominator instead of silently counting as
bearish.

Bank Nifty and market breadth are shown for context but deliberately NOT
scored. That keeps the label identical whether it comes from the manual
run (which has Bank Nifty but no stock universe) or from a scheduled scan
(which has the stock universe but no Bank Nifty).

The scoring and formatting functions are pure (no network, no clock
unless `today` is omitted), so they can be tested on synthetic data. The
only network code is fetch_index_history() at the bottom, used by the
manual command alone - the scheduled scans reuse the benchmark bars and
global snapshot they have already fetched.
"""
from __future__ import annotations

import html
import logging
import math
import numbers
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from scanner.global_cues import compute_market_regime_bias

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# --- scorecard thresholds (fractions, not percent) ---
TODAY_MOVE = 0.003      # a latest-session move this big counts as up/down
EMA20_BAND = 0.003      # dead zone around the 20-day EMA
EMA50_BAND = 0.005      # dead zone around the 50-day EMA (== compute_index_trend_bias)
SMA200_BAND = 0.005     # dead zone around the 200-day average, so a 0.03% wiggle can't flip the check
RSI_BULLISH = 55.0
RSI_BEARISH = 45.0
MIN_BARS = 22           # below this, no index metric is meaningful
EMA50_MIN_BARS = 55     # ema_period + 5 - the same minimum compute_index_trend_bias requires
SMA200_MIN_BARS = 200

# Where each index's daily bars come from, tried in order.
#  1. The real index through the Angel One client's own alias table. That
#     table wants the RAW tag ("NIFTY"), not the pre-mapped "Nifty 50" -
#     main.py's old options scan passed the mapped name, which the client
#     then looked up as "Nifty 50-EQ" and could never find, so every index
#     fetch failed.
#  2. The ETF proxies, the same fetch the scans already make every day.
#  3. Yahoo Finance, independent of Angel One entirely.
INDEX_SOURCES = {
    "Nifty 50": [("angel", "NIFTY"), ("angel", "NIFTYBEES"), ("yahoo", "^NSEI")],
    "Bank Nifty": [("angel", "BANKNIFTY"), ("angel", "BANKBEES"), ("yahoo", "^NSEBANK")],
}
TRUE_INDEX_SOURCES = {"NIFTY", "BANKNIFTY", "^NSEI", "^NSEBANK"}
SOURCE_NOTES = {
    "NIFTY": "Angel One index feed",
    "BANKNIFTY": "Angel One index feed",
    "NIFTYBEES": "NIFTYBEES ETF proxy",
    "BANKBEES": "BANKBEES ETF proxy",
    "^NSEI": "Yahoo Finance",
    "^NSEBANK": "Yahoo Finance",
}

STANCES = {
    "Bullish": "Trend and backdrop both support new long setups.",
    "Mildly bullish": "Backdrop leans supportive - the better-graded picks have a tailwind.",
    "Sideways / mixed": "No clear direction - breakouts are likelier to stall, so be selective.",
    "Mildly bearish": "Backdrop leans against longs - stick to the highest-graded picks and keep risk tight.",
    "Bearish": "Market is under pressure - breakouts tend to fail more often in this backdrop; consider sitting BTST out.",
    "Unclear": "Not enough index data for a read.",
}


@dataclass
class IndexReading:
    name: str
    source: str
    as_of: str                     # date of the latest bar, "" if the index is not dates
    last_close: float
    show_level: bool               # True for a real index, False for an ETF proxy (price != index level)
    day_change: float | None
    vs_ema20: float | None
    vs_ema50: float | None
    vs_sma200: float | None
    rsi14: float | None
    change_20d: float | None
    below_52w_high: float | None


@dataclass
class Factor:
    key: str                       # today / ema20 / ema50 / sma200 / rsi / global
    label: str
    shown: str                     # the number or word displayed next to the mark
    score: int                     # +1 / 0 / -1


@dataclass
class MarketDirection:
    label: str
    emoji: str
    stance: str
    score: int
    n_factors: int
    factors: list[Factor]
    nifty: IndexReading | None
    bank: IndexReading | None
    global_snapshot: dict = field(default_factory=dict)
    breadth: dict | None = None
    today: str = ""


# ----------------------------------------------------------------------
# scoring (pure)
# ----------------------------------------------------------------------
def _clean_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """A cleaned COPY (never mutates the caller's frame - the scans reuse their benchmark)."""
    if df is None or not hasattr(df, "columns") or "close" not in df.columns:
        return None
    out = df[~df.index.duplicated(keep="last")]
    if not out.index.is_monotonic_increasing:
        out = out.sort_index()
    out = out.assign(close=pd.to_numeric(out["close"], errors="coerce"))
    out = out[out["close"] > 0]                      # also drops NaN
    return out if len(out) else None


def _as_of(index) -> str:
    if isinstance(index, pd.DatetimeIndex) and len(index):
        return index[-1].strftime("%Y-%m-%d")
    return ""


def _wilder_rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    avg_gain = delta.clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().iloc[-1]
    avg_loss = (-delta).clip(lower=0.0).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().iloc[-1]
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if avg_loss == 0:
        return 50.0 if avg_gain == 0 else 100.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def read_index(df: pd.DataFrame | None, name: str, source: str = "") -> IndexReading | None:
    """Trend facts for one index from its daily bars. None when there is too little data."""
    frame = _clean_frame(df)
    if frame is None or len(frame) < MIN_BARS:
        return None
    close = frame["close"]
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close) >= EMA50_MIN_BARS else None
    sma200 = float(close.tail(200).mean()) if len(close) >= SMA200_MIN_BARS else None

    below_high = None
    if len(close) >= SMA200_MIN_BARS:
        highs = pd.to_numeric(frame["high"], errors="coerce") if "high" in frame.columns else close
        top = float(highs.tail(252).max())
        if np.isfinite(top) and top > 0:
            below_high = max(0.0, 1.0 - last / top)

    return IndexReading(
        name=name,
        source=source,
        as_of=_as_of(frame.index),
        last_close=last,
        show_level=source in TRUE_INDEX_SOURCES,
        day_change=last / prev - 1.0,
        vs_ema20=last / ema20 - 1.0,
        vs_ema50=(last / ema50 - 1.0) if ema50 else None,
        vs_sma200=(last / sma200 - 1.0) if sma200 else None,
        rsi14=_wilder_rsi(close),
        change_20d=(last / float(close.iloc[-21]) - 1.0) if len(close) >= 21 else None,
        below_52w_high=below_high,
    )


def _band_score(value: float | None, band: float) -> int | None:
    if value is None:
        return None
    if value >= band:
        return 1
    if value <= -band:
        return -1
    return 0


def _finite_snapshot(snapshot: dict | None) -> dict:
    """
    Keeps only usable numbers. Yahoo sometimes returns a NaN close for one
    ticker, and a single NaN turns compute_market_regime_bias's composite into
    NaN - which silently reads as "neutral" (the committed scan summaries show
    this on roughly 6% of days). Dropping just that ticker lets the others count.
    """
    out = {}
    for key, value in (snapshot or {}).items():
        if isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value):
            out[key] = float(value)
    return out


def _pct(value: float | None, digits: int = 1) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    if round(value * 100, digits) == 0:          # avoid "-0.0%" / "+0.0%"
        return f"{0:.{digits}f}%"
    return f"{value:+.{digits}%}"


def build_factors(nifty: IndexReading | None, global_snapshot: dict, today: str) -> list[Factor]:
    factors: list[Factor] = []

    def add(key: str, label: str, shown: str, score: int | None):
        if score is not None:
            factors.append(Factor(key, label, shown, score))

    if nifty is not None:
        add("today", "Today" if nifty.as_of == today else "Last session",
            _pct(nifty.day_change), _band_score(nifty.day_change, TODAY_MOVE))
        add("ema20", "20d EMA", _pct(nifty.vs_ema20), _band_score(nifty.vs_ema20, EMA20_BAND))
        add("ema50", "50d EMA", _pct(nifty.vs_ema50), _band_score(nifty.vs_ema50, EMA50_BAND))
        add("sma200", "200d avg", _pct(nifty.vs_sma200), _band_score(nifty.vs_sma200, SMA200_BAND))
        if nifty.rsi14 is not None:
            add("rsi", "RSI", f"{nifty.rsi14:.0f}",
                1 if nifty.rsi14 >= RSI_BULLISH else -1 if nifty.rsi14 <= RSI_BEARISH else 0)

    if global_snapshot:
        try:
            g_label, g_mult = compute_market_regime_bias(global_snapshot)
            add("global", "Global", g_label, 1 if g_mult > 1.0 else -1 if g_mult < 1.0 else 0)
        except Exception:
            logger.warning("Global cues could not be scored for the market direction", exc_info=True)
    return factors


def _verdict(score: int, n_factors: int) -> tuple[str, str]:
    """(label, emoji). The ratio, not the raw total, so a missing check can't skew the label."""
    if n_factors < 3:
        return "Unclear", "⚪"
    ratio = score / n_factors
    if ratio >= 0.5:
        return "Bullish", "🟢"
    if ratio >= 0.3:
        return "Mildly bullish", "🟢"
    if ratio > -0.3:
        return "Sideways / mixed", "🟡"
    if ratio > -0.5:
        return "Mildly bearish", "🔴"
    return "Bearish", "🔴"


def compute_breadth(bars: dict, ema_period: int = 50, min_stocks: int = 30) -> dict | None:
    """
    How many of the scanned stocks are above their own trend. Free inside a
    scheduled scan (the bars are already loaded). Only stocks whose latest
    bar is on the most common date count, so a halted or stale symbol can't
    skew "advancers/decliners".
    """
    entries = []
    for df in bars.values():
        try:
            close = pd.to_numeric(df["close"], errors="coerce").dropna()
            if len(close) < ema_period + 5:
                continue
            last, prev = float(close.iloc[-1]), float(close.iloc[-2])
            ema_fast = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
            ema_slow = float(close.ewm(span=ema_period, adjust=False).mean().iloc[-1])
            entries.append((_as_of(close.index), last > ema_slow, last > ema_fast, last - prev))
        except Exception:
            continue
    if len(entries) < min_stocks:
        return None
    ref_date = Counter(e[0] for e in entries).most_common(1)[0][0]
    live = [e for e in entries if e[0] == ref_date]
    if len(live) < min_stocks:
        return None
    n = len(live)
    return {
        "n": n,
        "pct_above_50": sum(e[1] for e in live) / n,
        "pct_above_20": sum(e[2] for e in live) / n,
        "advancers": sum(1 for e in live if e[3] > 0),
        "decliners": sum(1 for e in live if e[3] < 0),
    }


def assess_market_direction(
    nifty_df: pd.DataFrame | None,
    global_snapshot: dict | None = None,
    *,
    bank_df: pd.DataFrame | None = None,
    bars: dict | None = None,
    nifty_source: str = "",
    bank_source: str = "",
    today: str | None = None,
) -> MarketDirection:
    today = today or datetime.now(IST).strftime("%Y-%m-%d")
    nifty = read_index(nifty_df, "Nifty 50", nifty_source)
    bank = read_index(bank_df, "Bank Nifty", bank_source)
    snapshot = _finite_snapshot(global_snapshot)

    factors = build_factors(nifty, snapshot, today)
    score = sum(f.score for f in factors)
    label, emoji = _verdict(score, len(factors))

    breadth = None
    if bars:
        try:
            breadth = compute_breadth(bars)
        except Exception:
            logger.warning("Market breadth could not be computed", exc_info=True)

    return MarketDirection(
        label=label, emoji=emoji, stance=STANCES[label], score=score, n_factors=len(factors),
        factors=factors, nifty=nifty, bank=bank, global_snapshot=snapshot, breadth=breadth, today=today,
    )


# ----------------------------------------------------------------------
# formatting (Telegram HTML - only <b>/<i>; every dynamic string escaped)
# ----------------------------------------------------------------------
_MARK = {1: "✅", 0: "➖", -1: "❌"}
_TAG_RE = re.compile(r"<[^>]+>")


def to_plain_text(message: str) -> str:
    """The same message without Telegram's HTML, for the Actions log."""
    return html.unescape(_TAG_RE.sub("", message))


def _factor_text(f: Factor) -> str:
    return f"{html.escape(f.label)} {html.escape(f.shown)} {_MARK[f.score]}"


def _global_word(md: MarketDirection) -> str | None:
    g = next((f for f in md.factors if f.key == "global"), None)
    return g.shown.split(" ")[0] if g else None


def format_direction_line(md: MarketDirection) -> str:
    """One compact line - the header on the scheduled BTST/Swing messages."""
    parts = [f"{md.emoji} <b>{html.escape(md.label)}</b>"]
    n = md.nifty
    if n is not None and n.day_change is not None:
        parts.append(f"Nifty {_pct(n.day_change)} {'today' if n.as_of == md.today else 'last session'}")
    elif md.label == "Unclear":
        parts.append("index data unavailable")
    word = _global_word(md)
    if word:
        parts.append(f"global {html.escape(word)}")
    if md.breadth:
        parts.append(f"breadth {md.breadth['pct_above_50']:.0%} of {md.breadth['n']} stocks above 50d EMA")
    return "🧭 <b>Market:</b> " + " · ".join(parts)


def _trend_side(r: IndexReading) -> int | None:
    if r.vs_ema50 is not None:
        return _band_score(r.vs_ema50, EMA50_BAND)
    return _band_score(r.vs_ema20, EMA20_BAND)


def format_direction_message(md: MarketDirection) -> str:
    """The full readout - what the manual run sends."""
    e = html.escape
    lines = ["🧭 <b>Market Direction</b>"]

    n, b = md.nifty, md.bank
    provenance = []
    if n is not None and n.as_of:
        provenance.append(f"latest bar {n.as_of}" + (" (today - partial if the market is open)" if n.as_of == md.today else ""))
    if n is not None and n.source:
        provenance.append(f"Nifty: {SOURCE_NOTES.get(n.source, n.source)}")
    if provenance:
        lines.append("<i>" + e(" · ".join(provenance)) + "</i>")

    headline = f"{md.emoji} <b>{e(md.label)}</b>"
    if md.n_factors >= 3:
        headline += f" - score {md.score:+d} of {md.n_factors}"
    lines.append(headline + f"\n<i>{e(md.stance)}</i>")

    if n is None:
        lines.append("<b>Nifty 50</b>\nCould not read Nifty data from any source this run.")
    else:
        head = "<b>Nifty 50</b>" + (f"  {n.last_close:,.0f}" if n.show_level else "")
        by_key = {f.key: f for f in md.factors}
        row1 = [_factor_text(by_key[k]) for k in ("today", "ema20", "ema50") if k in by_key]
        row2 = [_factor_text(by_key[k]) for k in ("sma200", "rsi") if k in by_key]
        extra = []
        if n.change_20d is not None:
            extra.append(f"20-day change {_pct(n.change_20d)}")
        if n.below_52w_high is not None:
            extra.append(f"{n.below_52w_high:.1%} below 52-week high")
        block = [head]
        for row in (row1, row2, extra):
            if row:
                block.append(" | ".join(row))
        lines.append("\n".join(block))

    if b is not None:
        bits = [f"{'Today' if b.as_of == md.today else 'Last session'} {_pct(b.day_change)}",
                f"20d EMA {_pct(b.vs_ema20)}"]
        if b.vs_ema50 is not None:
            bits.append(f"50d EMA {_pct(b.vs_ema50)}")
        if b.rsi14 is not None:
            bits.append(f"RSI {b.rsi14:.0f}")
        block = ["<b>Bank Nifty</b>" + (f"  {b.last_close:,.0f}" if b.show_level else "") + " <i>(context, not scored)</i>",
                 " | ".join(bits)]
        side_b = _trend_side(b)
        side_n = _trend_side(n) if n is not None else None
        if side_b is not None and side_n is not None and side_b != side_n:
            block.append("↳ " + ("weaker" if side_b < side_n else "stronger") + " than Nifty on trend")
        lines.append("\n".join(block))

    g = next((f for f in md.factors if f.key == "global"), None)
    if g is not None:
        snap = md.global_snapshot
        gl = [f"<b>Global cues</b> {_MARK[g.score]} {e(g.shown)}"]
        moves = []
        if "us_dow" in snap or "us_nasdaq" in snap:
            us = [snap[k] for k in ("us_dow", "us_nasdaq") if k in snap]
            moves.append(f"US {_pct(sum(us) / len(us))}")
        if "nikkei" in snap or "hang_seng" in snap:
            asia = [snap[k] for k in ("nikkei", "hang_seng") if k in snap]
            moves.append(f"Asia {_pct(sum(asia) / len(asia))}")
        if "crude_oil" in snap:
            moves.append(f"Crude {_pct(snap['crude_oil'])}")
        if "dollar_index" in snap:
            moves.append(f"Dollar {_pct(snap['dollar_index'])}")
        if moves:
            gl.append(" | ".join(moves))
        lines.append("\n".join(gl))
    else:
        lines.append("<b>Global cues</b>\nUnavailable this run (Yahoo Finance did not respond) - not counted in the score.")

    if md.breadth:
        br = md.breadth
        lines.append(f"<b>Breadth</b>\n{br['pct_above_50']:.0%} of {br['n']} scanned stocks above their 50d EMA | "
                     f"{br['advancers']} up / {br['decliners']} down")

    lines.append("<i>Rule-based read of trend and overnight cues - not a forecast. No option strikes.</i>")
    return "\n\n".join(lines)


# ----------------------------------------------------------------------
# data fetching (network) - used by the manual command only
# ----------------------------------------------------------------------
def _fetch_yahoo_history(ticker: str, period: str = "2y") -> pd.DataFrame | None:
    import yfinance as yf

    data = yf.Ticker(ticker).history(period=period)
    if data is None or data.empty:
        return None
    data = data.rename(columns=str.lower)
    return data[[c for c in ("open", "high", "low", "close", "volume") if c in data.columns]]


def fetch_index_history(name: str, store=None, lookback_days: int = 300, min_bars: int = 60):
    """
    Returns (daily bars, source symbol) from the first INDEX_SOURCES entry that
    works, or (None, "") if none does. `store` is an Angel One historical store,
    or None to skip the Angel One sources (e.g. its login failed).
    """
    for kind, symbol in INDEX_SOURCES.get(name, []):
        try:
            if kind == "angel":
                if store is None:
                    continue
                df = store.get_bars(symbol, lookback_days=lookback_days)
            else:
                df = _fetch_yahoo_history(symbol)
        except Exception as exc:
            logger.warning(f"{name}: {symbol} ({kind}) unavailable - {exc}; trying the next source")
            continue
        n_bars = 0 if df is None else len(df)
        if df is not None and n_bars >= min_bars and "close" in df.columns:
            logger.info(f"{name}: using {symbol} via {kind} ({n_bars} bars)")
            return df, symbol
        logger.warning(f"{name}: {symbol} ({kind}) returned only {n_bars} bars - trying the next source")
    return None, ""

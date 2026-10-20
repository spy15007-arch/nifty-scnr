"""
Institutional-footprint scoring ("quality") and breakout-setup detection.

Everything here is computed from daily price and volume (plus the optional
end-of-day delivery-% feed) with the exact definitions that were tested on
seven years of real NSE data (2019-2026, split/bonus adjusted; see
reports/evidence/). Nothing here is a guess about the future: it ranks stocks
by traits that, in that history, separated the breakouts that followed
through from the ones that failed.

Two separate questions are answered:

1. SETUP - is the stock doing something actionable right now?
     FRESH  : closed above its prior 50-day high, up at least 2% on the day,
              on heavy volume (>= 1.5x its recent average, projected to a full
              day when the market is still open), closing in the top 40% of the
              day's range, and not more than 5% past the broken level.
     PRIMED : within 3% below its 50-day high (coiled right under resistance).

2. QUALITY - is it the kind of stock that tends to keep going?
     The average of cross-sectional z-scores (each limited to +/-3) of:
       + closeness to the 52-week high      + slope of the 200-day average
       + 12-month return                    + liquidity (log turnover)
       + delivery % (when available)        - volatility (ATR %)
       - stretch above the 21-day average   - 5-day spike
     expressed as a percentile among the day's liquid stocks. The delivery-%
     feature is the strongest single one: high delivery means the volume is
     investors who take shares home rather than intraday churn.

Honest note: these traits mostly help AVOID the weak breakouts. They do not
identify guaranteed winners - no rule does.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import config

# (feature, sign). Sign -1 means "lower is better".
SCORE_SPEC_PRICE: tuple[tuple[str, int], ...] = (
    ("dist_hi", +1),
    ("sma200_chg", +1),
    ("ret252", +1),
    ("atr_pct", -1),
    ("ema21_x", -1),
    ("ret5", -1),
    ("turn20", +1),
)
SCORE_SPEC_DLV: tuple[tuple[str, int], ...] = SCORE_SPEC_PRICE + (("dlv20", +1),)

# Plain-English phrases used in the "why" lines (positive contribution / negative contribution)
FEATURE_TEXT: dict[str, tuple[str, str]] = {
    "dist_hi": ("close to its 52-week high", "far below its 52-week high"),
    "sma200_chg": ("long-term (200-day) trend rising", "long-term trend weak"),
    "ret252": ("strong 12-month return", "weak 12-month return"),
    "atr_pct": ("calm, low-volatility mover", "very volatile"),
    "ema21_x": ("not stretched above its 21-day average", "already stretched above its 21-day average"),
    "ret5": ("no sharp 5-day spike", "already spiked in the last 5 days"),
    "turn20": ("highly liquid (institutions can trade it)", "thinly traded"),
    "dlv20": ("high delivery % (investors, not churn)", "low delivery % (mostly intraday churn)"),
}

MIN_BARS_FOR_FEATURES = 60      # below this the stock is skipped outright
WINSOR = 3.0                    # z-scores are limited to +/- 3
ALLOW_MISSING_FEATURES = 1      # a score needs at least n-1 of its n features


# --------------------------------------------------------------------------- #
# Bars -> features for the newest bar
# --------------------------------------------------------------------------- #
def _finite(*vals) -> bool:
    return all(v is not None and isinstance(v, (int, float, np.floating, np.integer)) and math.isfinite(v) for v in vals)


def clean_bars(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Float64 OHLCV, oldest first, one row per session, rows without a valid close dropped."""
    if df is None or len(df) == 0:
        return None
    need = ["open", "high", "low", "close", "volume"]
    if any(c not in df.columns for c in need):
        return None
    d = df[need].astype("float64")
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d = d[(d["close"] > 0) & d["open"].notna() & d["high"].notna() & d["low"].notna()]
    return d if len(d) else None


def compute_features(df: pd.DataFrame | None, vol_fraction: float = 1.0) -> dict | None:
    """
    Everything the scan needs about the NEWEST bar of `df`.

    `vol_fraction` is the share of a normal day's volume that has traded so
    far (1.0 when the bar is complete). The newest bar's volume is divided by
    it, i.e. projected to a full day, so volume tests compare like with like.

    Returns None when there is too little history. Individual values are NaN
    when their own look-back is not available.
    """
    d = clean_bars(df)
    if d is None or len(d) < MIN_BARS_FOR_FEATURES:
        return None
    if vol_fraction < 0.999:
        d = d.copy()
        d.iloc[-1, d.columns.get_loc("volume")] = d["volume"].iloc[-1] / max(float(vol_fraction), 0.05)

    o, h, l, c, v = d["open"], d["high"], d["low"], d["close"], d["volume"]
    n = len(d)
    cn, hn, ln = c.to_numpy(), h.to_numpy(), l.to_numpy()
    prev_c = c.shift(1)

    def last(series: pd.Series) -> float:
        x = series.iloc[-1]
        return float(x) if pd.notna(x) else float("nan")

    # --- trend / structure -------------------------------------------------
    sma50 = c.rolling(50, min_periods=45).mean()
    sma200 = c.rolling(200, min_periods=190).mean()
    ema21 = c.ewm(span=21, adjust=False, min_periods=15).mean()
    hi252 = h.rolling(252, min_periods=200).max()

    close = float(cn[-1])
    hi252_now = last(hi252)
    dist_hi = close / hi252_now - 1 if _finite(hi252_now) and hi252_now > 0 else float("nan")
    sma200_now = last(sma200)
    sma200_prev = float(sma200.iloc[-21]) if n >= 21 and pd.notna(sma200.iloc[-21]) else float("nan")
    sma200_chg = sma200_now / sma200_prev - 1 if _finite(sma200_now, sma200_prev) and sma200_prev > 0 else float("nan")
    ret252 = close / float(cn[-253]) - 1 if n >= 253 else float("nan")
    ret5 = close / float(cn[-6]) - 1 if n >= 6 else float("nan")
    ema21_now = last(ema21)
    ema21_x = close / ema21_now - 1 if _finite(ema21_now) and ema21_now else float("nan")

    # --- volatility (true range includes the newest bar) -------------------
    pcn = prev_c.to_numpy()
    tr = np.fmax(hn - ln, np.fmax(np.abs(hn - pcn), np.abs(ln - pcn)))
    atr14 = pd.Series(tr, index=d.index).rolling(14, min_periods=14).mean()
    atr14_now = last(atr14)
    atr_pct = atr14_now / close if _finite(atr14_now) else float("nan")

    # --- liquidity ----------------------------------------------------------
    turn20 = last((c * v).rolling(20, min_periods=15).mean())

    # --- breakout structure (look-backs exclude the newest bar) ------------
    lookback = int(config.BREAKOUT_LOOKBACK_SESSIONS)
    piv_series = h.shift(1).rolling(lookback, min_periods=int(lookback * 0.9)).max()
    piv50 = last(piv_series)
    piv_incl = float(max(piv50, float(hn[-1]))) if _finite(piv50) else float("nan")
    piv_prev = float(piv_series.iloc[-2]) if n >= 2 and pd.notna(piv_series.iloc[-2]) else float("nan")
    prev_close = float(cn[-2]) if n >= 2 else float("nan")
    gain1d = close / prev_close - 1 if _finite(prev_close) and prev_close else float("nan")
    close_pos = float((cn[-1] - ln[-1]) / (hn[-1] - ln[-1] + 1e-9))
    vol_avg15 = last(v.shift(1).rolling(15, min_periods=13).mean())
    rv15 = float(v.iloc[-1]) / vol_avg15 if _finite(vol_avg15) and vol_avg15 > 0 else float("nan")
    first_break = not (_finite(prev_close, piv_prev) and prev_close > piv_prev)

    # sessions since the stock first closed above its own rolling 50-day high
    # (0 = today is the first such close); None when it is not above it today
    above = (c > piv_series).to_numpy()
    run = 0
    for flag in above[::-1]:
        if flag:
            run += 1
        else:
            break
    days_since_break = run - 1 if run > 0 else None

    last_date = d.index[-1]
    return {
        "bars": n,
        "last_date": last_date.date() if hasattr(last_date, "date") else last_date,
        "open": float(o.iloc[-1]), "high": float(hn[-1]), "low": float(ln[-1]), "close": close,
        "prev_close": prev_close, "volume": float(v.iloc[-1]),
        # quality-score inputs
        "dist_hi": dist_hi, "sma200_chg": sma200_chg, "ret252": ret252, "atr_pct": atr_pct,
        "ema21_x": ema21_x, "ret5": ret5, "turn20": turn20,
        # breakout structure
        "piv50": piv50, "piv_incl": piv_incl, "gain1d": gain1d, "close_pos": close_pos,
        "rv15": rv15, "first_break": bool(first_break), "days_since_break": days_since_break,
        # regime inputs
        "sma50": last(sma50), "sma200": sma200_now,
    }


def detect_setup(
    f: dict | None,
    min_gain: float | None = None,
    min_rv: float | None = None,
    max_ext: float | None = None,
    near_pct: float | None = None,
    min_close_pos: float | None = None,
) -> dict:
    """
    Classify the newest bar. Returns a dict with:
      setup        'fresh' | 'primed' | None
      ext_pct      close vs the prior 50-day high (+ = above it)
      trigger      buy-stop price for a next-session entry (fresh: high x 1.001;
                   primed: 50-day high x 1.002)
      above_pivot  True when the close is already above the prior 50-day high
    FRESH takes precedence over PRIMED (a fresh breakout is also near its high).
    """
    min_gain = config.FRESH_MIN_GAIN if min_gain is None else min_gain
    min_rv = config.FRESH_MIN_VOLUME_RATIO if min_rv is None else min_rv
    max_ext = config.FRESH_MAX_EXTENSION if max_ext is None else max_ext
    near_pct = config.PRIMED_NEAR_PCT if near_pct is None else near_pct
    min_close_pos = config.FRESH_MIN_CLOSE_POSITION if min_close_pos is None else min_close_pos

    out = {"setup": None, "ext_pct": float("nan"), "trigger": None, "above_pivot": False}
    if not f:
        return out
    c, piv, piv_incl = f.get("close"), f.get("piv50"), f.get("piv_incl")
    if not _finite(c, piv, piv_incl) or piv <= 0:
        return out

    ext = c / piv - 1
    out["ext_pct"] = ext
    out["above_pivot"] = bool(c > piv)

    fresh = (
        c > piv
        and ext <= max_ext
        and _finite(f.get("rv15")) and f["rv15"] >= min_rv
        and _finite(f.get("gain1d")) and f["gain1d"] >= min_gain
        and _finite(f.get("close_pos")) and f["close_pos"] >= min_close_pos
    )
    primed = c >= (1.0 - near_pct) * piv_incl

    if fresh:
        out["setup"] = "fresh"
        out["trigger"] = f["high"] * 1.001
    elif primed:
        out["setup"] = "primed"
        out["trigger"] = piv_incl * 1.002
    return out


# --------------------------------------------------------------------------- #
# Cross-sectional quality score
# --------------------------------------------------------------------------- #
def _inputs(feats: pd.DataFrame, dlv20: pd.Series | None) -> pd.DataFrame:
    """Raw score inputs, one column per feature (turnover is log-scaled)."""
    X = pd.DataFrame(index=feats.index)
    for name in ("dist_hi", "sma200_chg", "ret252", "atr_pct", "ema21_x", "ret5"):
        X[name] = pd.to_numeric(feats[name], errors="coerce") if name in feats else np.nan
    t = pd.to_numeric(feats["turn20"], errors="coerce") if "turn20" in feats else pd.Series(np.nan, index=feats.index)
    X["turn20"] = np.log(t.where(t > 0))
    if dlv20 is not None:
        X["dlv20"] = pd.to_numeric(dlv20.reindex(feats.index), errors="coerce")
    else:
        X["dlv20"] = np.nan
    return X.replace([np.inf, -np.inf], np.nan)


def score_universe(
    feats: pd.DataFrame,
    dlv20: pd.Series | None = None,
    stats: dict | None = None,
    min_delivery_coverage: float = 0.60,
) -> tuple[pd.DataFrame, dict]:
    """
    Score every row of `feats` (index = symbol).

    stats=None  : the rows ARE the day's liquid universe. Means / standard
                  deviations come from them and the percentile is the rank
                  within them. Returns the stats so a later, partial run (the
                  intraday watchlist scan) can score with the same yardstick.
    stats=dict  : score with the stored means / standard deviations and place
                  the score in the stored distribution (so a few dozen
                  watchlist names are judged against the whole universe).

    The delivery-% feature is used only when the data is good enough
    (>= min_delivery_coverage of the universe has it); otherwise the
    price-only 7-feature score is used.

    Returns (frame, stats). Frame columns: score, quality_pct (0-100), n_feat,
    c_<feature> (signed contribution of each feature to the score).
    """
    X = _inputs(feats, dlv20)
    if stats is None:
        have_dlv = X["dlv20"].notna().mean() if len(X) else 0.0
        use_dlv = have_dlv >= min_delivery_coverage
        spec = SCORE_SPEC_DLV if use_dlv else SCORE_SPEC_PRICE
        mean = {k: float(np.nanmean(X[k].to_numpy())) if X[k].notna().any() else float("nan") for k, _ in spec}
        std = {k: float(np.nanstd(X[k].to_numpy())) if X[k].notna().any() else float("nan") for k, _ in spec}  # ddof=0
    else:
        use_dlv = stats.get("spec") == "dlv"
        spec = SCORE_SPEC_DLV if use_dlv else SCORE_SPEC_PRICE
        mean, std = stats["mean"], stats["std"]

    contrib = pd.DataFrame(index=X.index)
    for k, sign in spec:
        sd = std.get(k)
        if sd is None or not math.isfinite(sd) or sd <= 0:
            contrib[k] = np.nan
            continue
        z = ((X[k] - mean[k]) / sd).clip(-WINSOR, WINSOR)
        contrib[k] = sign * z

    n_feat = contrib.notna().sum(axis=1)
    score = contrib.sum(axis=1, skipna=True) / n_feat.clip(lower=1)
    score = score.where(n_feat >= len(spec) - ALLOW_MISSING_FEATURES)

    if stats is None:
        pct = score.rank(pct=True, method="average") * 100.0
        good = score.dropna().to_numpy()
        out_stats = {
            "spec": "dlv" if use_dlv else "price",
            "mean": mean, "std": std,
            "score_q": [float(x) for x in np.quantile(good, np.linspace(0, 1, 101))] if len(good) >= 30 else [],
            "n": int(len(good)),
        }
    else:
        q = np.asarray(stats.get("score_q") or [], dtype=float)
        if len(q) == 101:
            pct = pd.Series(np.interp(score.to_numpy(dtype=float), q, np.linspace(0, 100, 101),
                                      left=0.0, right=100.0), index=score.index).where(score.notna())
        else:
            pct = pd.Series(np.nan, index=score.index)
        out_stats = stats

    frame = pd.DataFrame({"score": score, "quality_pct": pct, "n_feat": n_feat}, index=X.index)
    for k, _ in spec:
        frame[f"c_{k}"] = contrib[k]
    return frame, out_stats


def top_reasons(row: pd.Series, k: int = 3) -> list[str]:
    """Plain-English list of the features that pushed this stock's score up the most."""
    items: list[tuple[float, str]] = []
    for name, (pos, neg) in FEATURE_TEXT.items():
        v = row.get(f"c_{name}")
        if v is None or not (isinstance(v, (int, float, np.floating)) and math.isfinite(v)):
            continue
        items.append((float(v), name))
    items.sort(reverse=True)
    out = []
    for v, name in items[:k]:
        if v > 0.25:
            out.append(FEATURE_TEXT[name][0])
    return out


def weak_points(row: pd.Series, k: int = 2) -> list[str]:
    """The features that hurt this stock's score the most (shown as caveats)."""
    items: list[tuple[float, str]] = []
    for name in FEATURE_TEXT:
        v = row.get(f"c_{name}")
        if v is None or not (isinstance(v, (int, float, np.floating)) and math.isfinite(v)):
            continue
        items.append((float(v), name))
    items.sort()
    return [FEATURE_TEXT[name][1] for v, name in items[:k] if v < -0.5]


def grade_for_quality(quality_pct: float | None) -> str:
    """Cosmetic letter for the quality percentile. It is a RANK, not a probability of profit."""
    if quality_pct is None or not math.isfinite(quality_pct):
        return "-"
    if quality_pct >= 90:
        return "A+"
    if quality_pct >= 80:
        return "A"
    if quality_pct >= 65:
        return "B"
    return "C"


# --------------------------------------------------------------------------- #
# Market breadth (share of liquid stocks in an up-trend)
# --------------------------------------------------------------------------- #
def market_breadth(feats: pd.DataFrame, min_names: int = 50) -> dict:
    """
    breadth50 / breadth200 = share of the liquid universe closing above its 50 /
    200-day average. NaN when fewer than `min_names` stocks have the data.
    Used as a soft warning only: when most stocks are below their 50-day
    average, breakouts fail more often (the effect was not stable enough in
    the history to justify blocking signals).
    """
    out = {"breadth50": float("nan"), "breadth200": float("nan"), "n": 0}
    if feats is None or len(feats) == 0:
        return out
    for key, col in (("breadth50", "sma50"), ("breadth200", "sma200")):
        if col not in feats or "close" not in feats:
            continue
        ok = feats[col].notna() & feats["close"].notna()
        if int(ok.sum()) >= min_names:
            out[key] = float((feats.loc[ok, "close"] > feats.loc[ok, col]).mean())
            out["n"] = int(ok.sum())
    return out


def regime_label(breadth50: float | None) -> str:
    """'healthy' / 'mixed' / 'weak' / 'unknown' from the 50-day breadth."""
    if breadth50 is None or not math.isfinite(breadth50):
        return "unknown"
    if breadth50 >= config.HEALTHY_BREADTH:
        return "healthy"
    if breadth50 < config.WEAK_BREADTH:
        return "weak"
    return "mixed"

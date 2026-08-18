import pandas as pd
import numpy as np

def compute_rsi(prices, period=14):
    """Calculates standard 14-period Relative Strength Index using pure pandas."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def compute_atr(high, low, close, period=14):
    """Calculates Average True Range for clean structural price profiling."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def check_pre_breakout_setup(df: pd.DataFrame) -> dict:
    """
    Catches stocks BEFORE they break out, not after they've already run.

    The old approach (RSI >= 60 required) is fundamentally backwards for
    this purpose: RSI is a LAGGING momentum measure, so by the time RSI
    crosses 60 a stock has typically already moved several percent to
    get there - the move is already underway, often already done. That
    old gate could only ever surface stocks that had ALREADY broken out.

    This instead looks for RSI BUILDING in a neutral-to-firm zone
    (45-65) and RISING over the last few days - momentum accumulating
    without the stock being overbought yet. RSI > 68 is a HARD
    EXCLUSION (already extended, chasing risk), not a qualifying
    condition.
    """
    if df is None or len(df) < 30:
        return {"flagged": False, "reason": "Insufficient historical depth"}

    df['RSI'] = compute_rsi(df['close'])
    df['ATR'] = compute_atr(df['high'], df['low'], df['close'])

    current_rsi = df['RSI'].iloc[-1]
    prior_rsi_5 = df['RSI'].iloc[-5] if len(df) > 5 else current_rsi
    current_close = df['close'].iloc[-1]
    current_atr = df['ATR'].iloc[-1]

    # HARD EXCLUSION: already overbought / already ran - the opposite
    # of what a pre-breakout scan should be catching
    if current_rsi > 68:
        return {"flagged": False, "reason": f"Already overbought (RSI {current_rsi:.1f}) - move likely already happened, high chase risk"}

    if pd.isna(current_rsi) or pd.isna(prior_rsi_5):
        return {"flagged": False, "reason": "RSI not yet computable"}

    # Building-momentum zone: RSI firm but not yet overbought, AND
    # rising vs 5 days ago (momentum accumulating, not fading/flat)
    building_momentum = 45 <= current_rsi <= 65 and current_rsi > prior_rsi_5

    if not building_momentum:
        return {"flagged": False, "reason": f"RSI {current_rsi:.1f} not in pre-breakout building zone (45-65) or not rising"}

    # Higher-low structural check - still want basic trend health,
    # not catching a stock that's just bouncing in a downtrend
    recent_lows = df['low'].tail(10)
    if current_close < recent_lows.mean():
        return {"flagged": False, "reason": "Fails higher-low structural pattern"}

    stop_loss = current_close - (1.5 * current_atr)

    return {
        "flagged": True,
        "current_rsi": round(current_rsi, 2),
        "entry_price": round(current_close, 2),
        "stop_loss": round(stop_loss, 2),
        "target_1": round(current_close + (1.0 * current_atr), 2),
        "target_2": round(current_close + (2.0 * current_atr), 2),
        "target_3": round(current_close + (3.0 * current_atr), 2),
        "target_4": round(current_close + (4.0 * current_atr), 2),
    }


def check_rsi_60_breakout(df: pd.DataFrame) -> dict:
    """
    LEGACY momentum-confirmation check - kept for reference only, NOT
    used as the primary gate anymore (see check_pre_breakout_setup
    above). Requiring RSI >= 60 means this can only ever fire AFTER a
    stock has already moved, not before.
    """
    if df is None or len(df) < 30:
        return {"flagged": False, "reason": "Insufficient historical depth"}

    df['RSI'] = compute_rsi(df['close'])
    df['ATR'] = compute_atr(df['high'], df['low'], df['close'])

    current_rsi = df['RSI'].iloc[-1]
    previous_rsi = df['RSI'].iloc[-2]
    prior_rsi_3 = df['RSI'].iloc[-3] if len(df) > 3 else previous_rsi

    current_close = df['close'].iloc[-1]
    current_atr = df['ATR'].iloc[-1]

    fresh_crossover = (previous_rsi <= 60 and current_rsi > 60)
    coiling_above_60 = (current_rsi >= 60 and previous_rsi >= 58 and prior_rsi_3 >= 57 and current_rsi <= 75)

    if fresh_crossover or coiling_above_60:
        if current_rsi > 80:
            return {"flagged": False, "reason": "Overextended momentum (RSI > 80)"}

        recent_lows = df['low'].tail(10)
        if current_close < recent_lows.mean():
            return {"flagged": False, "reason": "Fails higher-low structural pattern"}

        stop_loss = current_close - (1.5 * current_atr)

        return {
            "flagged": True,
            "current_rsi": round(current_rsi, 2),
            "entry_price": round(current_close, 2),
            "stop_loss": round(stop_loss, 2),
            "target_1": round(current_close + (1.0 * current_atr), 2),
            "target_2": round(current_close + (2.0 * current_atr), 2),
            "target_3": round(current_close + (3.0 * current_atr), 2),
            "target_4": round(current_close + (4.0 * current_atr), 2)
        }

    return {"flagged": False, "reason": "No active breakout or coiling signatures found"}

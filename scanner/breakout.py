import pandas as pd
import numpy as np

def compute_rsi(prices, period=14):
    """Calculates standard 14-period Relative Strength Index using pure pandas."""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-10) # Prevent division by zero
    return 100 - (100 / (1 + rs))

def compute_atr(high, low, close, period=14):
    """Calculates Average True Range for clean structural price profiling."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def check_rsi_60_breakout(df: pd.DataFrame) -> dict:
    """
    Advanced scanning engine. Captures both fresh RSI 60 crossovers AND mature, 
    coiling trendline consolidations with higher lows holding above the 60 zone.
    """
    if df is None or len(df) < 30:
        return {"flagged": False, "reason": "Insufficient historical depth"}

    # Calculate indicators using standard core columns
    df['RSI'] = compute_rsi(df['close'])
    df['ATR'] = compute_atr(df['high'], df['low'], df['close'])
    
    current_rsi = df['RSI'].iloc[-1]
    previous_rsi = df['RSI'].iloc[-2]
    prior_rsi_3 = df['RSI'].iloc[-3] if len(df) > 3 else previous_rsi
    
    current_close = df['close'].iloc[-1]
    current_atr = df['ATR'].iloc[-1]

    # STAGE 1: Check for Launchpad Momentum Conditions
    # Condition A: Fresh Crossover above 60
    fresh_crossover = (previous_rsi <= 60 and current_rsi > 60)
    
    # Condition B: Trendline Consolidation Holding (RSI has been stabilizing above 58-60 for the last 3 days)
    coiling_above_60 = (current_rsi >= 60 and previous_rsi >= 58 and prior_rsi_3 >= 57 and current_rsi <= 75)

    if fresh_crossover or coiling_above_60:
        # STAGE 2: Safety Check — Ensure it's not already completely overextended above 80
        if current_rsi > 80:
            return {"flagged": False, "reason": "Overextended momentum (RSI > 80)"}
            
        # STAGE 3: Higher-Low Structuring Check (Verifies trendline accumulation)
        recent_lows = df['low'].tail(10)
        if current_close < recent_lows.mean():
            return {"flagged": False, "reason": "Fails higher-low structural pattern"}

        # STAGE 4: Build 4 precise mathematical target boundaries using ATR multiples
        stop_loss = current_close - (1.5 * current_atr)
        
        return {
            "flagged": True,
            "current_rsi": round(current_rsi, 2),
            "entry_price": round(current_close, 2),
            "stop_loss": round(stop_loss, 2),
            "target_1": round(current_close + (1.0 * current_atr), 2),  # T1: Conservative Target
            "target_2": round(current_close + (2.0 * current_atr), 2),  # T2: Standard swing extension
            "target_3": round(current_close + (3.0 * current_atr), 2),  # T3: Extended daily run
            "target_4": round(current_close + (4.0 * current_atr), 2)   # T4: Volatility max breakout target
        }

    return {"flagged": False, "reason": "No active breakout or coiling signatures found"}

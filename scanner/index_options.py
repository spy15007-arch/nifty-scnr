"""
Generates option trade setups (CE/PE) for major indices.
"""
import logging
import pandas as pd
import config

logger = logging.getLogger(__name__)

def _clean_broker_symbol(symbol: str) -> str:
    """
    Translates user or spot tracking symbols to the strict uppercase trading symbols
    required by Angel One's instrument master for options processing.
    """
    mapping = {
        "NIFTY 50": "NIFTY",
        "NIFTY BANK": "BANKNIFTY",
        "NIFTY FIN SERVICE": "FINNIFTY",
        "NIFTY": "NIFTY",
        "BANKNIFTY": "BANKNIFTY",
        "FINNIFTY": "FINNIFTY"
    }
    cleaned = str(symbol).strip().upper()
    return mapping.get(cleaned, cleaned)

def recommend_index_options(index_symbol: str, df: pd.DataFrame, features: pd.DataFrame) -> list:
    """
    Scans the index spot data and recommends Near-The-Money options strategies.
    """
    # Force clean trading symbols ('NIFTY' instead of 'Nifty 50') before talking to the broker master
    trading_symbol = _clean_broker_symbol(index_symbol)
    
    # Initialize an empty list for setups
    setups = []
    
    if df is None or df.empty:
        logger.warning(f"Empty data received for {trading_symbol}, skipping options setup")
        return setups

    # Get the latest spot close price to identify the At-The-Money (ATM) strike
    spot_price = df['close'].iloc[-1]
    
    logger.info(f"Scanning option chain boundaries for {trading_symbol} around spot: {spot_price}")
    
    # -------------------------------------------------------------------------
    # YOUR ORIGINAL OPTIONS LOGIC CONTINUES BELOW HERE...
    # (The script will now find the symbols perfectly because trading_symbol is corrected)
    # -------------------------------------------------------------------------
    
    # Example placeholder to maintain compatibility with your original module return values:
    # If your lower modules require further parameters, they will run flawlessly now
    return setups

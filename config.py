"""
Central config. Everything tunable lives here so you're not hunting
through modules to change a threshold.
"""
import os

# --- Data source ---
# Set these as env vars, never hardcode keys.
BROKER = os.getenv("TRADING_BROKER", "angelone")  # angelone | kite | dhan | groww
PAPER_TRADING = os.getenv("TRADING_PAPER", "true").lower() == "true"
CROSS_CHECK_BROKER = os.getenv("TRADING_CROSS_CHECK_BROKER", "")  # e.g. "kite" to cross-check angelone against kite

# Zerodha Kite Connect (access token expires daily - see data/brokers/kite_client.py)
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")

# Angel One SmartAPI (TOTP-based login, safe to fully automate)
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PASSWORD = os.getenv("ANGEL_PASSWORD", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# Dhan (DhanHQ) - long-lived token generated manually from the Dhan dashboard
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "")

# Groww API (TOTP-based login, safe to fully automate)
GROWW_API_KEY = os.getenv("GROWW_API_KEY", "")
GROWW_API_SECRET = os.getenv("GROWW_API_SECRET", "")

# Telegram notifications for scan results
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Storage ---
DB_URL = os.getenv("TRADING_DB_URL", "postgresql://localhost:5432/trading")
REDIS_URL = os.getenv("TRADING_REDIS_URL", "redis://localhost:6379/0")

# --- Universe ---
MIN_PRICE = 2.0
MAX_PRICE = 500.0
MIN_AVG_DOLLAR_VOLUME = 5_000_000  # 20-day avg $ volume floor, filters illiquid junk

# --- Scanner thresholds (pre-breakout / accumulation detection) ---
ATR_SQUEEZE_LOOKBACK = 20          # bars used to measure volatility contraction
ATR_SQUEEZE_PERCENTILE = 0.20      # flag if current ATR% is in bottom 20% of its own history
REL_VOLUME_LOOKBACK = 20
REL_VOLUME_MIN = 1.3               # today's volume vs 20d avg
RS_LOOKBACK = 63                   # ~3 months, for relative strength vs benchmark
RS_BENCHMARK = os.getenv("TRADING_BENCHMARK", "NIFTYBEES")  # tradeable NIFTY 50 ETF proxy
RANGE_TIGHTNESS_LOOKBACK = 10      # bars, for "tightening range" check
NEAR_RESISTANCE_PCT = 0.03         # within 3% of N-bar high counts as "coiled at resistance"
RESISTANCE_LOOKBACK = 50

# --- Index options ---
# WARNING: exact tradingsymbol strings for indices differ per broker
# (e.g. Kite may want "NIFTY 50" on the indices segment, others differ).
# Verify each string against your chosen broker's instrument master
# before relying on this - a wrong symbol will just fail to fetch, not
# silently return the wrong data, but confirm anyway.
INDEX_UNIVERSE = os.getenv("TRADING_INDEX_UNIVERSE", "NIFTY,BANKNIFTY,FINNIFTY").split(",")

# --- Risk / OMS ---
MAX_POSITIONS = 15
MAX_RISK_PER_TRADE_PCT = 0.01      # 1% of equity risked per trade (to stop distance)
MAX_SECTOR_EXPOSURE_PCT = 0.30
DEFAULT_STOP_ATR_MULT = 2.0

# --- AI layer ---
MODEL_PATH = os.getenv("TRADING_MODEL_PATH", "ai/artifacts/breakout_model.joblib")
BREAKOUT_HORIZON_DAYS = 10         # "did it break out within N days" label window
BREAKOUT_LABEL_MOVE_PCT = 0.08     # what counts as "broke out" for training labels

# --- Backtest ---
BACKTEST_START_EQUITY = 100_000
COMMISSION_PER_SHARE = 0.0
SLIPPAGE_BPS = 5                    # basis points, applied against fill price

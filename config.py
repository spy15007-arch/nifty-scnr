"""
Central config. Everything tunable lives here so you're not hunting
through modules to change a threshold.
"""
import os

# --- Data source ---
# Set these as env vars, never hardcode keys.
BROKER = "angelone"
PAPER_TRADING = os.getenv("TRADING_PAPER", "true").lower() == "true"

# Angel One SmartAPI (TOTP-based login, safe to fully automate)
ANGEL_API_KEY = os.getenv("ANGEL_API_KEY", "")
ANGEL_CLIENT_ID = os.getenv("ANGEL_CLIENT_ID", "")
ANGEL_PASSWORD = os.getenv("ANGEL_PASSWORD", "")
ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET", "")

# Telegram notifications for scan results
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- Storage ---
DB_URL = os.getenv("TRADING_DB_URL", "postgresql://localhost:5432/trading")
REDIS_URL = os.getenv("TRADING_REDIS_URL", "redis://localhost:6379/0")

# --- Universe ---
# NO upper price cap (removed 2026-10-18). A share's price says nothing about
# its quality or its chance of breaking out, and the old Rs 500 cap threw away
# roughly 58% of the 500 most-traded NSE stocks (most large and mid caps trade
# above Rs 500) - exactly the stocks big funds buy. Cost per share only
# affects how many shares you can buy, not the return, so it is a position-
# sizing question, not a scan filter.
MIN_PRICE = float(os.getenv("TRADING_MIN_PRICE", "2.0"))
# Liquidity floor on 20-day average TURNOVER (price x volume, in rupees).
# Raised from Rs 50 lakh to Rs 5 crore: big funds can only build positions in
# stocks that trade enough to absorb them, and setups in thin stocks are mostly
# noise. Every Nifty 500 stock clears Rs 5 crore on a normal day (the 500th
# most-traded NSE stock trades ~Rs 36 crore/day), so this only removes the
# illiquid tail. Override with TRADING_MIN_TURNOVER_INR.
MIN_AVG_DOLLAR_VOLUME = float(os.getenv("TRADING_MIN_TURNOVER_INR", "50000000"))
MIN_AVG_TURNOVER_INR = MIN_AVG_DOLLAR_VOLUME  # clearer alias; same number

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
# WARNING: exact tradingsymbol strings for indices - verify against
# Angel One's instrument master before relying on this; a wrong symbol
# will just fail to fetch, not silently return wrong data, but confirm.
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

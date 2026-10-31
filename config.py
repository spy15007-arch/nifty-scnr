"""
Central config. Everything tunable lives here so you're not hunting
through modules to change a threshold.
"""
import os


def _env_float(name: str, default: float) -> float:
    """Float from an environment variable. A missing OR blank variable (GitHub passes unset variables as "") uses the default."""
    raw = (os.getenv(name) or "").strip()
    try:
        return float(raw) if raw else float(default)
    except ValueError:
        return float(default)


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
MIN_PRICE = _env_float("TRADING_MIN_PRICE", 2.0)
# Liquidity floor on 20-day average TURNOVER (price x volume, in rupees).
# Raised from Rs 50 lakh to Rs 5 crore: big funds can only build positions in
# stocks that trade enough to absorb them, and setups in thin stocks are mostly
# noise. Every Nifty 500 stock clears Rs 5 crore on a normal day (the 500th
# most-traded NSE stock trades ~Rs 36 crore/day), so this only removes the
# illiquid tail. Override with TRADING_MIN_TURNOVER_INR.
MIN_AVG_DOLLAR_VOLUME = _env_float("TRADING_MIN_TURNOVER_INR", 50000000)
MIN_AVG_TURNOVER_INR = MIN_AVG_DOLLAR_VOLUME  # clearer alias; same number

# --- Selection v2 (2026-10-19): evidence-based, see reports/evidence/ ---
# Chosen and validated on 7 years of real NSE end-of-day data (2019-2026),
# designed on 2019-2024 and checked separately on 2025-26. The legacy
# 7-filter engine, the RSI 45-65 gate and the 3-signal minimum are no longer
# used to pick stocks (they never showed an edge, and the RSI cap was
# excluding genuine breakouts).
#
# A stock is only a candidate when it is a SETUP (just broke out of its
# 50-day high on volume, or is sitting within 3% below that high) AND its
# QUALITY SCORE is in the top half of the liquid universe that day.
QUALITY_GATE_PCT = _env_float("TRADING_QUALITY_GATE_PCT", 50)   # keep stocks at/above this percentile
BREAKOUT_LOOKBACK_SESSIONS = 50      # the "prior high" a breakout must clear
FRESH_MIN_GAIN = 0.02                # breakout day must be up at least 2%
FRESH_MIN_VOLUME_RATIO = 1.5         # projected full-day volume vs the previous 15-day average
FRESH_MAX_EXTENSION = 0.05           # not more than 5% past the broken high (still chaseable)
FRESH_MIN_CLOSE_POSITION = 0.60      # closing in the top 40% of the day's range
PRIMED_NEAR_PCT = 0.03               # "about to break": within 3% below the 50-day high
MAX_PICKS = {"btst": 8, "swing": 15}  # how many ranked picks a message carries

# Trade plan (same numbers the research simulated):
STOP_ATR_MULT = 2.5                  # initial stop = fill price x (1 - 2.5 x ATR%)
TRAIL_ATR_MULT = 5.0                 # chandelier trail: highest close x (1 - 5 x ATR%), only ever moves up
MAX_HOLD_SESSIONS = 60               # time stop
ENTRY_VALID_SESSIONS = 5             # a buy-stop order stays valid this many sessions
MAX_CHASE_PCT = 0.03                 # skip if the stock gaps more than 3% past the trigger
ROUND_TRIP_COST = 0.0035             # 0.35%: STT, stamp duty, exchange/DP charges, brokerage and slippage
# Position size is deliberately NOT computed: it depends on the money the reader still has free, which no scan can know
# (a fixed capital assumption gave wrong share counts as soon as other stocks were bought). The messages state the rule instead.
RISK_PER_TRADE_PCT = 1.0             # guidance in the message footer: risk at most this % of YOUR capital to the stop on one trade (half in a weak market)
WEAK_BREADTH = 0.40                  # share of stocks above their 50-day average below which the market is "weak"
HEALTHY_BREADTH = 0.60
COOLDOWN_SESSIONS = 15               # a stock that was picked is not picked again for this many sessions

# Warning on a pick card: "already spiked X% in 5 days - buy on a pull-back or in continuation".
# It only changes the wording of the message; the quality score already marks a 5-day spike down, and nothing is dropped.
# Made stricter on 2026-10-30 at the user's request (weak market): the warning fires sooner and "rising" is harder to claim.
# The 10-year replay of 2026-10-30 (reports/evidence/README.md, section 7) found NO profit difference between stricter and looser: picks flagged
# as "already spiked" did not do worse after costs at any cut-off tried, so these numbers only decide how often the line appears. They stay stricter.
SPIKE_5D_Z = 1.0                    # the gain is at least this many standard deviations above the average stock's 5-day gain that day (roughly the top sixth; was 1.28, the top tenth) ...
SPIKE_5D_MIN = 0.04                  # ... and at least this large in absolute terms, so a 3% week is never a spike, even on a very quiet day (was 5%)
TREND_200D_RISING_MIN = 0.01         # "200-day trend rising" is claimed only when the 200-day average is at least this much (1%) above its level 20 sessions ago (was 0.5%)

# Delivery-% feed (end-of-day public data; only the lagged 20-day average is used)
DELIVERY_MIN_COVERAGE = 0.60         # use delivery % in the score only if this share of the universe has it
DELIVERY_MAX_STALE_DAYS = 12         # ...and the newest delivery day is at most this many calendar days old

# On-time BTST: an evening run builds a watchlist, the afternoon run only has to re-check it
WATCHLIST_SIZE = 150                 # how many symbols the afternoon run fetches
WATCHLIST_MIN_QUALITY = 40.0         # a bit looser than the 50 gate: ranks move overnight
WATCHLIST_BAND = (-0.10, 0.05)       # close vs the 50-day high (incl. today): within 10% below ... 5% above
WATCHLIST_MAX_AGE_DAYS = 5           # an older watchlist is ignored (falls back to scanning the whole universe)
BTST_FETCH_DEADLINE_IST = (14, 25)   # the afternoon fetch stops here so the message lands before 2:30 PM
BTST_LATE_CUTOFF_IST = (14, 20)      # a SCHEDULED btst run that GitHub starts after this time is skipped (too late to act before the close)

# --- Scanner thresholds (legacy 7-filter engine; no longer used for selection) ---
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

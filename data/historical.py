"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.

Fetches are SEQUENTIAL. THREE safety mechanisms now protect a run:
  1. Consecutive rate-limit failures (15 in a row) - catches a hard block
  2. Rolling-window failure RATE (30%+ of the last 50 attempts) - catches
     scattered persistent throttling that never strings together 15 in
     a row but still burns huge time on a steady trickle of failures
  3. TIME BUDGET - during market hours Angel One can let requests
     through on attempt 2/3 (technically succeeding, just very slowly),
     which never trips #1 or #2 since nothing actually fails. Without
     this, the run just grinds until GitHub's external timeout kills it
     - an ABRUPT kill that loses even the symbols that DID succeed,
     since nothing gets returned/committed. This checks elapsed time
     and stops GRACEFULLY with a time budget to spare, returning
     whatever was fetched so far so the rest of the pipeline (scoring,
     reporting, committing) can still run on partial data instead of
     losing everything.
"""
from __future__ import annotations
import logging
import time
from collections import deque
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int, max_duration_seconds: int = 5400) -> dict[str, pd.DataFrame]:
        """
        max_duration_seconds default 5400 (90 min) - leaves a 30 min
        buffer within a 120 min workflow timeout for scanning, report
        generation, and the git commit step to still run on whatever
        was fetched, rather than everything being lost to an external
        kill mid-fetch.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        consecutive_blocked = 0
        breaker_threshold = 15

        window_size = 50
        rate_threshold = 0.30
        recent_outcomes: deque = deque(maxlen=window_size)

        start_time = time.time()
        time_budget_hit = False

        for i, symbol in enumerate(symbols, 1):
            elapsed = time.time() - start_time
            if elapsed > max_duration_seconds:
                logger.error(
                    f"Stopping early: time budget ({max_duration_seconds}s) reached after {i-1}/{total} "
                    f"symbols - this happens when requests are succeeding but very slowly (e.g. heavy "
                    f"market-hours throttling), which the failure-based circuit breakers don't catch. "
                    f"Using the {len(results)} symbols fetched so far rather than losing everything to "
                    f"an external timeout kill."
                )
                time_budget_hit = True
                break

            try:
                df = self.get_bars(symbol, lookback_days)
                if df is not None and not df.empty:
                    results[symbol] = df
                consecutive_blocked = 0
                recent_outcomes.append(False)
            except Exception as e:
                failures += 1
                is_blocked = "rate" in str(e).lower() or "access denied" in str(e).lower() or "too many" in str(e).lower()
                consecutive_blocked = consecutive_blocked + 1 if is_blocked else 0
                recent_outcomes.append(is_blocked)
                logger.warning(f"Skipping {symbol}: {e}")

                if consecutive_blocked >= breaker_threshold:
                    logger.error(
                        f"Stopping early: {consecutive_blocked} symbols in a row rejected on rate/access "
                        f"errors - account-level block/cooldown, not fixable by slower pacing. "
                        f"Got {len(results)}/{total} symbols before this happened."
                    )
                    break

                if len(recent_outcomes) == window_size:
                    window_fail_rate = sum(recent_outcomes) / window_size
                    if window_fail_rate >= rate_threshold:
                        logger.error(
                            f"Stopping early: {window_fail_rate:.0%} of the last {window_size} symbols were "
                            f"rate-limited (scattered, not consecutive) - sustained throttling makes the rest "
                            f"of this run mostly wasted retry time. Got {len(results)}/{total} symbols before "
                            f"this happened. Try again later."
                        )
                        break

            if i % 100 == 0 or i == total:
                elapsed_min = (time.time() - start_time) / 60
                logger.info(f"Fetched {i}/{total} symbols ({failures} failed so far, {elapsed_min:.1f} min elapsed)")

        if time_budget_hit:
            logger.info(f"Universe fetch stopped by time budget: {len(results)}/{total} symbols succeeded")
        elif failures:
            logger.info(f"Universe fetch complete: {len(results)}/{total} symbols succeeded")
        return results

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError


class ParquetStore(HistoricalStore):
    def __init__(self, root: str = "./market_data"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        return self.root / f"{symbol.upper()}.parquet"

    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        path = self._path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.read_parquet(path)
        return df.tail(lookback_days)

    def save_bars(self, symbol: str, df: pd.DataFrame):
        path = self._path(symbol)
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_parquet(path)


class TimescaleStore(HistoricalStore):
    def __init__(self, db_url: str):
        from sqlalchemy import create_engine
        self.engine = create_engine(db_url)

    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        query = """
            SELECT ts, open, high, low, close, volume
            FROM bars
            WHERE symbol = %(symbol)s
            ORDER BY ts DESC
            LIMIT %(n)s
        """
        df = pd.read_sql(query, self.engine, params={"symbol": symbol, "n": lookback_days})
        return df.set_index("ts").sort_index()

    def save_bars(self, symbol: str, df: pd.DataFrame):
        out = df.copy()
        out["symbol"] = symbol
        out.to_sql("bars", self.engine, if_exists="append", index_label="ts")


class AngelOneHistoricalStore(HistoricalStore):
    def __init__(self):
        import config
        from data.brokers.angelone_client import AngelOneDataClient

        self._client = AngelOneDataClient(
            config.ANGEL_API_KEY, config.ANGEL_CLIENT_ID,
            config.ANGEL_PASSWORD, config.ANGEL_TOTP_SECRET,
        )

    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        return self._client.get_historical_bars(symbol, days=lookback_days)

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError("AngelOneHistoricalStore is read-through; use ParquetStore to persist bars")

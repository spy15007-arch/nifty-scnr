"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.

NOTE: Universe fetches use a controlled 3-thread parallel pool. Thanks
to the thread lock in AngelOneDataClient, concurrent requests queue safely
without violating broker limits, speeding up scans to ~3-4 minutes.
"""
from __future__ import annotations
import logging
import time
import pandas as pd
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        """
        Fetches symbols concurrently using a small thread pool with controlled pacing,
        drastically reducing overall scan time while honoring broker limits.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        consecutive_blocked = 0
        breaker_threshold = 15
        
        # Use 3 concurrent workers to speed things up safely without overwhelming the API
        max_workers = 3

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(self.get_bars, symbol, lookback_days): symbol 
                for symbol in symbols
            }
            
            for i, future in enumerate(as_completed(future_to_symbol), 1):
                symbol = future_to_symbol[future]
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        results[symbol] = df
                    consecutive_blocked = 0
                except Exception as e:
                    failures += 1
                    is_blocked = "rate" in str(e).lower() or "access denied" in str(e).lower() or "ab1021" in str(e).lower()
                    consecutive_blocked = consecutive_blocked + 1 if is_blocked else 0
                    logger.warning(f"Skipping {symbol}: {e}")

                    if consecutive_blocked >= breaker_threshold:
                        logger.error(
                            f"Stopping early: {consecutive_blocked} symbols in a row rejected on rate/access "
                            f"errors - this looks like an account-level block or cooldown. Got {len(results)}/{total} symbols."
                        )
                        for f in future_to_symbol:
                            f.cancel()
                        break

                if i % 100 == 0 or i == total:
                    logger.info(f"Fetched {i}/{total} symbols ({failures} failed so far)")

        if failures:
            logger.info(f"Universe fetch complete: {len(results)}/{total} symbols succeeded")
        return results

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError


class ParquetStore(HistoricalStore):
    """
    Local-disk cache. IMPORTANT: only used for training/backtesting
    (see main.py's _get_training_store) - never for live scans. Using
    a frozen snapshot for a "live" scan means every scan mode analyzes
    the exact same stale data regardless of when it actually runs.
    """

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

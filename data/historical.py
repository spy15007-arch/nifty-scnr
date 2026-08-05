"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.
"""
from __future__ import annotations
import logging
import pandas as pd
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        """
        Fetches each symbol independently - one symbol's failure (rate
        limit, bad symbol, network blip) is logged and skipped rather
        than crashing the whole scan. At ~1500 symbols this matters:
        losing a handful of names to a transient error is fine, losing
        the entire run to one bad name is not.

        Circuit breaker: if many symbols in a row ALL fail on what looks
        like a rate-limit/access-denied error, that's no longer "one bad
        symbol" - it's the broker blocking the whole session (a cooldown,
        not something more pacing can fix). Stop early with a clear
        message rather than grinding through the rest of the universe
        hitting the same wall one-by-one for the full timeout window.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        consecutive_blocked = 0
        breaker_threshold = 15

        for i, symbol in enumerate(symbols, 1):
            try:
                results[symbol] = self.get_bars(symbol, lookback_days)
                consecutive_blocked = 0
            except Exception as e:
                failures += 1
                is_blocked = "rate" in str(e).lower() or "access denied" in str(e).lower()
                consecutive_blocked = consecutive_blocked + 1 if is_blocked else 0
                logger.warning(f"Skipping {symbol}: {e}")

                if consecutive_blocked >= breaker_threshold:
                    logger.error(
                        f"Stopping early: {consecutive_blocked} symbols in a row rejected on rate/access "
                        f"errors - this looks like an account-level block or cooldown, not something "
                        f"slower pacing can fix. Got {len(results)}/{total} symbols before this happened. "
                        f"Try again later rather than immediately - repeated retries during an active "
                        f"cooldown likely extend it."
                    )
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
    Simple local-disk store for development/backtesting before you
    stand up a real time-series DB. One parquet file per symbol.
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
        """df must have a DatetimeIndex and open/high/low/close/volume columns."""
        path = self._path(symbol)
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        df.to_parquet(path)


class TimescaleStore(HistoricalStore):
    """
    Production store. Requires: pip install sqlalchemy psycopg2-binary
    Expects a `bars` table: (symbol, ts, open, high, low, close, volume).
    """

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
    """
    Pulls bars live from Angel One's API each call instead of a DB -
    the right fit for a scheduled GitHub Action scan (no persistent
    infrastructure to maintain).
    """

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

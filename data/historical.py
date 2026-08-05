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
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0

        for i, symbol in enumerate(symbols, 1):
            try:
                results[symbol] = self.get_bars(symbol, lookback_days)
            except Exception as e:
                failures += 1
                logger.warning(f"Skipping {symbol}: {e}")

            if i % 100 == 0 or i == total:
                logger.info(f"Fetched {i}/{total} symbols ({failures} failed so far)")

        if failures:
            logger.info(f"Universe fetch complete: {total - failures}/{total} symbols succeeded")
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

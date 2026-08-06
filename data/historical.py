"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.
"""
from __future__ import annotations
import logging
import pandas as pd
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        """
        Fetches symbols using a bulletproof linear sequence. Insulates your retail 
        account completely from broker minute session freezes and IP blocks.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        
        logger.info(f"⚡ Ingesting {total} symbols via rate-insulated stream...")

        for i, symbol in enumerate(symbols, 1):
            try:
                # Mandatory 0.20-second pause creates a consistent 5 requests/sec pacing layout
                # This stays safely below Angel One's security firewall thresholds
                time.sleep(0.20) 
                
                df = self.get_bars(symbol, lookback_days)
                if df is not None and not df.empty:
                    results[symbol] = df
            except Exception as e:
                failures += 1
                if "rate" in str(e).lower() or "too many" in str(e).lower() or "ab1021" in str(e).lower():
                    logger.warning(f"⚠️ Account Cooldown Active. Pacing connection for {symbol}...")
                    time.sleep(1.0) # Adaptive recovery bridge brake delay
                    try:
                        df_retry = self.get_bars(symbol, lookback_days)
                        if df_retry is not None and not df_retry.empty:
                            results[symbol] = df_retry
                            failures -= 1
                    except Exception:
                        pass
                else:
                    logger.debug(f"Skipping {symbol}: {e}")

            if i % 50 == 0 or i == total:
                logger.info(f"📋 Indexing Progress: {i}/{total} symbols scanned ({failures} skipped)")

        logger.info(f"📊 Central Data Lake ready: {len(results)}/{total} assets cached in memory.")
        return results

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError


class ParquetStore(HistoricalStore):
    """Simple local-disk store for development/backtesting."""
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
    """Production store using TimescaleDB."""
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
    """Pulls bars live from Angel One's API each call."""
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

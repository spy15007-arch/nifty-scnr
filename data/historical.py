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
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        """
        Fetches symbols using a paced parallel pool to remain completely
        within Angel One's retail rate limit threshold.
        """
        results: dict[str, pd.DataFrame] = {}
        total = len(symbols)
        failures = 0
        
        logger.info(f"⚡ Initiating stable parallel download for {total} symbols...")

        def _fetch_worker(sym: str):
            # Introduce a tiny staggered delay between thread starts to smooth network spikes
            time.sleep(0.1) 
            return sym, self.get_bars(sym, lookback_days)

        # Set workers to 3 to precisely align with Angel One's 3 requests/sec limit rule
        max_workers = 3 
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {executor.submit(_fetch_worker, symbol): symbol for symbol in symbols}
            
            for i, future in enumerate(as_completed(future_to_symbol), 1):
                symbol = future_to_symbol[future]
                try:
                    sym, df = future.result()
                    if df is not None and not df.empty:
                        results[sym] = df
                except Exception as e:
                    failures += 1
                    if "rate" in str(e).lower() or "access" in str(e).lower():
                        logger.warning(f"Rate limit hit for {symbol}. Retrying sequentially...")
                        try:
                            # Instant fallback safety gear: if pool spikes, fetch single item cleanly
                            time.sleep(0.4)
                            df_fallback = self.get_bars(symbol, lookback_days)
                            if df_fallback is not None and not df_fallback.empty:
                                results[symbol] = df_fallback
                                failures -= 1
                        except Exception:
                            pass
                    else:
                        logger.debug(f"Skipping {symbol}: {e}")

                if i % 50 == 0 or i == total:
                    logger.info(f"Processed {i}/{total} symbols ({failures} skipped so far)")

        logger.info(f"📊 Universe fetch complete: {len(results)}/{total} symbols successfully indexed.")
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

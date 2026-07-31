"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path


class HistoricalStore:
    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_universe_bars(self, symbols: list[str], lookback_days: int) -> dict[str, pd.DataFrame]:
        return {s: self.get_bars(s, lookback_days) for s in symbols}

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


class BrokerHistoricalStore(HistoricalStore):
    """
    Pulls bars live from a broker API each call instead of a DB - the
    right fit for a scheduled GitHub Action scan (no persistent
    infrastructure to maintain). Supports Zerodha Kite, Angel One, and
    Dhan. Set `cross_check_broker` to fetch from a second broker too
    and flag mismatches, which catches bad data from either feed
    before it reaches the scanner.
    """

    def __init__(self, primary: str = "angelone", cross_check_broker: str | None = None):
        import config
        self.primary = primary
        self.cross_check_broker = cross_check_broker
        self._clients: dict[str, object] = {}

        for name in {primary, cross_check_broker} - {None}:
            self._clients[name] = self._make_client(name, config)

    @staticmethod
    def _make_client(name: str, config):
        if name == "kite":
            from data.brokers.kite_client import KiteDataClient
            return KiteDataClient(config.KITE_API_KEY, config.KITE_ACCESS_TOKEN)
        if name == "angelone":
            from data.brokers.angelone_client import AngelOneDataClient
            return AngelOneDataClient(
                config.ANGEL_API_KEY, config.ANGEL_CLIENT_ID,
                config.ANGEL_PASSWORD, config.ANGEL_TOTP_SECRET,
            )
        if name == "dhan":
            from data.brokers.dhan_client import DhanDataClient
            return DhanDataClient(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN)
        if name == "groww":
            from data.brokers.groww_client import GrowwDataClient
            return GrowwDataClient(config.GROWW_API_KEY, config.GROWW_API_SECRET)
        raise ValueError(f"Unknown broker: {name}")

    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        df = self._clients[self.primary].get_historical_bars(symbol, days=lookback_days)

        if self.cross_check_broker:
            check_df = self._clients[self.cross_check_broker].get_historical_bars(symbol, days=lookback_days)
            if not df.empty and not check_df.empty:
                latest_diff = abs(df["close"].iloc[-1] - check_df["close"].iloc[-1]) / df["close"].iloc[-1]
                if latest_diff > 0.02:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"{symbol}: {self.primary} vs {self.cross_check_broker} close differ by "
                        f"{latest_diff:.1%} - check feed"
                    )
        return df

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError("BrokerHistoricalStore is read-through; use ParquetStore to persist bars")

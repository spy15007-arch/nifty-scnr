"""
Historical OHLCV storage/retrieval. Same interface whether it's backed
by TimescaleDB, plain Postgres, or (for local dev) parquet files on
disk - swap HistoricalStore implementations without touching callers.

Fetches are SEQUENTIAL. Safety mechanisms protect a run:
  1. Consecutive failures (15 in a row) or a high failure RATE (30%+ of the
     last 50 attempts) mean the broker is throttling. The fetch no longer gives
     up at once: it COOLS DOWN (45 s, up to 3 times) and carries on, because
     the throttle is a per-minute window that a short pause clears. Only if the
     broker keeps refusing after that does it stop and return what it has.
  2. A SECOND PASS retries the stocks the broker refused the first time.
  3. TIME BUDGET - the run stops gracefully when its budget is spent and
     returns whatever was fetched, so scoring, reporting and committing still
     run on partial data instead of losing everything to an external kill.

After every call `last_report` says what happened (how many loaded, why it
stopped, the broker's most common replies), and the run note quotes it - a
partial scan is never left unexplained.
"""
from __future__ import annotations
import logging
import re
import time
from collections import Counter, deque
import pandas as pd
from pathlib import Path

from data.brokers.angelone_client import is_transient_error

logger = logging.getLogger(__name__)


def _short(error) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", str(error)))[:110]


class HistoricalStore:
    # behaviour knobs (class attributes so tests can shrink the waits)
    BREAKER_CONSECUTIVE = 15
    BREAKER_WINDOW = 50
    BREAKER_RATE = 0.30
    COOLDOWN_S = 45.0
    MAX_COOLDOWNS = 3
    RETRY_PAUSE_S = 10.0
    RETRY_MIN_BUDGET_S = 90.0
    RETRY_GIVE_UP_AFTER = 10

    last_report: dict | None = None

    def get_bars(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raise NotImplementedError

    def get_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """{symbol: {"ltp","open","high","low","prev_close","volume"}} - only stores with a live quote feed implement it."""
        raise NotImplementedError("this store has no live quote feed")

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
        start = time.time()
        errors: Counter = Counter()
        report = {"requested": total, "loaded": 0, "failed": 0, "stopped": "", "cooldowns": 0, "recovered": 0,
                  "seconds": 0, "errors": []}
        self.last_report = report

        def left() -> float:
            return max_duration_seconds - (time.time() - start)

        def attempt(symbol: str) -> str:
            """'ok' | 'empty' (the broker really has no candles) | 'transient' (throttled / unwell) | 'permanent'."""
            try:
                df = self.get_bars(symbol, lookback_days)
            except Exception as e:  # noqa: BLE001
                errors[_short(e)] += 1
                logger.warning(f"Skipping {symbol}: {e}")
                return "transient" if is_transient_error(e) else "permanent"
            if df is not None and not df.empty:
                results[symbol] = df
                return "ok"
            return "empty"

        failed_transient: list[str] = []
        consecutive = 0
        recent: deque = deque(maxlen=self.BREAKER_WINDOW)
        stopped = ""

        for i, symbol in enumerate(symbols, 1):
            if left() <= 0:
                stopped = "time budget"
                logger.error(
                    f"Stopping early: time budget ({max_duration_seconds}s) reached after {i - 1}/{total} symbols. "
                    f"Using the {len(results)} symbols fetched so far rather than losing everything to an external timeout."
                )
                break

            outcome = attempt(symbol)
            blocked = outcome == "transient"
            if blocked:
                failed_transient.append(symbol)
                consecutive += 1
            else:
                consecutive = 0
            recent.append(blocked)

            tripped = consecutive >= self.BREAKER_CONSECUTIVE or (
                len(recent) == self.BREAKER_WINDOW and sum(recent) / len(recent) >= self.BREAKER_RATE)
            if tripped:
                if report["cooldowns"] < self.MAX_COOLDOWNS and left() > self.COOLDOWN_S + 60:
                    report["cooldowns"] += 1
                    logger.warning(
                        f"The broker is refusing requests ({consecutive} in a row, {sum(recent)} of the last {len(recent)}) - "
                        f"cooling down {self.COOLDOWN_S:.0f}s, then continuing (cool-down {report['cooldowns']} of {self.MAX_COOLDOWNS}); "
                        f"{len(results)}/{total} loaded so far")
                    time.sleep(self.COOLDOWN_S)
                    consecutive = 0
                    recent.clear()
                else:
                    stopped = "the broker kept refusing requests"
                    logger.error(
                        f"Stopping early: the broker kept refusing requests after {report['cooldowns']} cool-down(s). "
                        f"Got {len(results)}/{total} symbols. Try again later.")
                    break

            if i % 100 == 0 or i == total:
                logger.info(f"Fetched {i}/{total} symbols ({len(results)} loaded, {len(failed_transient)} refused so far, "
                            f"{(time.time() - start) / 60:.1f} min elapsed)")

        # second pass: stocks the broker refused the first time, once the throttle has had a moment to clear
        if failed_transient and not stopped:
            retry = [s for s in failed_transient if s not in results]
            if retry and left() > self.RETRY_MIN_BUDGET_S:
                logger.info(f"Second pass: retrying {len(retry)} stocks the broker refused the first time")
                time.sleep(self.RETRY_PAUSE_S)
                misses = 0
                for symbol in retry:
                    if left() <= 0:
                        stopped = "time budget"
                        break
                    if attempt(symbol) == "ok":
                        report["recovered"] += 1
                        misses = 0
                    else:
                        misses += 1
                        if misses >= self.RETRY_GIVE_UP_AFTER:
                            break

        report.update(loaded=len(results), failed=total - len(results), stopped=stopped,
                      seconds=round(time.time() - start), errors=errors.most_common(3))
        if stopped:
            logger.info(f"Universe fetch stopped ({stopped}): {len(results)}/{total} symbols succeeded")
        elif len(results) < total:
            logger.info(f"Universe fetch complete: {len(results)}/{total} symbols succeeded"
                        f"{', ' + str(report['recovered']) + ' recovered in the second pass' if report['recovered'] else ''}")
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

    def get_quotes(self, symbols: list[str]) -> dict[str, dict]:
        return self._client.get_quotes(list(symbols))

    def save_bars(self, symbol: str, df: pd.DataFrame):
        raise NotImplementedError("AngelOneHistoricalStore is read-through; use ParquetStore to persist bars")

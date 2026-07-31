"""
Broker-agnostic live data feed. Wraps whichever vendor SDK you actually
install (alpaca-py, ib_insync, polygon-api-client, ...) behind one
interface so the rest of the app never imports a vendor SDK directly.

This keeps you from having to rewrite scanner/OMS code if you switch
data vendors later.
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Tick:
    symbol: str
    price: float
    size: int
    timestamp: float


@dataclass
class Bar:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: float


class LiveFeed:
    """
    Base interface. Implement `connect`, `subscribe`, `run` for your
    chosen vendor. Everything downstream (scanner, OMS) only talks to
    this interface, never the vendor SDK.
    """

    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self._tick_handlers: list[Callable[[Tick], Awaitable[None]]] = []
        self._bar_handlers: list[Callable[[Bar], Awaitable[None]]] = []

    def on_tick(self, handler: Callable[[Tick], Awaitable[None]]):
        self._tick_handlers.append(handler)

    def on_bar(self, handler: Callable[[Bar], Awaitable[None]]):
        self._bar_handlers.append(handler)

    async def _dispatch_tick(self, tick: Tick):
        for h in self._tick_handlers:
            await h(tick)

    async def _dispatch_bar(self, bar: Bar):
        for h in self._bar_handlers:
            await h(bar)

    async def connect(self):
        raise NotImplementedError

    async def run(self):
        raise NotImplementedError


class AlpacaLiveFeed(LiveFeed):
    """
    Real implementation for Alpaca. Requires: pip install alpaca-py
    Set TRADING_API_KEY / TRADING_API_SECRET env vars.
    """

    async def connect(self):
        try:
            from alpaca.data.live import StockDataStream
        except ImportError as e:
            raise RuntimeError(
                "alpaca-py not installed. Run: pip install alpaca-py"
            ) from e

        import config

        self._stream = StockDataStream(config.API_KEY, config.API_SECRET)

        async def _on_bar(vendor_bar):
            bar = Bar(
                symbol=vendor_bar.symbol,
                open=vendor_bar.open,
                high=vendor_bar.high,
                low=vendor_bar.low,
                close=vendor_bar.close,
                volume=vendor_bar.volume,
                timestamp=vendor_bar.timestamp.timestamp(),
            )
            await self._dispatch_bar(bar)

        self._stream.subscribe_bars(_on_bar, *self.symbols)

    async def run(self):
        await self._stream._run_forever()


def make_feed(symbols: list[str]) -> LiveFeed:
    """Factory - reads config.BROKER and returns the right implementation."""
    import config

    if config.BROKER == "alpaca":
        return AlpacaLiveFeed(symbols)
    raise NotImplementedError(f"No live feed implementation for {config.BROKER}")

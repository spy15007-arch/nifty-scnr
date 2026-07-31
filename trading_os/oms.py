"""
Order Management System. Tracks every position through a state
machine and is the ONLY place that talks to the broker for execution.
Scanner/AI code never touches the broker directly - it only ever
proposes SizedOrders, which flow through here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class PositionState(Enum):
    WATCHLIST = "watchlist"
    SIGNALED = "signaled"
    ORDER_PENDING = "order_pending"
    FILLED = "filled"
    MANAGED = "managed"     # open, being tracked (trailing stop etc.)
    CLOSED = "closed"


@dataclass
class Position:
    symbol: str
    state: PositionState
    shares: int = 0
    entry_price: float | None = None
    stop_price: float | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None
    history: list[str] = field(default_factory=list)

    def transition(self, new_state: PositionState, note: str = ""):
        self.history.append(f"{datetime.utcnow().isoformat()} {self.state.value} -> {new_state.value} {note}")
        self.state = new_state


class BrokerClient:
    """Wrap your broker SDK behind this interface (paper or live)."""

    def submit_order(self, symbol: str, shares: int, side: str, order_type: str = "market", limit_price: float | None = None) -> str:
        raise NotImplementedError

    def cancel_order(self, order_id: str):
        raise NotImplementedError

    def get_account_equity(self) -> float:
        raise NotImplementedError


class AlpacaBrokerClient(BrokerClient):
    """Requires: pip install alpaca-py"""

    def __init__(self):
        from alpaca.trading.client import TradingClient
        import config
        self.client = TradingClient(config.API_KEY, config.API_SECRET, paper=config.PAPER_TRADING)

    def submit_order(self, symbol, shares, side, order_type="market", limit_price=None) -> str:
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order = MarketOrderRequest(
            symbol=symbol,
            qty=shares,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        result = self.client.submit_order(order)
        return str(result.id)

    def cancel_order(self, order_id: str):
        self.client.cancel_order_by_id(order_id)

    def get_account_equity(self) -> float:
        return float(self.client.get_account().equity)


class OMS:
    def __init__(self, broker: BrokerClient):
        self.broker = broker
        self.positions: dict[str, Position] = {}

    def add_to_watchlist(self, symbol: str):
        self.positions[symbol] = Position(symbol=symbol, state=PositionState.WATCHLIST)

    def signal(self, symbol: str, note: str = ""):
        pos = self.positions.setdefault(symbol, Position(symbol=symbol, state=PositionState.WATCHLIST))
        pos.transition(PositionState.SIGNALED, note)

    def submit_entry(self, symbol: str, shares: int, stop_price: float) -> Position:
        pos = self.positions[symbol]
        pos.transition(PositionState.ORDER_PENDING, f"submitting {shares} shares")
        order_id = self.broker.submit_order(symbol, shares, "buy")
        pos.shares = shares
        pos.stop_price = stop_price
        pos.transition(PositionState.FILLED, f"order_id={order_id}")
        pos.opened_at = datetime.utcnow()
        pos.transition(PositionState.MANAGED, "now under active management")
        return pos

    def close_position(self, symbol: str, reason: str = ""):
        pos = self.positions[symbol]
        self.broker.submit_order(symbol, pos.shares, "sell")
        pos.closed_at = datetime.utcnow()
        pos.transition(PositionState.CLOSED, reason)

    def check_stops(self, current_prices: dict[str, float]):
        """Call on every price update; closes anything that broke its stop."""
        for symbol, pos in self.positions.items():
            if pos.state != PositionState.MANAGED:
                continue
            price = current_prices.get(symbol)
            if price is not None and pos.stop_price is not None and price <= pos.stop_price:
                self.close_position(symbol, f"stop hit at {price}")

"""
Portfolio state: what you hold, what it's worth, how exposed you are
by sector/factor. Reads from OMS positions, doesn't duplicate order
logic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd


@dataclass
class Holding:
    symbol: str
    shares: int
    entry_price: float
    current_price: float
    sector: str = "unknown"

    @property
    def market_value(self) -> float:
        return self.shares * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.entry_price) * self.shares

    @property
    def unrealized_pnl_pct(self) -> float:
        return (self.current_price / self.entry_price - 1) if self.entry_price else 0.0


class PortfolioManager:
    def __init__(self, cash: float):
        self.cash = cash
        self.holdings: dict[str, Holding] = {}
        self.closed_trades: list[dict] = []

    @property
    def equity(self) -> float:
        return self.cash + sum(h.market_value for h in self.holdings.values())

    def open_position(self, symbol: str, shares: int, price: float, sector: str = "unknown"):
        self.cash -= shares * price
        self.holdings[symbol] = Holding(symbol, shares, price, price, sector)

    def update_price(self, symbol: str, price: float):
        if symbol in self.holdings:
            self.holdings[symbol].current_price = price

    def close_position(self, symbol: str, price: float):
        h = self.holdings.pop(symbol, None)
        if not h:
            return
        proceeds = h.shares * price
        self.cash += proceeds
        self.closed_trades.append({
            "symbol": symbol, "shares": h.shares, "entry": h.entry_price,
            "exit": price, "pnl": (price - h.entry_price) * h.shares,
            "pnl_pct": price / h.entry_price - 1,
        })

    def sector_exposure(self) -> pd.Series:
        if not self.holdings:
            return pd.Series(dtype=float)
        df = pd.DataFrame([{"sector": h.sector, "value": h.market_value} for h in self.holdings.values()])
        return df.groupby("sector")["value"].sum() / self.equity

    def summary(self) -> dict:
        total_pnl = sum(h.unrealized_pnl for h in self.holdings.values())
        realized_pnl = sum(t["pnl"] for t in self.closed_trades)
        return {
            "equity": self.equity,
            "cash": self.cash,
            "n_positions": len(self.holdings),
            "unrealized_pnl": total_pnl,
            "realized_pnl": realized_pnl,
        }

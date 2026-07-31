"""
Risk engine sits between "signal" and "order" - nothing reaches the
OMS without passing through here. This is the part that keeps one bad
model output from blowing up the account.
"""
from __future__ import annotations
from dataclasses import dataclass
import config


@dataclass
class SizedOrder:
    symbol: str
    shares: int
    stop_price: float
    risk_dollars: float
    reason: str


class RiskEngine:
    def __init__(self, equity: float, open_positions: dict[str, dict]):
        self.equity = equity
        self.open_positions = open_positions  # symbol -> {sector, market_value, ...}

    def position_size(self, symbol: str, entry_price: float, atr: float, sector: str) -> SizedOrder | None:
        if len(self.open_positions) >= config.MAX_POSITIONS:
            return None

        sector_exposure = sum(
            p["market_value"] for p in self.open_positions.values() if p.get("sector") == sector
        )
        if (sector_exposure / self.equity) >= config.MAX_SECTOR_EXPOSURE_PCT:
            return None

        stop_distance = atr * config.DEFAULT_STOP_ATR_MULT
        if stop_distance <= 0:
            return None

        risk_dollars = self.equity * config.MAX_RISK_PER_TRADE_PCT
        shares = int(risk_dollars / stop_distance)
        if shares <= 0:
            return None

        stop_price = entry_price - stop_distance
        return SizedOrder(
            symbol=symbol,
            shares=shares,
            stop_price=round(stop_price, 2),
            risk_dollars=round(risk_dollars, 2),
            reason=f"{config.MAX_RISK_PER_TRADE_PCT:.1%} equity risk, stop at {config.DEFAULT_STOP_ATR_MULT}x ATR",
        )

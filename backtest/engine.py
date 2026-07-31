"""
Event-driven backtester. Walks forward bar-by-bar and calls the SAME
scanner engine, risk engine, and AI model used live - this is the
single most important design choice in the whole system. A backtest
that runs different logic than production tells you nothing.

Not vectorized on purpose: vectorized backtests are fast but leak
future information constantly unless you're extremely careful. This
is slower but honest.
"""
from __future__ import annotations
import pandas as pd
from dataclasses import dataclass, field

from scanner.engine import ScannerEngine
from trading_os.risk import RiskEngine
from portfolio.manager import PortfolioManager
from ai.features import build_features
import config


@dataclass
class BacktestResult:
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    dates: list = field(default_factory=list)


class Backtester:
    def __init__(self, bars_by_symbol: dict[str, pd.DataFrame], benchmark_df: pd.DataFrame,
                 model=None, min_probability: float = 0.55):
        self.bars_by_symbol = bars_by_symbol
        self.benchmark_df = benchmark_df
        self.scanner = ScannerEngine()
        self.model = model  # optional trained BreakoutModel; None = scanner-score-only mode
        self.min_probability = min_probability
        self.portfolio = PortfolioManager(cash=config.BACKTEST_START_EQUITY)

    def _fill_price(self, raw_price: float, side: str) -> float:
        slip = raw_price * (config.SLIPPAGE_BPS / 10_000)
        return raw_price + slip if side == "buy" else raw_price - slip

    def run(self, start_idx: int = 100) -> BacktestResult:
        result = BacktestResult()
        all_dates = sorted(set().union(*[set(df.index) for df in self.bars_by_symbol.values()]))[start_idx:]

        for date in all_dates:
            # 1. mark to market
            for symbol, holding in list(self.portfolio.holdings.items()):
                df = self.bars_by_symbol.get(symbol)
                if df is not None and date in df.index:
                    price = df.loc[date, "close"]
                    self.portfolio.update_price(symbol, price)
                    if price <= holding.entry_price - self._atr_stop(symbol, date):
                        fill = self._fill_price(price, "sell")
                        self.portfolio.close_position(symbol, fill)

            # 2. scan as of this date (only using data up to `date` - no lookahead)
            snapshot = {s: df[df.index <= date] for s, df in self.bars_by_symbol.items()}
            bench_snapshot = self.benchmark_df[self.benchmark_df.index <= date]
            candidates = self.scanner.scan_universe(snapshot, bench_snapshot, top_n=10)

            # 3. filter through AI model if provided, then risk-size and enter
            for cand in candidates:
                if cand.symbol in self.portfolio.holdings:
                    continue
                df = snapshot[cand.symbol]
                if len(df) < 60:
                    continue

                if self.model is not None:
                    feats = build_features(df, bench_snapshot)
                    if any(pd.isna(v) for v in feats.values()):
                        continue
                    prob = self.model.predict_proba(feats)
                    if prob < self.min_probability:
                        continue

                risk_engine = RiskEngine(
                    equity=self.portfolio.equity,
                    open_positions={s: {"market_value": h.market_value, "sector": h.sector}
                                     for s, h in self.portfolio.holdings.items()},
                )
                atr = self._atr_stop(cand.symbol, date, raw=True)
                entry_price = df["close"].iloc[-1]
                sized = risk_engine.position_size(cand.symbol, entry_price, atr, sector="unknown")
                if sized is None:
                    continue

                fill = self._fill_price(entry_price, "buy")
                self.portfolio.open_position(cand.symbol, sized.shares, fill)

            result.equity_curve.append(self.portfolio.equity)
            result.dates.append(date)

        result.trades = self.portfolio.closed_trades
        return result

    def _atr_stop(self, symbol: str, date, raw: bool = False) -> float:
        from scanner.filters import _atr
        df = self.bars_by_symbol[symbol]
        df = df[df.index <= date]
        if len(df) < 15:
            return 0.0
        atr = _atr(df).iloc[-1]
        return atr if raw else atr * config.DEFAULT_STOP_ATR_MULT


def compute_metrics(result: BacktestResult) -> dict:
    equity = pd.Series(result.equity_curve, index=result.dates)
    returns = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1
    sharpe = (returns.mean() / returns.std()) * (252 ** 0.5) if returns.std() > 0 else 0.0
    drawdown = (equity / equity.cummax() - 1).min()
    win_trades = [t for t in result.trades if t["pnl"] > 0]
    win_rate = len(win_trades) / len(result.trades) if result.trades else 0.0

    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": drawdown,
        "n_trades": len(result.trades),
        "win_rate": win_rate,
    }

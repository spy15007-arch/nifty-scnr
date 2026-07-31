# Trading Platform Scaffold

A modular scaffold for: live market data → scanner → AI recommendations
→ trading OS/OMS → portfolio management → backtesting → reports.

## Design principle

The scanner, risk sizing, and feature-engineering code are used
**identically** in live trading and in the backtester (`backtest/engine.py`
imports directly from `scanner/engine.py`, `trading_os/risk.py`, and
`ai/features.py`). This is intentional — it's the only way a backtest
result means anything. Never fork the logic between "live version" and
"backtest version."

## Layout

```
config.py           # every tunable threshold lives here
data/
  live_feed.py       # broker-agnostic live data interface (Alpaca impl included)
  historical.py       # bar storage (Parquet for dev, TimescaleDB for prod)
scanner/
  filters.py            # individual pre-breakout signal detectors
  engine.py               # combines filters into ranked candidates
trading_os/
  risk.py                    # position sizing, exposure limits
  oms.py                       # order state machine + broker execution
ai/
  features.py                    # feature engineering (shared train/live)
  model.py                         # calibrated breakout-probability classifier
  explain.py                         # plain-language "why" for each pick
portfolio/
  manager.py                           # holdings, P&L, sector exposure
backtest/
  engine.py                              # event-driven backtester
reports/
  generator.py                             # scan / portfolio / calibration reports
main.py              # CLI: scan | train | backtest
```

## What "pre-breakout" detection actually does here

The scanner looks for signals that tend to precede a *visible* breakout:
volatility contraction, rising relative volume, volume creeping up on
quiet/down days (accumulation), price coiling near resistance, and
relative strength vs. a benchmark. These shift probability — they do
not predict the future. The `explain.py` module exists specifically so
every AI output comes with its reasoning and caveats attached, never a
bare "buy" signal.

The **calibration report** (`reports/generator.py::calibration_report`)
is the most important report in the system — it tells you whether a
"70% probability" pick is actually hitting ~70% of the time. Without
it you have no idea if the model is any good.

## Getting started

```bash
pip install -r requirements.txt
export TRADING_API_KEY=...
export TRADING_API_SECRET=...

# 1. Backfill historical data into ./market_data/ (you'll need to write
#    a backfill script against your vendor - not included, since it's
#    100% vendor-specific)

# 2. Train the model (needs real history first)
python main.py train

# 3. Run today's scan
python main.py scan

# 4. Backtest before ever going live
python main.py backtest --use-model
```

## What's a stub vs. what's real logic

**Real, functional logic:** filters, scanner scoring, risk sizing, OMS
state machine, portfolio math, backtester mechanics, report generation.

**Stubs you must fill in for your setup:**
- `data/live_feed.py` — Alpaca implementation included as a template;
  swap for your vendor
- Historical backfill script (not included — pull bars from your vendor
  into `ParquetStore` or `TimescaleStore`)
- `UNIVERSE` list in `main.py` — replace the placeholder tickers
- Sector data — positions currently default to `sector="unknown"`; wire
  in real sector/industry classification for exposure limits to mean
  anything

## Honest limitations

- No amount of scanning finds moves "before retail" with certainty —
  these are statistical tilts, not inside information
  ("dark pool prints" / unusual options flow require a paid data feed
  not wired in here — flagged as a natural next filter to add)
- The model needs meaningful historical data (many symbols × years) to
  train something that isn't just noise — don't trust it on a small
  universe
- This is not financial advice infrastructure — position sizing and
  risk defaults are conservative starting points, not guarantees

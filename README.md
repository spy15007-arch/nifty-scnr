# NIFTY Scanner

An automated NSE stock scanner. Twice a day it reads end-of-day and live prices from Angel One, picks the stocks that are **about to break out of a 50-day high, or just did so on heavy volume** - and only the good-quality ones - and sends each pick to Telegram with a complete plan: where to buy, where the stop is, how the stop trails, and how many shares fit a 1% risk.

It is a **ranked shortlist, not a guarantee.** Replayed on seven years of real NSE data the rule won about 4 trades in 10 and its profit came from a few big winners; the edge was **strong in 2019-2023 and about break-even after costs in 2024-2026**, and it is weakest when the market is weak (details and numbers in [reports/evidence](reports/evidence/README.md)). Judge it over 30+ closed trades - the scoreboard does that for you.

## What you get

| Scan | When | What it is |
| :-- | :-- | :-- |
| 🌙 **BTST** | about 2:00 PM IST, before the close | Fresh breakouts to **buy now, near the close**, plus stocks about to break out (buy-stop orders). Re-checks last evening's ~150-stock watchlist with live prices, so it finishes in minutes. |
| 📈 **Swing** | about 4:05 PM IST, after the close | The full-market scan on the day's final candles: buy-stop orders for tomorrow. Also builds tomorrow's BTST watchlist and refreshes the delivery-% data. |
| 🧭 **Market direction** | button | Which way Nifty/Bank Nifty and global cues lean (also a one-line header on both scans). |

Index-option strike alerts are not part of the automatic run (the strike has moved by the time a scan reports). `python main.py options` still exists for manual use.

## How a stock gets picked

A stock must pass **both** tests (`scanner/pipeline.py`, `scanner/footprint.py`):

1. **Setup** - it is doing something actionable right now:
   * 🔥 **Fresh breakout** - closed above its prior 50-day high, up at least 2% on the day, on at least 1.5x its normal volume (projected to a full day while the market is open), closing in the top 40% of the day's range, and not more than 5% past the broken level.
   * 👀 **About to break out** - within 3% below that 50-day high.
2. **Quality** - it is the kind of stock that tends to keep going: in the **top half** of the day's liquid stocks (a filter, not a ladder - a higher rank inside the top half did not give better results in the back-test) on the average of eight traits - closeness to the 52-week high, rising 200-day trend, strong 12-month return, low volatility, not stretched above its 21-day average, no sharp 5-day spike, high liquidity, and **high delivery %** (the share of traded shares actually taken home: the closest free proxy for institutions accumulating, from NSE's public end-of-day file).

There is **no price cap.** Liquidity is the filter (20-day average turnover of at least ₹5 crore), because that is what lets big money in.

## The trade plan on every pick

* **Entry** - *Buy now* near the close (live fresh breakouts), or a **buy-stop** just above the breakout level, valid 5 sessions (skip it if the stock opens more than 3% above the trigger).
* **Stop** - 2.5 × ATR below the entry. **Trailing stop** - 5 × ATR below the highest close, raised daily, never lowered. **No fixed target**: in the history, small targets and partial profit-taking made results worse; the money came from the minority of trades that ran for weeks.
* **Time limit** - 60 sessions. **Size** - risk 1% of capital (half in a weak market, when under 40% of stocks are above their 50-day average), no position above 20% of capital.
* **Costs** - the scoreboard deducts 0.35% round trip.

## The scoreboard (`tracking/trades.csv`)

Every pick is logged and followed exactly by the plan, then judged on **closed** trades after costs, in R (a −1R trade lost the amount risked, +3R gained three times it). A buy-stop that never triggered is neither a win nor a loss. The ledger is replayed from each signal date over completed sessions only, so updating is safe to repeat and a half-finished day can never corrupt it. The old v1 ledgers are kept as `tracking/legacy_v1_*.csv`.

## Files and dashboard

| File | Content |
| :-- | :-- |
| `scan_results_btst.csv`, `scan_results_swing.csv` | latest picks with the whole plan (dated copies in `reports/<mode>/`) |
| `summary_btst.md`, `summary_swing.md`, `summary.md` | latest write-ups (the master file keeps the newest 24) |
| `tracking/trades.csv` | the scoreboard ledger |
| `data_cache/` | delivery-% cache, the BTST watchlist, run markers |
| `reports/output/` | backtest and live-scoreboard reports |

`streamlit run app.py` shows picks, open trades with their current stops, and the scoreboard by quality bucket / setup / market regime.

## Running it

GitHub Actions workflows (all have a **Run workflow** button): **BTST Scan**, **Swing Scan**, **Run All Scans Manual Override**, **Market Direction (Manual)**, **Backtest & Calibrate**.

**On-time scheduling:** GitHub's own cron is often hours late, so an external timer starts the scans on the dot - setup in [docs/TIMER_SETUP.md](docs/TIMER_SETUP.md). GitHub's schedule remains as a backup; the program skips a duplicate or a too-late BTST run (`scanner/runguard.py`).

Repository **secrets**: `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never put them in code or commit them; the logs mask them. Optional repository **variable**: `TRADING_CAPITAL_INR` (the capital the share counts assume; default ₹1 lakh).

Locally: `python main.py scan_swing | scan_btst | run_all | market | calibrate | backtest` (needs the same environment variables). `python -m unittest discover tests` runs the self-contained tests (no network, no broker).

## Layout

```
config.py                 every tunable number, with the reason for it
main.py                   commands and the run flow (fetch -> track -> select -> files -> Telegram)
scanner/
  footprint.py              features, setups, quality score
  pipeline.py               ranking, trade plan, sizing, watchlist
  tradesim.py               the trade state machine (scoreboard AND backtest use it)
  session.py                NSE session clock, time-of-day volume projection
  runguard.py               skip duplicate / too-late scheduled runs
  market_direction.py, global_cues.py, sectors.py
data/                     Angel One client, universe (NIFTY 500), NSE delivery-% feed
tracking/outcomes.py      the scoreboard
backtest/engine.py        replays the rule on broker data; live-scoreboard report
reports/                  Telegram formatter, file writers, evidence
app.py                    Streamlit dashboard
docs/TIMER_SETUP.md       on-time timer setup
tests/                    self-contained tests
```

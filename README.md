# NIFTY Scanner

An automated NSE stock scanner. Twice a day it reads end-of-day and live prices from Angel One, picks the stocks that are **about to break out of a 50-day high, or just did so on heavy volume** - and only the good-quality ones - and sends each pick to Telegram as a short card with the plan: where to buy, where the stop is, and how far the stop trails.

It is a **ranked shortlist, not a guarantee.** Replayed on seven years of real NSE data the rule won about 4 trades in 10 and its profit came from a few big winners; the edge was **strong in 2019-2023 and about break-even after costs in 2024-2026**, and it is weakest when the market is weak (details and numbers in [reports/evidence](reports/evidence/README.md)). Judge it over 30+ closed trades - the scoreboard does that for you.

## What you get

| Scan | When | What it is |
| :-- | :-- | :-- |
| 🌙 **BTST** | about 2:00 PM IST, before the close | Fresh breakouts to **buy now, near the close**, plus stocks about to break out (buy-stop orders). Re-checks last evening's watchlist (up to 150 stocks) with live prices: the evening run saves their candles, so the afternoon run needs only a few live-quote calls and finishes in minutes. |
| 📈 **Swing** | about 4:05 PM IST, after the close | The full-market scan on the day's final candles: buy-stop orders for tomorrow. Also builds tomorrow's BTST watchlist (and saves its candles) and refreshes the delivery-% data. It fetches about 500 stocks one by one at the broker's pace: roughly 10 minutes on a quiet day, up to 40 when the broker is busy and refuses many requests. |
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
* **Time limit** - 60 sessions. **Size** - deliberately not computed: how many shares you can buy depends on the money you still have free, which no scan knows. The rule is to risk at most 1% of *your* capital to the stop (half that in a weak market, when under 40% of stocks are above their 50-day average), so shares = the money you are willing to lose ÷ (entry − stop).
* **Costs** - the scoreboard deducts 0.35% round trip.

## The scoreboard (`tracking/trades.csv`)

Every pick is logged and followed exactly by the plan, then judged on **closed** trades after costs, in R (a −1R trade lost the amount risked, +3R gained three times it). A buy-stop that never triggered is neither a win nor a loss. The ledger is replayed from each signal date over completed sessions only, so updating is safe to repeat and a half-finished day can never corrupt it. The old v1 ledgers are kept as `tracking/legacy_v1_*.csv`.

## What the Telegram message looks like

Short on purpose - only what is needed to act, never a share count or an amount to invest (that depends on the money you still have free):

```
📈 Swing scan · Tue 27 Oct · today's close
🧭 Market: 🔴 Bearish · Nifty -0.2% today · global neutral
📊 Breadth 18% (stocks above their 50-day average) - weak market: half size

BUY-STOP ORDERS (GTT / stop-limit, valid 5 sessions)

1. BHARTIARTL 👀 About to break out (1.1% below the 50-day high) · Quality 79/100
Buy at 2,009.85 · skip if it opens above 2,070.10
Stop 1,881.00 (-6.4%) · trail 12.8%
near 52-wk high · low volatility · delivery ~46%

2. GLAND 👀 About to break out (2.1% below the 50-day high) · Quality 86/100 · Pharma
Buy at 3,177.35 · skip if it opens above 3,272.65
Stop 2,905.10 (-8.6%) · trail 17.1%
near 52-wk high · very liquid · delivery ~54%
⚠️ 200-day trend rising, but already spiked 8% in 5 days - buy on a pull-back or in continuation
```

* On a buy-stop card the first line says **Buy at** and the line under it is the **Stop** (the stop-loss); the heading above the cards says they are entered as buy-stop / GTT trigger orders (a plain limit order at that price would fill at once, below the trigger).
* A **🔥 buy-now** card (BTST, live breakout) reads *Buy near 848.15 · up 3.4% today on 2.1x volume*.
* A **⚠️** line is added only when something specific is going on. The one that tells you what to do: **"already spiked X% in 5 days - buy on a pull-back or in continuation"**, i.e. do not chase it. It appears when the gain is at least 5% **and** at least 1.28 standard deviations above the average stock's 5-day gain that day (roughly the top 10%; `SPIKE_5D_Z`, `SPIKE_5D_MIN` in `config.py`), and it begins with *"200-day trend rising, but ..."* when the 200-day average is at least 0.5% above where it was 20 sessions ago (`TREND_200D_RISING_MIN`). For a fresh breakout its own big day is the signal, not a spike, so the run-up is measured up to the previous close (*"... spiked 9% before today"*). Other warnings: already more than 3% past the breakout level, a very volatile stock. This is a caution, not a tested rule: the 5-day spike is one of the weaker traits in the research (see [reports/evidence](reports/evidence/README.md)) and pull-back entries were not back-tested. The full list of strengths and weak points of every pick is in the dated write-up `reports/<mode>/scan_<date>.md`.
* The evening **Swing** message ends with **📒 Your list**: what filled, expired or stopped out, every open trade's current stop (↑ = raised today) and the buy-stops still waiting. The afternoon **BTST** message leaves the stop list out (stops only change after a completed session) and shows only what changed.
* The last two lines are the live record (closed trades only) and the standing rule. The back-test numbers are in [reports/evidence](reports/evidence/README.md), the README and the dashboard, not repeated in every message.

## Files and dashboard

| File | Content |
| :-- | :-- |
| `scan_results_btst.csv`, `scan_results_swing.csv` | latest picks with the whole plan (dated copies in `reports/<mode>/`) |
| `summary_btst.md`, `summary_swing.md`, `summary.md` | latest write-ups (the master file keeps the newest 24) |
| `tracking/trades.csv` | the scoreboard ledger |
| `data_cache/` | delivery-% cache, the BTST watchlist and its saved candles (`btst_history.csv.gz`), run markers |
| `reports/output/` | backtest and live-scoreboard reports, and `fetch_report_btst.json` / `fetch_report_swing.json`: what the latest run's data fetch did (see below) |

`streamlit run app.py` shows picks, open trades with their current stops, and the scoreboard by quality bucket / setup / market regime.

## When a scan says "Partial scan"

The scans get their prices from Angel One, which now and then refuses requests. Each scan therefore says plainly what it got: *"Partial scan: 448 of 501 stocks loaded - the 45-minute limit was reached, so some names may be missing"*, plus the broker's own words when it refused (which stocks the broker does not list is in the fetch report, not the message). How it copes (`data/brokers/angelone_client.py`, `data/historical.py`):

* an answer of "not found" / "invalid ..." is final for that stock (no retries, no slowdown); a refused request is retried after a short pause (1 s, then 2 s; longer if the broker said "rate") and does **not** slow the calls that follow - the pace only drops while the broker is refusing *right now* (3+ refusals in a row, or a third of the last 20 calls) and is back to full speed about 20 calls after it recovers;
* a real outage (15 refused stocks in a row) triggers a 45-second cool-down, up to three times, then the run carries on; stocks refused the first time get a second pass;
* the fetch stops after 45 minutes and the scan runs on what it has; stocks with an open tracked trade and last evening's watchlist are fetched first, so a cut-short run still has the ones that matter;
* the afternoon BTST needs only a few live-quote calls (not one per stock) and gets the market line from Yahoo Finance first, because the broker's candle endpoint is the unreliable one during market hours.

Every run also commits `reports/output/fetch_report_<scan>.json` - route used (`quotes` or `candles`), stocks asked for and loaded, seconds, cool-downs, never-reached and refused counts, the broker's most common replies, and how many calls were slowed - so a slow or short scan can be explained without opening the run log.

## Running it

GitHub Actions workflows (all have a **Run workflow** button): **BTST Scan**, **Swing Scan**, **Run All Scans Manual Override**, **Market Direction (Manual)**, **Backtest & Calibrate**.

**On-time scheduling:** GitHub's own cron is often hours late, so an external timer starts the scans on the dot - setup in [docs/TIMER_SETUP.md](docs/TIMER_SETUP.md). GitHub's schedule remains as a backup; the program skips a duplicate or a too-late BTST run (`scanner/runguard.py`).

Repository **secrets**: `ANGEL_API_KEY`, `ANGEL_CLIENT_ID`, `ANGEL_PASSWORD`, `ANGEL_TOTP_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Never put them in code or commit them; the logs mask them.

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

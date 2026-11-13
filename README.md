# NIFTY Scanner

An automated NSE stock scanner. Twice a day it reads end-of-day and live prices from Angel One, picks the stocks that are **about to break out of a 50-day high, or just did so on heavy volume** - and only the good-quality ones - and sends each pick to Telegram as a short card with the plan: where to buy, where the stop is, and how far the stop trails.

It is a **ranked shortlist, not a guarantee.** Replayed on seven years of real NSE data the rule won about 4 trades in 10 and its profit came from a few big winners; the edge was **strong in 2019-2023 and about break-even after costs in 2024-2026**, and it is weakest when the market is weak (details and numbers in [reports/evidence](reports/evidence/README.md)). Judge it over 30+ closed trades - the scoreboard does that for you.

## What you get

| Scan | When | What it is |
| :-- | :-- | :-- |
| 🌙 **BTST** | about 2:45 PM IST, before the close | Fresh breakouts to **buy now, near the close**, plus stocks about to break out (buy orders that trigger on the way up). Re-checks last evening's watchlist (up to 150 stocks) with live prices: the evening run saves their candles, so the afternoon run needs only a few live-quote calls and finishes in minutes. |
| 📈 **Swing** | about 4:05 PM IST, after the close | The full-market scan on the day's final candles: buy orders for tomorrow (each triggers only if the price rises to its Buy at price). Also builds tomorrow's BTST watchlist (and saves its candles) and refreshes the delivery-% data. It fetches about 500 stocks one by one at the broker's pace: 10-17 minutes on a quiet day (a Sunday, a market holiday), but 42-49 minutes on a normal weekday evening, when the broker refuses about a third of the requests at random (the limit is 75 minutes; see "When a scan says Partial scan" below), so its message arrives at about 5 PM. |
| 🧭 **Market direction** | button | Which way Nifty/Bank Nifty and global cues lean (also a one-line header on both scans). |

Index-option strike alerts are not part of the automatic run (the strike has moved by the time a scan reports). `python main.py options` still exists for manual use.

## How a stock gets picked

A stock must pass **both** tests (`scanner/pipeline.py`, `scanner/footprint.py`):

1. **Setup** - it is doing something actionable right now:
   * 🔥 **Fresh breakout** - closed above its prior 50-day high, up at least 2% on the day, on at least 1.5x its normal volume (projected to a full day while the market is open), closing in the top 40% of the day's range, and not more than 5% past the broken level.
   * 👀 **About to break out** - within 3% below that 50-day high.
2. **Quality** - it is the kind of stock that tends to keep going: in the **top half** of the day's liquid stocks (a filter, not a ladder - a higher rank inside the top half did not give better results in the back-test) on the average of eight traits - closeness to the 52-week high, rising 200-day trend, strong 12-month return, low volatility, not stretched above its 21-day average, no sharp 5-day spike, high liquidity, and **high delivery %** (the share of traded shares actually taken home: the closest free proxy for institutions accumulating, from NSE's public end-of-day file).

There is **no price cap.** Liquidity is the filter (20-day average turnover of at least ₹5 crore), because that is what lets big money in.

**Order on the list:** stocks in a **💪 strong uptrend** (200-day average up 5%+ in a month, explained below) first, then fresh breakouts before about-to-break stocks, then by quality. The order only matters for how the list reads and for which picks stay when more stocks qualify than a message can carry (15 swing, 8 BTST - rare); it never adds a stock. A stock picked on an earlier day whose buy order is still waiting, and that still ranks above today's new picks, is shown in a separate reminder block above them (see "What the Telegram message looks like"); it is never picked, or counted, a second time.

## The trade plan on every pick

* **Entry** - *Buy now* near the close (live fresh breakouts), or a **buy order** (a GTT / stop-limit order - "buy-stop" in trading terms) just above the breakout level, valid 5 sessions (skip it if the stock opens more than 3% above the trigger).
* **Stop** - 2.5 × ATR below the entry. **Trailing stop** - 5 × ATR below the highest close, raised daily, never lowered. **No fixed target**: in the history, small targets and partial profit-taking made results worse; the money came from the minority of trades that ran for weeks.
* **Time limit** - 60 sessions. **Size** - deliberately not computed: how many shares you can buy depends on the money you still have free, which no scan knows. The rule is to risk at most 1% of *your* capital to the stop (half that in a weak market, when under 40% of stocks are above their 50-day average), so shares = the money you are willing to lose ÷ (entry − stop). **The whole list is more than one account can hold:** on 5 Nov 2026 the 29 bought and 23 waiting stocks needed about 370% of the capital at that rule, so when the list needs more than all of it the footer of each message says so, and the dashboard's **💼 Capital plan** sizes the list for *your* capital and the most stocks you want to hold, in the scanner's order, saying which to take and which to skip. In the 10-year replay an account that could hold only 6-10 stocks grew about 10-12% a year with 13-23% drawdowns (the per-signal averages elsewhere describe an account that cannot exist - [reports/evidence](reports/evidence/README.md) section 7, item 8). A smaller daily cap on new picks was replayed too and does not help: no cap earned clearly more, and even one pick a day would still leave a list needing about 200% of the capital (item 10).
* **Costs** - the scoreboard deducts 0.35% round trip.

## The scoreboard (`tracking/trades.csv`)

Every pick is logged and followed exactly by the plan, then judged on **sold (finished)** trades after costs, in R (a −1R trade lost the amount risked, +3R gained three times it). A buy order that never triggered is neither a win nor a loss. The ledger is replayed from each signal date over completed sessions only, so updating is safe to repeat and a half-finished day can never corrupt it. The old v1 ledgers are kept as `tracking/legacy_v1_*.csv`.

Since 31 Oct 2026 each new pick also records what the stock had done before the signal - `trend200` (the 200-day average vs 20 sessions earlier), `run_up` and `z_run_up` (the 5-day gain the spike warning measures, in % and in standard deviations above the average stock), and the two flags the card showed, `strong_trend` and `spiked`. The live scoreboard report (`reports/output/live_scoreboard.md`) and the dashboard use them to compare 💪 strong-uptrend picks with the rest, and ⚠️ spiked picks with the rest, on **real** closed trades: that is how the tag is confirmed or dropped. Picks logged before that date have these columns blank ("not recorded") and are left out of those two tables.

Since 5 Nov 2026 the record also says **how the trades still held stand right now** - *Open now: 22 of 29 up, average +1.8% after costs (not final)* - marked at the last completed close after the 0.35% cost. It is **not a result** (those trades can still go either way; in the replay about 6 trades in 10 lost and the average trade only turned clearly positive after weeks), so it sits on its own line under the sold-trade record, which stays the only thing that counts. The dashboard's Scoreboard tab shows the same by scan (BTST, Swing, both) and a progress bar toward the 30 sold trades at which the record starts to mean something.

## What the Telegram message looks like

Short on purpose - only what is needed to act, never a share count or an amount to invest (that depends on the money you still have free). **Every heading is bold and in CAPITALS** (since 6 Nov 2026, at the user's request: the stock names are bold too, and a bold heading in ordinary letters got lost among them) - the examples below show the capitals, Telegram shows them in bold; whatever explains a heading follows it in ordinary letters, and a blank line sets each part of 📒 YOUR LIST apart. A test fails on any bold text with a lower-case letter (the market's verdict and the weak-market warning aside), so a heading cannot be added in the old style. The example shows the layout; its names and numbers are illustrative, not a current pick:

```
📈 SWING SCAN · Tue 27 Oct · today's close
🧭 MARKET: 🔴 Bearish · Nifty -0.2% today · global neutral
📊 BREADTH 18% (stocks above their 50-day average) - weak market: half size

PLACE THESE BUY ORDERS (valid 5 sessions)
Set each as a GTT / stop-limit order: it buys only if the price rises to its "Buy at" price.

1. BHARTIARTL 👀 About to break out (1.1% below the 50-day high) · Quality 79/100
Buy at 2,009.85 · skip if it opens above 2,070.10
Stop 1,881.00 (-6.4%) · trail 12.8%
Profit marks 2,138.70 · 2,267.55 · 2,396.40
💪 strong uptrend · near 52-wk high · low volatility · delivery ~46%

2. GLAND 👀 About to break out (2.1% below the 50-day high) · Quality 86/100 · Pharma
Buy at 3,177.35 · skip if it opens above 3,272.65
Stop 2,905.10 (-8.6%) · trail 17.1%
Profit marks 3,449.60 · 3,721.85 · 3,994.10
near 52-wk high · very liquid · delivery ~54%
⚠️ 200-day trend rising, but already spiked 8% in 5 days - buy in continuation, not on a dip

💪 strong uptrend = the 200-day average is up 5%+ in a month; these are listed first
ℹ️ Profit marks = the price where you would be up 1x, 2x or 3x the amount you risk (reference points only). Trail = keep the stop that % below the highest close; only raise it, there is no fixed target. Risk at most 1% of your capital per trade, half in a weak market. A ranked shortlist, not a guarantee.
```

On a day when stocks picked earlier are still among the strongest, the same message gets a reminder block above the new picks, and the new picks' headings say **NEW ·** (illustrative again):

```
📈 SWING SCAN · Mon 2 Nov · close of Fri 30 Oct
📊 BREADTH 19% (stocks above their 50-day average) - weak market: half size

⭐ EARLIER PICKS, STILL STRONG (picked before, order still waiting - no new order)
GLAND 👀 💪 · Quality 94/100 · picked 29 Oct
Buy at 3,177.35 · order valid 4 more sessions
Profit marks 3,449.60 · 3,721.85 · 3,994.10
ZYDUSLIFE 🔥 · Quality 72/100 · picked 30 Oct
Buy at 1,251.40 · order valid 5 more sessions
Profit marks 1,338.50 · 1,425.55 · 1,512.65
BHARTIARTL 👀 · Quality 96/100 · picked 27 Oct
Buy at 2,009.85 · order valid 2 more sessions
Profit marks 2,138.70 · 2,267.55 · 2,396.40
+5 more earlier picks also still qualify (on your list)

NEW · PLACE THESE BUY ORDERS (valid 5 sessions)
Set each as a GTT / stop-limit order: it buys only if the price rises to its "Buy at" price.

1. ...
```

* **⭐ EARLIER PICKS, STILL STRONG** is a reminder, not a new signal. It lists a stock picked on an earlier day when **all** of these hold: its buy order is **still waiting** (not bought yet, not expired - a stock already bought is in 📒 YOUR LIST with its stop, and one whose order expired unfilled is not shown again); it **still passes today's tests** (a setup, top half by quality); today's price has not reached its trigger (so "still waiting" is true); and it **ranks above every one of today's new picks** by the list order (💪 first, then fresh before about-to-break, then quality) - or there is no new pick at all. So it really is first on the list: a fresh first-breakout stock is never pushed below an earlier about-to-break one, and the earlier picks that usually go on top are the 💪 strong uptrends. An earlier pick that beats only some of the new ones stays in 📒 YOUR LIST. The best 3 are shown (`EARLIER_PICKS_MAX` in `config.py`; `0` switches the block off), with a count of how many more qualify. It is placed **above** the new picks so a strong stock from earlier in the week is not lost behind a long list of new ones; the word **NEW ·** appears on the new picks' headings only on days when the block is shown. The word "fresh" is not used for new picks because 🔥 *Fresh breakout* already means something specific (above).
  **Nothing is picked or logged a second time:** the order you already placed stands (no new order), the 15-session wait before a stock can be picked again is unchanged, and the scoreboard still counts one trade per stock - the same rule the back-test used, so the live record stays comparable with it. Honest limits: only the 💪 tag and fresh-before-about-to-break are backed by the back-test; the quality order inside the top half is **not** a proven ladder, so "ranks above" means ahead in the list's order, not "will do better". The same list is written to `summary_<mode>.md` under *Earlier picks, still strong*.
* **🔭 BUILDING UP (watch only - no order yet)** follows the new picks in the **Swing** message (illustrative layout - the numbers are real as of the 30 Oct close):

  ```
  🔭 BUILDING UP (watch only - no order yet)
  Strong stocks 3%-6% below their 50-day high. One joins the picks when it gets within 3% of that high.
  AUROPHARMA · Quality 95/100 · 12-mo +53%
  3.4% below its 50-day high (1,740.00)
  UNIONBANK · Quality 83/100 · 12-mo +30%
  3.3% below its 50-day high (192.33)
  ```

  It answers "which strong stocks are close to a breakout but not yet in the picks?" - the stocks just outside the 👀 *About to break out* band (within 3% of the 50-day high). A stock is listed when **all** of these hold: it is **3%-6% below** its 50-day high (`BUILDING_UP_NEAR_PCT` in `config.py`; the 3% is `PRIMED_NEAR_PCT`); its **quality is in the top half** (the same gate of 50 as the picks); its **200-day average is rising** (up 1%+ in 20 sessions); its **12-month return is strong** (at least 0.25 standard deviations above the average stock's, `BUILDING_UP_MIN_RETURN_Z` - this keeps out flat stocks that merely sit near a high); and it is not already a pick, an earlier pick or on your list (an order waiting, a trade open or in its 15-session cool-down). **💪 strong uptrends come first**, then the nearest to the high; the message shows the best 5 (`BUILDING_UP_MAX`; `0` switches the list off) and the rest is in `summary_swing.md`, `reports/output/building_up_swing.csv` and the dashboard. The **BTST** message has no such list.
  **It is a watch list, not a signal.** No order is suggested, nothing is written to the ledger and nothing is scored, so it cannot move the live record. And **it has not been back-tested**: the 10-year replay only ever traded stocks within 3% of their 50-day high, so what happens after buying one 3%-6% below it is unmeasured. What the replay does say about "a bit further below the high" ([reports/evidence](reports/evidence/README.md) section 7, item 6; `python -m backtest.gap_study <trades file>` reproduces it): among the about-to-break-out signals the nearer half (under 1.5% below) had its buy order trigger 77% of the time against 54% for the further half, and the two earned about the same per trade that did fill (+2.77% vs +3.02%); and among the further half the **💪 strong uptrends** did much better than stocks whose trend was not rising (+6.09% vs +1.83% per filled trade) - the reason 💪 stocks are listed first - though that cut was made after looking at the data. When a stock on the list gets within 3% of its high it becomes a normal pick on that day's scan, with a buy price and a stop; until then the only thing it asks of you is a look.
* On a buy-order card the first line says **Buy at** and the line under it is the **Stop** (the stop-loss). The heading above the cards, **PLACE THESE BUY ORDERS**, says what to do and how: set each as a **GTT / stop-limit order**, which buys only if the price *rises* to the Buy at price (a plain limit order at that price would fill at once, below it). It used to read "BUY-STOP ORDERS (GTT / stop-limit...)"; "buy-stop" is the trading name for such an order. A **BUY NOW** card (BTST, near the close) is bought straight away, only if the price is still above its breakout price.
* **Profit marks** (the line under **Stop**) are the prices where the trade would be up 1x, 2x and 3x the amount it risks (+1R, +2R and +3R in the scoreboard's words). They are **reference points, not targets**: the plan has no fixed target - the stop-loss keeps rising - and in the back-test the profit came from a few big winners that selling at a fixed price would cut off. What you do at a mark is your call; booking part of the profit there has **not** been back-tested. An earlier pick shows the same marks its original card had (worked out from the plan stored in the ledger); nothing is sold, logged or scored at them. The footer explains the term on days a card carries it.
* A **🔥 buy-now** card (BTST, live breakout) reads *Buy near 848.15 · up 3.4% today on 2.1x volume*.
* **A very strong market** (swing message and swing summary only): on a day when **77% or more** of the stocks are above their 50-day average, one italic sentence under the breadth line says that in the 10-year test picks made on such days earned about +0.7R a trade against +0.2R on other days (`STRONG_MARKET_BREADTH`, `STRONG_MARKET_R`, `OTHER_MARKET_R` in `config.py`; set the first above 1 to switch it off). It is **information only**: no pick, order or size depends on it. The rule that allowed it was fixed and pushed before the test was run and was met, but the gap is uneven across years (about zero in four of the nine judged years, negative in two), the 77% line was read off the same data, and sizing by it did not pay in the study's account runs ([reports/evidence](reports/evidence/README.md) item 12). The BTST message never carries it (that scan cannot be replayed).
* **💪 strong uptrend** starts the "why" line of a stock whose 200-day average is at least 5% above where it was 20 sessions ago (`STRONG_TREND_200D_MIN` in `config.py`; about 1 signal in 5). These are **listed first**, and the footer explains the tag - only on a day when a card carries it. What the evidence says, honestly: in the 10-year replay (30 Oct 2026, [reports/evidence](reports/evidence/README.md) section 7) such stocks made **+3.95% per card after costs against +1.29%** for the rest, ahead in all three periods. But the 5% line was chosen after looking at the data, so the true gap is probably smaller; the gap was smaller in 2022-26 (about +1.5%, not clearly different from zero); it was not shown in weak markets (only 114 signals); and it comes from swing signals (BTST cannot be replayed). It is a ranking hint, not a promise - the live scoreboard (above) is how it gets confirmed or dropped, and setting `STRONG_TREND_200D_MIN` to `9.99` turns the tag and the ordering off.
* A **⚠️** line is added only when something specific is going on. The one that tells you what to do: **"already spiked X% in 5 days - buy in continuation, not on a dip"**, i.e. do not chase it and do not wait for a dip either: buy it at the **Buy at** price, which only fills if the stock keeps going up. In the 10-year replay waiting for a dip did **not** beat buying at that price (a dip of 1 ATR: about the same; 2 ATR: clearly worse; for strong uptrends clearly worse). It appears when the gain is at least 4% **and** at least 1 standard deviation above the average stock's 5-day gain that day (roughly the top sixth - about a 5-6% gain in a normal week; `SPIKE_5D_Z`, `SPIKE_5D_MIN` in `config.py`). The line begins with *"200-day trend rising, but ..."* when the 200-day average is at least 1% above where it was 20 sessions ago (`TREND_200D_RISING_MIN`) and it is not a 💪 strong uptrend; for a 💪 stock the tag already says it, so the line begins with just *"Already spiked ..."*. These cut-offs were made stricter on 30 Oct 2026 (the weak market): before, the gain had to be 5% and 1.28 standard deviations (the top tenth) and "rising" meant 0.5%. For a fresh breakout its own big day is the signal, not a spike, so the run-up is measured up to the previous close (*"... spiked 9% before today"*). Other warnings: already more than 3% past the breakout level, a very volatile stock. This is a caution, not a profit tip, and the cut-offs only decide how often the line appears: the same replay found that picks flagged this way did **not** do worse after costs than the rest at any cut-off tried (they do swing more). The full list of strengths and weak points of every pick is in the dated write-up `reports/<mode>/scan_<date>.md`.
* The evening **Swing** message ends with **📒 YOUR LIST**, in plain words (illustrative example):

  ```
  📒 YOUR LIST
  • ETERNAL - bought at 301.60 (buy order triggered)
  • TCS - sold at 3,120.00: stop-loss hit (-1.0R, -6.0% after costs)
  • HDFCBANK - buy order expired; no trade

  BOUGHT, NOT SOLD YET (2) - sell if the price falls to:
  ETERNAL 276.90 · INFY 1,566.50↑
  ↑ = moved up today

  WAITING TO BUY (4) - buy when the price rises to:
  GLAND 3,177.35 · ZYDUSLIFE 1,251.40
  BHARTIARTL 2,009.85 · MANKIND 2,448.10
  ```

  *BOUGHT, NOT SOLD YET* lists each stock you hold with the price to sell at if it falls that far (its current stop-loss, which only ever moves up; ↑ marks one that moved up today). A stop only starts rising once the stock has closed roughly 5%-15% above your buy price (the dashboard shows the exact level for each stock as *Stop starts rising above*); until then it sits on its first stop-loss - on purpose: a tighter stop-loss was replayed on 10 years of signals (a 3 or 4 times the daily range trail, break-even once the stock is up by what you risked, locking a profit at twice that) and **every one earned less**, because ordinary dips shook out the stocks that went on to make the big gains ([reports/evidence](reports/evidence/README.md) section 7, item 7). *WAITING TO BUY* lists each buy order that has not triggered yet with its **buy price**, so you do not have to scroll back to the message that issued the pick. An order that is skipped because the stock opened more than 3% above its buy price, or that expires, is reported once as an update ("buy order skipped ..." / "buy order expired; no trade") and then leaves the list. Each list shows 24 stocks at most, then "...and N more in the dashboard". The afternoon **BTST** message leaves both lists out (stops only change after a completed session) and shows only what changed.
* The last lines are the live record (sold trades only, e.g. *Record since 21 Oct: 2 sold, 50% won, average +0.50R ...*; under it, in the same italics, *Open now: ...* for the trades still held, see above) and the standing rule. When the whole list (bought + waiting) needs more than all of one account's capital at that rule, one sentence is added to it: *Your whole list now needs about 370% of your capital at that rule, so it cannot all be held - choose which to hold (dashboard: Capital plan).* The back-test numbers are in [reports/evidence](reports/evidence/README.md), the README and the dashboard, not repeated in every message.

## Files and dashboard

| File | Content |
| :-- | :-- |
| `scan_results_btst.csv`, `scan_results_swing.csv` | latest picks with the whole plan (dated copies in `reports/<mode>/`) |
| `summary_btst.md`, `summary_swing.md`, `summary.md` | latest write-ups (the master file keeps the newest 24) |
| `tracking/trades.csv` | the scoreboard ledger |
| `data_cache/` | delivery-% cache, the BTST watchlist and its saved candles (`btst_history.csv.gz`), run markers, and the BTST snapshot log (`btst_snapshots.csv.gz`, below) |
| `reports/output/` | backtest and live-scoreboard reports, and `fetch_report_btst.json` / `fetch_report_swing.json`: what the latest run's data fetch did (see below) |
| `reports/output/building_up_swing.csv` | the 🔭 *building up* watch list of the latest Swing scan (every stock that qualifies, not just the 5 in the message; header only on a day with none) |

`streamlit run app.py` shows the latest picks (BTST and Swing tabs), **📌 Your list** (the same two lists as the Telegram message: *Bought, not sold yet - keep your stop-loss updated* with the price to sell at if it falls, and *Waiting to buy* with each buy price) and the **📒 Scoreboard** (sold trades by quality score / setup / scan / market condition). It speaks the same plain words as the message (bought, sold, waiting to buy, stop-loss, buy order), the table columns have readable names instead of the file names (*Buy at*, *Stop-loss*, *Profit mark 1* ...), and the few technical words left (R, ATR, profit factor) are explained next to the numbers. The Swing tab also shows **🔭 Building up near a breakout (watch only - no order yet)**, the watch list above with every stock that qualifies. **📌 Your list** opens with **💼 Capital plan**: enter your trading capital, the risk per trade (default 1%, halved for picks made in a weak market, as in the messages) and the most stocks you want to hold at once, optionally tick the stocks you already hold or have an order for, and it sizes every bought and waiting stock (shares = the money you accept to lose ÷ (buy price − first stop-loss), never more than the capital can pay for), lists them in the scanner's order (💪 strong uptrend, 🔥 fresh before 👀 about to break, then quality) and marks each *Take* (a room is free and the money fits) or *Skip* (every room is taken / the money does not fit). That order is a tidy way to choose, **not a proven edge**: in the 10-year replay of an account that could hold only a few stocks it did not clearly beat a random choice among the same signals. The **📒 Scoreboard** tab adds the *Open now* table and the 30-trade progress bar. The files themselves are unchanged.

## The BTST snapshot log (research only)

The afternoon BTST scan judges a half-formed day: it sees the live price and the volume so far, and projects the day's volume with an assumed profile (about 64% of a normal day's volume traded by 2:00 PM, 72% by 2:30, 81% by 3:00 - an estimate that was never measured on this universe; `scanner/session.py`). The 10-year replay works on daily candles, so it cannot test the afternoon scan at all. Since 5 Nov 2026 every live BTST run therefore saves one row per watchlist stock to `data_cache/btst_snapshots.csv.gz` (the time, open, high, low, last price, volume so far, previous close, the share of the day's volume the scan assumed, and whether the stock was a pick); the evening runs then add how each day really ended (final volume, high, low, close) and, the evening after, the next session's open and close. `python -m backtest.snapshot_study` turns the log into two answers - is the volume projection right (the real share of the day's volume against the assumed one), and what did the picks and the rest of the watchlist do from the afternoon price to the close and to the next session - and says "too few to judge" until enough days are in (all the stocks of one day move with the market, so days matter far more than stocks). Nothing reads the log back into a scan, so it cannot change a pick, and a failure to write it never costs a scan; the volume profile and the BTST rules stay as they are until a result is clear and is recorded in [reports/evidence](reports/evidence/README.md).

## When a scan says "Partial scan"

The scans get their prices from Angel One, which on trading-day evenings refuses about a third of the requests at random - [docs/BROKER_REFUSALS.md](docs/BROKER_REFUSALS.md) has the numbers, why a weekday scan takes 45 minutes, and what was changed on 13 Nov 2026 after four of nine trading-day scans came up short. Each scan therefore says plainly what it got: *"Partial scan: 450 of 501 stocks loaded - the broker kept refusing requests, so the 45-minute limit ran out and 45 stocks were not checked. The broker said: Access denied because of exceeding access rate"* (that was 12 Nov, under the old 45-minute limit; today the limit is 75). A full-market scan says this as soon as 5 or more stocks the broker lists were not checked (before 13 Nov only when under 90% had loaded); the six stocks the broker does not list at all are never counted, and are named in the fetch report, not the message. How it copes (`data/brokers/angelone_client.py`, `data/historical.py`):

* an answer of "not found" / "invalid ..." is final for that stock (no retries, no slowdown); a refused request is retried after a short pause (1 s, then 2 s; longer if the broker said "rate") and does **not** slow the calls that follow - the pace only drops while the broker is refusing *right now* (3+ refusals in a row, or a third of the last 20 calls) and is back to full speed about 20 calls after it recovers;
* a real outage (15 refused stocks in a row) triggers a 45-second cool-down, up to three times, then the run carries on; stocks refused the first time get a second pass;
* the fetch stops after 75 minutes (the job itself is allowed 120; a normal weekday evening needs 42-49, because the scan waits 2.5 s before each call and 3-6 s before each retry while the broker is refusing) and the scan runs on what it has; stocks with an open tracked trade and last evening's watchlist are fetched first, and any of them missed are fetched again afterwards, so a cut-short run still has the ones that matter;
* the afternoon BTST needs only a few live-quote calls (not one per stock) and gets the market line from Yahoo Finance first, because the broker's candle endpoint is the unreliable one during market hours.

Every run also commits `reports/output/fetch_report_<scan>.json` - route used (`quotes` or `candles`), stocks asked for and loaded, seconds, cool-downs, never-reached and refused counts, the broker's most common replies, and how many calls were slowed - so a slow or short scan can be explained without opening the run log.

## Running it

GitHub Actions workflows (all have a **Run workflow** button): **BTST Scan**, **Swing Scan**, **Run All Scans Manual Override**, **Market Direction (Manual)**, **Backtest & Calibrate**. In *Backtest & Calibrate* choose `data_source`: **broker** (about 3 years of Angel One candles; uses the broker, so do not start it while a scan is running) or **yahoo** (10+ years, split-adjusted, about 4 minutes, never touches the broker, safe at any time). The Yahoo run writes `reports/output/backtest_swing_yahoo_<date>.md` and `..._trades.csv`; `python -m backtest.spike_study <that trades file>` turns the csv into the study in `reports/evidence/` (and `python -m backtest.gap_study <that trades file>` into the distance-below-the-high study behind the 🔭 watch list, `python -m backtest.stop_study <that trades file>` into the stop-loss study: every signal is also re-run with five other stop rules, and `python -m backtest.portfolio_study <that trades file>` into the account study: what an account that can hold only N stocks would have earned, with drawdowns and a comparison with random choices - it needs a trades file from a run made on or after 4 Nov 2026, which carries the weekly `path_R` marks; and `python -m backtest.pick_cap_study <that trades file>` into the pick-cap study: would a daily cap on the new picks have earned more, or made the list holdable? - same file, the rule that decides is fixed in the file's header; and `python -m backtest.busy_day_study <that trades file>` into the busy-day study: do the filled trades of days with many new picks earn more than those of quiet days, once the mood of the month is taken out? - same file, the rule is in the file's header and was pushed to GitHub before the first run; `python -m backtest.market_strength_study <that trades file>` into the market-strength study: do the filled trades made when 77% or more of the market is above its 50-day average earn more than the rest, across years and parts of the history? and `python -m backtest.late_pick_study <that trades file>` into the late-pick study: did the picks that had already risen 50% or more in the 12 months before the signal earn less, and how far along are the live picks on their own candles? - both with the rule in the file's header, pushed before the first run). The Yahoo run now also replays an **early turn-up lane** (a stock 3-15% below its 50-day high that closes up 3%+ back above both its 21- and 50-day averages for the first time) into its own files, `reports/output/backtest_swing_turnup_yahoo_<date>.md` and `..._trades.csv` - research only, nothing in the scan changes; `python -m backtest.turn_up_study <the shipped trades file> <the turn-up trades file>` turns the two into the study of whether buying at the turn would have paid more than the shipped entry (the rule is in the file's header and was pushed before the first run; `python -m backtest.turn_up_study --cache data_cache/btst_history.csv.gz` gives a first look on the saved candles of the BTST watchlist). Since 10 Nov 2026 the Yahoo run also re-runs every signal with the same entry and stops but a longer time limit (90, 120, 180 and 250 sessions instead of 60; extra `hold90_*` ... `hold250_*` columns in the same trades file, research only), and `python -m backtest.hold_limit_study <that trades file>` turns them into the study of whether the 60-session limit sells winners too early (about one filled trade in three is sold only because the limit ran out); the rule is in the file's header and was pushed to GitHub before the first run, and the scan's time limit changes only if it is met (first run, 11 Nov 2026: unclear, so the limit stays 60 sessions).

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
backtest/engine.py        replays the rule on broker or Yahoo data; live-scoreboard report
backtest/spike_study.py   offline study of a Yahoo back-test (spiked or not, 200-day trend, pull-back vs buy-stop)
backtest/gap_study.py     offline study of the same file: how far below the 50-day high a signal was (behind the 🔭 watch list)
backtest/stop_study.py    offline study of the same file: does a stop that follows the price more or less closely beat the shipped plan?
backtest/portfolio_study.py  offline study of the same file: an account that can hold only N stocks (growth, drawdowns, order of preference vs random)
backtest/pick_cap_study.py   offline study of the same file: would a daily cap on new picks have earned more or made the list holdable? (rule fixed before the run)
backtest/busy_day_study.py   offline study of the same file: do busy days (many new picks) pay more than quiet ones, beyond the market's mood? (rule pushed before the run)
backtest/market_strength_study.py   offline study of the same file: do trades made when 77%+ of the market is above its 50-day average earn more? (rule pushed before the run; behind the swing message's one-sentence note on such days)
backtest/late_pick_study.py   offline study of the same file: did picks that had already risen 50%+ in 12 months earn less? Also measures the live picks on their own candles (rule pushed before the run)
backtest/turn_up_study.py   offline study of two files (the shipped replay and its early turn-up lane, which the Yahoo run now also writes): would buying the first strong close back above the averages after a pullback have earned more than the shipped entry? (rule pushed before the run; research only - nothing changes in the scan unless the user says yes)
backtest/hold_limit_study.py   offline study of the same file (from a run made after 10 Nov 2026, which replays every signal with a 90-, 120-, 180- and 250-session time limit as well): does the 60-session time limit sell winners too early? (rule pushed before the run; research only - nothing changes in the scan unless the rule is met)
backtest/snapshot_study.py   offline study of the BTST snapshot log: is the afternoon volume projection right, what happened after the afternoon price?
capital_plan.py           how much of the list one account can hold (dashboard calculator; also the footer's whole-list sentence); imports nothing from the scanner
data/snapshots.py         the BTST snapshot log: what each afternoon scan saw, completed by the evening runs (research only, never read back into a scan)
data/yahoo_bars.py        split-adjusted 10-year daily bars from Yahoo Finance - back-test only, the scans never use it
reports/                  Telegram formatter, file writers, evidence
app.py                    Streamlit dashboard
docs/TIMER_SETUP.md       on-time timer setup
docs/BROKER_REFUSALS.md   why a weekday Swing scan takes 45 minutes, what "Partial scan" means, what was changed (and not) on 13 Nov 2026
tests/                    self-contained tests
```

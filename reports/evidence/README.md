# Evidence: what seven years of NSE data say about this rule

**Short version.** There is no sure shot, and this evidence does not contain one. Replayed over 2019-2026, the rule had a real edge that was **concentrated in 2019-2023**; **2024-2026 was about break-even after costs**. The scans are a ranked shortlist with a disciplined exit, not a promise. Treat the live scoreboard (`tracking/trades.csv`, shown in the dashboard) as the judge once it has 30+ closed trades.

All numbers below were produced by the production code itself (`scanner/footprint.py`, `scanner/pipeline.py`, `scanner/tradesim.py` via `backtest/engine.py`), so the back-test and the live scan follow the same rule.

## 1. Data and method

* **Data:** NSE end-of-day prices for about 1,500 stocks, 2019-2026, adjusted for splits and bonuses, with delivery quantity. Design period up to 2024-12-31; validation 2025-01 to 2026-10 (never used to choose anything).
* **Replay:** every day the rule is applied exactly as live - setup test, quality score from that day's data only (no look-ahead), buy-stop entries, 2.5 x ATR stop, 5 x ATR trailing stop, 60-session limit, same-session stop-first assumption, **0.35% round-trip cost**, a trade is dropped (VOID) if the price series jumps more than 35% overnight (split / demerger).
* **Unit of result:** closed trades after costs. A buy-stop that never triggered is neither a win nor a loss (36% of signals never triggered).

## 2. The headline (full history, with delivery %)

4,303 closed trades: **39% won, average +3.08% per trade after costs (+0.37R), profit factor 1.66, median trade -4.97%.** A typical loser lost about 1R (-1.04R median); a typical winner made about +1.9R; the best trades made +18R.

| Year | Closed | Win rate | Avg per trade (after costs) | Profit factor |
| :-- | --: | --: | --: | --: |
| 2019 | 138 | 48% | +3.77% | 2.01 |
| 2020 | 568 | 44% | +6.10% | 2.37 |
| 2021 | 660 | 39% | +4.80% | 1.99 |
| 2022 | 519 | 27% | **-1.68%** | 0.70 |
| 2023 | 789 | 53% | +8.68% | 3.85 |
| 2024 | 674 | 31% | +0.12% | 1.02 |
| 2025 | 553 | 35% | **-0.33%** | 0.93 |
| 2026 (to Oct) | 402 | 31% | +0.52% | 1.10 |

Grouped: **2019-2023** 2,674 trades, 42% won, +4.91% average, PF 2.14. **2024-2026** 1,629 trades, 33% won, +0.07% average, **PF 1.01**. In plain words: the average trade over the last three years paid for its costs and nothing more.

A second, independent replay on today's NIFTY-500 universe, last 600 sessions, price/volume factors only (what the system can compute without delivery data): 1,189 closed trades, 32% won, +0.19% average, PF 1.04 (2024 -2.2%, 2025 +1.0%, 2026 +1.5%).

## 3. The profits are lumpy

* About **1 trade in 8 reached +3R or more**; the top 10% of trades produced about 1.5x the whole net profit - the other 90% together lost money.
* Exits: 1,917 initial-stop exits, 614 trailing-stop exits, 1,472 time-limit exits (median hold about 46 calendar days), 265 gaps through the stop and 35 same-day stop-outs.
* Only 50 of 86 signal months had a positive average trade. Long losing runs are normal for this style: expect to lose on most trades and to go many weeks without a clear winner.

## 4. What did and did not matter

| Question | Finding |
| :-- | :-- |
| Does a **higher quality rank** mean a better result? | **No, not reliably.** Inside the top-half gate the buckets were not ordered (full history, avg/trade: 50-65 +3.10%, 65-80 +3.24%, 80-90 +2.20%, 90-100 +3.67%; 2024+: +0.2%, +1.0%, **-1.8%**, -0.4%). Quality is therefore used as a **filter** (top half), and the messages show the rank only. Letter grades were removed for this reason. |
| Does the **market regime** matter? | Yes. Healthy breadth (60%+ of stocks above the 50-day average): +3.48%/trade. Mixed: +2.62%. **Weak (under 40%): +0.20%, PF 1.04 - essentially no edge.** 2024+: healthy +0.5%, mixed -0.5%, weak -1.6%. That is why weak markets get **half size** and a warning. |
| **Fresh breakout** vs **about to break out**? | Fresh was better on the full history (+5.03% vs +2.90% per trade) but has far fewer trades (356 vs 3,947), and on the recent price-only sample it was not better (74 trades). Both are kept; fresh ones are listed first. |
| Does **delivery %** help? | A little, not consistently: +3.08% vs +2.93% average per trade over the full history (2024 +0.12% vs -0.60%, but 2026 +0.52% vs +1.53%). It remains one of the strongest cross-sectional predictors (see below), so it stays in the score. |
| Does a **price cap** help? | No (+3.05% with the old cap vs +3.08% without). The cap was removed. |
| **Fixed targets / partial profit-taking?** | They lowered expectancy: the money came from the minority of trades allowed to run. No fixed target is used; +1R/+2R/+3R are reference levels only. |
| **BTST (sell next day)?** | Selling the next day was about break-even after costs. The plan therefore holds with the trailing stop rather than selling next morning. |

## 5. Which traits predicted the next 20 days (all stocks, rank correlation in %, design / validation)

| Trait | Design | Validation | Used in the score as |
| :-- | --: | --: | :-- |
| Delivery % (20-day average) | +3.0 | +6.6 | + |
| Closeness to 52-week high | +2.9 | +5.2 | + |
| Rising 200-day average | +2.4 | +4.1 | + |
| 12-month return | +2.2 | +4.4 | + |
| Daily volatility (ATR %) | -1.8 | -4.9 | - |
| 5-day return (spike) | -1.1 | -0.9 | - |
| Stretch above the 21-day average | -0.9 | -1.4 | - |

Signs were stable between the design and validation periods, but the sizes are small (a few percent) - these traits tilt the odds, they do not decide any single trade. That is exactly what the back-test shows.

## 6. Limits of this evidence

* **Survivorship and selection:** the panel contains the stocks that still trade; names that were delisted or fell out of the data are missing, which flatters old years. The live universe (NIFTY 500) is narrower than the back-test panel.
* **Fills:** stops are assumed to fill at the stop (or the open after a gap). Fast markets, circuit limits and illiquid names will fill worse. The 0.35% cost is an average, not a guarantee.
* **Regimes change:** an edge that was strong in 2019-2023 faded after; nothing guarantees it returns, and nothing guarantees it will not fade further.
* **Intraday BTST fills** (buying near 2 PM) cannot be replayed from end-of-day data; the live scoreboard is the only honest test of that part.
* Taxes, brokerage tiers and slippage beyond the 0.35% are not modelled.

## 7. How to use this

1. Treat every scan as a **shortlist** and risk at most 1% of your capital per trade: shares = the money you are willing to lose ÷ (buy price − stop). The message gives no share count because it depends on the money you still have free. In a weak market use half size - or sit out. A pick that has **already spiked** in the last 5 days is flagged on its card (buy on a pull-back or in continuation, do not chase); that is a caution - the 5-day spike is a weak trait in the table above, and pull-back entries were not back-tested.
2. Never widen a stop or add to a loser; exit only through the stop as it trails up.
3. Expect about 6 trades in 10 to lose. Do not judge the system on 5 trades - judge it on **30+ closed trades** in the scoreboard, after costs.
4. If after 50+ closed trades the scoreboard is clearly negative (average below about -0.2R and profit factor under 1), cut the size or pause and re-run **Backtest & Calibrate** rather than hoping. For a lumpy system even 50 trades is a small sample - 100+ is much more reliable.

*Reproduce on your own data: the **Backtest & Calibrate** workflow (or `python main.py backtest`) replays the same code on the broker's history for today's universe. The research panel used for the full-history numbers is a large local dataset and is not stored in the repository.*

# Backtest - swing rule, replayed on Yahoo daily bars (2026-10-30)

*Data: Yahoo Finance daily bars, split-adjusted, 2015-06-01 to 2026-10-30; 474 of 501 NIFTY 500 stocks had usable history. The universe is TODAY's NIFTY 500, so stocks that left the index or were delisted are missing (past results look better than they would have at the time). Price-only quality score; yahoo volume may not be split-adjusted.*

474 stocks, 7258 signals: 4453 closed, 18 still open, 12 still waiting for the buy-stop, 2770 never triggered, 5 dropped (suspected split/bonus gap).

**Closed trades:** n=4453, win rate 39%, avg +2.98% per trade after costs (+0.38R), median -4.81%, profit factor 1.67, best +18.1R, worst -2.7R

### By setup

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| fresh | 279 | 37% | +3.72% | +0.45 | 1.73 |
| primed | 4174 | 39% | +2.93% | +0.38 | 1.67 |

### By market regime on the signal day

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| healthy | 3037 | 40% | +3.40% | +0.45 | 1.79 |
| mixed | 962 | 37% | +2.40% | +0.26 | 1.52 |
| weak | 454 | 36% | +1.40% | +0.19 | 1.28 |

### By quality percentile

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| 50-65 | 2382 | 39% | +3.19% | +0.40 | 1.69 |
| 65-80 | 1119 | 40% | +2.66% | +0.37 | 1.61 |
| 80-90 | 536 | 38% | +3.53% | +0.42 | 1.87 |
| 90-100 | 416 | 34% | +1.94% | +0.24 | 1.46 |

### By year

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| 2016 | 158 | 42% | +2.05% | +0.37 | 1.49 |
| 2017 | 376 | 43% | +4.20% | +0.58 | 2.11 |
| 2018 | 238 | 28% | -1.88% | -0.25 | 0.60 |
| 2019 | 258 | 42% | +1.73% | +0.22 | 1.43 |
| 2020 | 419 | 47% | +6.40% | +0.72 | 2.55 |
| 2021 | 490 | 39% | +4.18% | +0.46 | 1.88 |
| 2022 | 412 | 30% | -1.14% | -0.12 | 0.78 |
| 2023 | 647 | 54% | +9.85% | +1.44 | 4.43 |
| 2024 | 571 | 28% | -0.28% | -0.05 | 0.95 |
| 2025 | 511 | 37% | +0.46% | +0.06 | 1.10 |
| 2026 | 373 | 31% | +1.77% | +0.04 | 1.36 |

## Research: does it matter what the stock did before the signal?

z = the stock's 5-day gain before the signal, in standard deviations above the average stock's 5-day gain that day (a fresh breakout's own day is left out).

### Run-up before the signal (z)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) below 0 (weaker than the average stock) | 1462 | 41% | +3.02% | +0.41 | 1.73 |
| b) 0 to 0.5 | 1420 | 38% | +3.12% | +0.39 | 1.73 |
| c) 0.5 to 1 | 919 | 38% | +2.64% | +0.34 | 1.58 |
| d) 1 to 1.5 | 419 | 36% | +2.88% | +0.33 | 1.57 |
| e) 1.5 to 2.5 | 200 | 42% | +3.10% | +0.39 | 1.58 |
| f) 2.5 and up | 33 | 24% | +4.82% | +0.43 | 1.63 |

### The card's warning: gain at least 4% and z at least 1 (current) versus 5% and 1.28 (before)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) flagged by the current cut-offs | 564 | 37% | +3.40% | +0.39 | 1.64 |
| b) not flagged by the current cut-offs | 3889 | 39% | +2.92% | +0.38 | 1.68 |
| c) flagged by the old cut-offs | 324 | 37% | +2.89% | +0.36 | 1.51 |
| d) not flagged by the old cut-offs | 4129 | 39% | +2.99% | +0.38 | 1.69 |

### 200-day trend (change of the 200-day average over 20 sessions; 'rising' = at least 1.0%)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) falling (below 0) | 595 | 39% | +1.77% | +0.20 | 1.48 |
| b) 0 to 0.5% | 276 | 41% | +2.15% | +0.32 | 1.59 |
| c) 0.5% to 1% | 312 | 44% | +2.78% | +0.48 | 1.85 |
| d) 1% to 2% | 626 | 41% | +1.90% | +0.34 | 1.52 |
| e) 2% and up | 2644 | 37% | +3.62% | +0.43 | 1.72 |

### Spike and trend together

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) rising trend, not spiked | 2768 | 39% | +3.31% | +0.42 | 1.72 |
| b) rising trend, already spiked | 502 | 36% | +3.16% | +0.35 | 1.56 |
| c) trend not rising, not spiked | 1121 | 40% | +1.94% | +0.28 | 1.54 |
| d) trend not rising, already spiked | 62 | 48% | +5.39% | +0.71 | 2.91 |

### Fresh breakout or about to break out, spiked or not

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) fresh, not spiked | 266 | 38% | +3.95% | +0.47 | 1.80 |
| b) fresh, already spiked | 13 | 23% | -1.03% | -0.02 | 0.87 |
| c) about to break, not spiked | 3623 | 39% | +2.84% | +0.37 | 1.67 |
| d) about to break, already spiked | 551 | 38% | +3.51% | +0.40 | 1.67 |

### Waiting for a pull-back, for the stocks the card flags as already spiked

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 828 | 68% | +3.52% | +0.40 | 1.66 | +2.37% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 828 | 74% | +2.45% | +0.26 | 1.50 | +1.81% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 828 | 52% | +2.06% | +0.20 | 1.46 | +1.07% |

### Waiting for a pull-back, for the stocks that were not flagged

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 6363 | 61% | +2.95% | +0.38 | 1.69 | +1.80% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 6363 | 82% | +2.18% | +0.30 | 1.54 | +1.79% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 6363 | 54% | +1.76% | +0.25 | 1.44 | +0.94% |

---
Entry is a buy-stop at the trigger (valid 5 sessions), stop 2.5 ATR below the fill, trailing stop 5 ATR below the highest close, 60-session time limit, 0.35% round-trip costs. Only decisions available at the signal day's close are used. Daily candles from the broker are not split/bonus adjusted, the quality score here is the 7-factor price-only version, and a rule that worked on a past window is no promise about the next one.

# Backtest - swing rule, replayed on Yahoo daily bars (2026-11-11)

*Data: Yahoo Finance daily bars, split-adjusted, 2015-06-01 to 2026-11-09; 475 of 501 NIFTY 500 stocks had usable history. The universe is TODAY's NIFTY 500, so stocks that left the index or were delisted are missing (past results look better than they would have at the time). Price-only quality score; yahoo volume may not be split-adjusted.*

475 stocks, 7308 signals: 4457 closed, 54 still open, 16 still waiting for the buy-stop, 2776 never triggered, 5 dropped (suspected split/bonus gap).

**Closed trades:** n=4457, win rate 39%, avg +2.98% per trade after costs (+0.38R), median -4.81%, profit factor 1.67, best +18.1R, worst -2.7R

### By setup

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| fresh | 279 | 37% | +3.72% | +0.45 | 1.73 |
| primed | 4178 | 39% | +2.93% | +0.38 | 1.67 |

### By market regime on the signal day

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| healthy | 3042 | 40% | +3.41% | +0.45 | 1.79 |
| mixed | 960 | 37% | +2.40% | +0.26 | 1.52 |
| weak | 455 | 36% | +1.37% | +0.19 | 1.28 |

### By quality percentile

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| 50-65 | 2386 | 39% | +3.19% | +0.40 | 1.69 |
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
| 2026 | 377 | 31% | +1.78% | +0.04 | 1.36 |

## Research: does it matter what the stock did before the signal?

z = the stock's 5-day gain before the signal, in standard deviations above the average stock's 5-day gain that day (a fresh breakout's own day is left out).

### Run-up before the signal (z)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) below 0 (weaker than the average stock) | 1463 | 41% | +3.03% | +0.41 | 1.73 |
| b) 0 to 0.5 | 1420 | 38% | +3.12% | +0.39 | 1.73 |
| c) 0.5 to 1 | 921 | 38% | +2.65% | +0.34 | 1.59 |
| d) 1 to 1.5 | 419 | 36% | +2.88% | +0.33 | 1.57 |
| e) 1.5 to 2.5 | 200 | 42% | +3.10% | +0.39 | 1.58 |
| f) 2.5 and up | 34 | 24% | +4.34% | +0.39 | 1.56 |

### The card's warning: gain at least 4% and z at least 1 (current) versus 5% and 1.28 (before)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) flagged by the current cut-offs | 565 | 37% | +3.38% | +0.39 | 1.63 |
| b) not flagged by the current cut-offs | 3892 | 39% | +2.92% | +0.38 | 1.68 |
| c) flagged by the old cut-offs | 325 | 37% | +2.84% | +0.35 | 1.50 |
| d) not flagged by the old cut-offs | 4132 | 39% | +2.99% | +0.38 | 1.69 |

### 200-day trend (change of the 200-day average over 20 sessions; 'rising' = at least 1.0%)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) falling (below 0) | 596 | 39% | +1.79% | +0.20 | 1.49 |
| b) 0 to 0.5% | 276 | 41% | +2.15% | +0.32 | 1.59 |
| c) 0.5% to 1% | 312 | 44% | +2.78% | +0.48 | 1.85 |
| d) 1% to 2% | 627 | 41% | +1.92% | +0.34 | 1.53 |
| e) 2% and up | 2646 | 37% | +3.61% | +0.43 | 1.72 |

### Spike and trend together

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) rising trend, not spiked | 2770 | 39% | +3.32% | +0.42 | 1.72 |
| b) rising trend, already spiked | 503 | 36% | +3.13% | +0.35 | 1.55 |
| c) trend not rising, not spiked | 1122 | 40% | +1.95% | +0.28 | 1.54 |
| d) trend not rising, already spiked | 62 | 48% | +5.39% | +0.71 | 2.91 |

### Fresh breakout or about to break out, spiked or not

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) fresh, not spiked | 266 | 38% | +3.95% | +0.47 | 1.80 |
| b) fresh, already spiked | 13 | 23% | -1.03% | -0.02 | 0.87 |
| c) about to break, not spiked | 3626 | 39% | +2.85% | +0.37 | 1.67 |
| d) about to break, already spiked | 552 | 38% | +3.48% | +0.40 | 1.66 |

### Waiting for a pull-back, for the stocks the card flags as already spiked

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 829 | 68% | +3.46% | +0.40 | 1.65 | +2.34% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 829 | 74% | +2.42% | +0.26 | 1.49 | +1.80% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 829 | 52% | +2.08% | +0.20 | 1.46 | +1.08% |

### Waiting for a pull-back, for the stocks that were not flagged

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 6368 | 61% | +2.95% | +0.38 | 1.69 | +1.80% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 6368 | 82% | +2.18% | +0.30 | 1.54 | +1.79% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 6368 | 54% | +1.75% | +0.25 | 1.44 | +0.94% |

### Following the price more or less closely with the stop (the same signals, the same entries, only the stop differs)

| Stop plan | Signals | Filled | Win rate | Avg after costs (filled) | Avg R | Profit factor | Median trade | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| Shipped plan: trail 5 ATR below the highest close | 7233 | 62% | 39% | +2.98% | +0.38 | 1.67 | -4.81% | +1.84% |
| Trail 3 ATR below the highest close | 7233 | 62% | 38% | +1.63% | +0.20 | 1.43 | -3.16% | +1.01% |
| Trail 4 ATR below the highest close | 7233 | 62% | 39% | +2.49% | +0.31 | 1.59 | -4.30% | +1.54% |
| Trail 7 ATR below the highest close | 7233 | 62% | 39% | +3.20% | +0.41 | 1.70 | -5.19% | +1.97% |
| Trail 5 ATR below the highest close; once a close is +1R the stop is at least the buy price | 7233 | 62% | 32% | +2.63% | +0.33 | 1.66 | -0.95% | +1.62% |
| Trail 5 ATR below the highest close; once a close is +1R the stop is at least the buy price; once a close is +2R the stop is at least +1R above it | 7233 | 62% | 34% | +2.29% | +0.29 | 1.58 | -0.83% | +1.41% |

---
Entry is a buy-stop at the trigger (valid 5 sessions), stop 2.5 ATR below the fill, trailing stop 5 ATR below the highest close, 60-session time limit, 0.35% round-trip costs. Only decisions available at the signal day's close are used. Daily candles from the broker are not split/bonus adjusted, the quality score here is the 7-factor price-only version, and a rule that worked on a past window is no promise about the next one.

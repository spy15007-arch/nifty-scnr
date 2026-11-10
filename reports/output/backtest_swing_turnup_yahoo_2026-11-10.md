# Backtest - swing rule, replayed on Yahoo daily bars (2026-11-10)

*Data: Yahoo Finance daily bars, split-adjusted, 2015-06-01 to 2026-11-09; 475 of 501 NIFTY 500 stocks had usable history. The universe is TODAY's NIFTY 500, so stocks that left the index or were delisted are missing (past results look better than they would have at the time). Price-only quality score; yahoo volume may not be split-adjusted.*

475 stocks, 2758 signals: 2366 closed, 48 still open, 4 still waiting for the buy-stop, 340 never triggered, 0 dropped (suspected split/bonus gap).

**Closed trades:** n=2366, win rate 40%, avg +3.52% per trade after costs (+0.34R), median -5.65%, profit factor 1.69, best +16.2R, worst -2.3R

### By setup

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| turn | 2366 | 40% | +3.52% | +0.34 | 1.69 |

### By market regime on the signal day

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| healthy | 1233 | 39% | +3.47% | +0.36 | 1.70 |
| mixed | 633 | 39% | +3.52% | +0.31 | 1.69 |
| weak | 500 | 41% | +3.64% | +0.31 | 1.68 |

### By quality percentile

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| 50-65 | 953 | 39% | +2.70% | +0.26 | 1.54 |
| 65-80 | 723 | 41% | +4.09% | +0.39 | 1.80 |
| 80-90 | 383 | 38% | +2.95% | +0.28 | 1.58 |
| 90-100 | 307 | 39% | +5.44% | +0.51 | 1.98 |

### By year

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| 2016 | 59 | 22% | -3.18% | -0.36 | 0.51 |
| 2017 | 144 | 49% | +5.39% | +0.72 | 2.33 |
| 2018 | 148 | 34% | -1.45% | -0.13 | 0.74 |
| 2019 | 130 | 41% | +2.26% | +0.24 | 1.53 |
| 2020 | 235 | 55% | +8.90% | +0.82 | 3.45 |
| 2021 | 295 | 41% | +5.00% | +0.41 | 1.99 |
| 2022 | 265 | 34% | -0.12% | -0.04 | 0.98 |
| 2023 | 217 | 55% | +12.84% | +1.51 | 4.56 |
| 2024 | 346 | 29% | +0.55% | -0.02 | 1.09 |
| 2025 | 279 | 38% | +1.53% | +0.14 | 1.30 |
| 2026 | 248 | 33% | +2.89% | +0.15 | 1.50 |

## Research: does it matter what the stock did before the signal?

z = the stock's 5-day gain before the signal, in standard deviations above the average stock's 5-day gain that day (a fresh breakout's own day is left out).

### Run-up before the signal (z)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) below 0 (weaker than the average stock) | 918 | 42% | +4.37% | +0.40 | 1.83 |
| b) 0 to 0.5 | 707 | 39% | +2.84% | +0.29 | 1.58 |
| c) 0.5 to 1 | 455 | 37% | +2.73% | +0.27 | 1.56 |
| d) 1 to 1.5 | 196 | 38% | +4.44% | +0.41 | 1.86 |
| e) 1.5 to 2.5 | 84 | 38% | +2.40% | +0.16 | 1.48 |
| f) 2.5 and up | 6 | 17% | -2.33% | -0.19 | 0.78 |

### The card's warning: gain at least 4% and z at least 1 (current) versus 5% and 1.28 (before)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) flagged by the current cut-offs | 222 | 39% | +3.95% | +0.37 | 1.78 |
| b) not flagged by the current cut-offs | 2144 | 40% | +3.47% | +0.33 | 1.68 |
| c) flagged by the old cut-offs | 123 | 37% | +3.00% | +0.27 | 1.59 |
| d) not flagged by the old cut-offs | 2243 | 40% | +3.55% | +0.34 | 1.70 |

### 200-day trend (change of the 200-day average over 20 sessions; 'rising' = at least 1.0%)

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) falling (below 0) | 214 | 42% | +1.91% | +0.19 | 1.49 |
| b) 0 to 0.5% | 107 | 36% | +2.11% | +0.23 | 1.47 |
| c) 0.5% to 1% | 120 | 42% | +1.32% | +0.12 | 1.30 |
| d) 1% to 2% | 265 | 40% | +2.20% | +0.25 | 1.49 |
| e) 2% and up | 1660 | 39% | +4.19% | +0.39 | 1.77 |

### Spike and trend together

| Group | Trades | Win rate | Avg after costs | Avg R | Profit factor |
| :-- | --: | --: | --: | --: | --: |
| a) rising trend, not spiked | 1725 | 40% | +3.92% | +0.37 | 1.74 |
| b) rising trend, already spiked | 200 | 38% | +3.81% | +0.36 | 1.72 |
| c) trend not rising, not spiked | 419 | 40% | +1.62% | +0.17 | 1.38 |
| d) trend not rising, already spiked | 22 | 50% | +5.24% | +0.47 | 2.66 |

### Waiting for a pull-back, for the stocks the card flags as already spiked

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 253 | 87% | +4.05% | +0.38 | 1.80 | +3.52% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 253 | 54% | +4.13% | +0.41 | 1.78 | +2.22% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 253 | 38% | +1.82% | +0.16 | 1.32 | +0.68% |

### Waiting for a pull-back, for the stocks that were not flagged

| Entry | Signals | Filled | Avg after costs (filled) | Avg R | Profit factor | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: |
| Buy-stop at the trigger (what the cards say) | 2439 | 88% | +3.52% | +0.34 | 1.69 | +3.08% |
| Buy-limit 1 ATR below the trigger, live 5 sessions | 2439 | 50% | +3.36% | +0.33 | 1.68 | +1.69% |
| Buy-limit 2 ATR below the trigger, live 10 sessions | 2439 | 31% | +2.52% | +0.25 | 1.51 | +0.78% |

### Following the price more or less closely with the stop (the same signals, the same entries, only the stop differs)

| Stop plan | Signals | Filled | Win rate | Avg after costs (filled) | Avg R | Profit factor | Median trade | Per signal (unfilled = 0) |
| :-- | --: | --: | --: | --: | --: | --: | --: | --: |
| Shipped plan: trail 5 ATR below the highest close | 2705 | 87% | 40% | +3.52% | +0.34 | 1.69 | -5.65% | +3.08% |
| Trail 3 ATR below the highest close | 2705 | 87% | 40% | +2.35% | +0.21 | 1.54 | -3.17% | +2.05% |
| Trail 4 ATR below the highest close | 2705 | 87% | 40% | +3.12% | +0.29 | 1.64 | -4.87% | +2.73% |
| Trail 7 ATR below the highest close | 2705 | 87% | 40% | +3.74% | +0.36 | 1.71 | -6.20% | +3.27% |
| Trail 5 ATR below the highest close; once a close is +1R the stop is at least the buy price | 2705 | 87% | 33% | +3.23% | +0.31 | 1.72 | -0.35% | +2.82% |
| Trail 5 ATR below the highest close; once a close is +1R the stop is at least the buy price; once a close is +2R the stop is at least +1R above it | 2705 | 87% | 35% | +2.95% | +0.28 | 1.66 | -0.35% | +2.58% |

---
Entry is a buy-stop at the trigger (valid 5 sessions), stop 2.5 ATR below the fill, trailing stop 5 ATR below the highest close, 60-session time limit, 0.35% round-trip costs. Only decisions available at the signal day's close are used. Daily candles from the broker are not split/bonus adjusted, the quality score here is the 7-factor price-only version, and a rule that worked on a past window is no promise about the next one.

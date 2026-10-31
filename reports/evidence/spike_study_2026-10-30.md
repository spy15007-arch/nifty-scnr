# Spike / trend / pull-back study

Source: `backtest_swing_yahoo_2026-10-30_trades.csv`. How to read it: see the top of `backtest/spike_study.py`.

```
Signals 7258, resolved 7223, closed 4453, 125 months, 2016-06-08 to 2026-10-30; buy-stop results: {'CLOSED': 4453, 'UNFILLED': 2770, 'OPEN': 18, 'PENDING': 12, 'VOID': 5}
Warning cut-offs now: gain >= 4% and z >= 1; 'rising' = 200-day trend >= 1.0%. Before 30 Oct 2026: 5% / 1.28 / 0.5%.

ALL signals (buy-stop at the trigger)        signals   7223  filled  62%  win  39%  per filled  +2.98% [ +1.66%, +4.28%]  per signal  +1.84% [ +1.01%, +2.69%]

== 1. THE QUESTION: does an 'already spiked' setup do worse? (same signals, flagged vs not flagged) ==
flagged, current cut-offs                    signals    834  filled  68%  win  37%  per filled  +3.40% [ +1.10%, +5.83%]  per signal  +2.30% [ +0.73%, +3.99%]
not flagged, current                         signals   6389  filled  61%  win  39%  per filled  +2.92% [ +1.60%, +4.17%]  per signal  +1.78% [ +0.95%, +2.58%]
flagged, old cut-offs                        signals    450  filled  72%  win  37%  per filled  +2.89% [ +0.39%, +5.60%]  per signal  +2.08% [ +0.27%, +4.04%]
not flagged, old                             signals   6773  filled  61%  win  39%  per filled  +2.99% [ +1.67%, +4.26%]  per signal  +1.82% [ +1.00%, +2.66%]
current: flagged - not flagged (per filled)  diff  +0.48% [ -1.32%, +2.54%]  P(first better)  68%
current: flagged - not flagged (per signal)  diff  +0.52% [ -0.72%, +1.95%]  P(first better)  77%
current: flagged - not flagged (per signal, R) diff +0.033R [-0.103R,+0.190R]  P(first better)  67%
old:     flagged - not flagged (per filled)  diff  -0.10% [ -2.28%, +2.33%]  P(first better)  46%
old:     flagged - not flagged (per signal)  diff  +0.26% [ -1.31%, +2.04%]  P(first better)  61%
share of signals flagged: current 12%, old 6%
-- the same, period by period (per signal):
  2016-2019                                  diff  +1.66% [ -0.66%, +4.74%]  P(first better)  90%
  2020-2022                                  diff  -0.34% [ -2.39%, +1.95%]  P(first better)  38%
  2023-2026                                  diff  +0.44% [ -1.59%, +2.79%]  P(first better)  65%
  weak market (breadth < 40%)                diff  +1.83% [ -1.10%, +5.51%]  P(first better)  88%
  healthier market                           diff  +0.39% [ -1.04%, +2.00%]  P(first better)  68%
  fresh setups                               diff  -4.33% [-12.28%, +6.15%]  P(first better)  17%
  primed setups                              diff  +0.67% [ -0.58%, +2.12%]  P(first better)  83%
-- any other cut-off pair (flagged - not flagged, per signal | per filled):
  z>=0.5  & >=3%:  27% flagged | diff  +0.24% [ -0.57%, +1.11%]  P(first better)  71% | diff  +0.05% [ -1.16%, +1.33%]  P(first better)  53%
  z>=1    & >=4%:  12% flagged | diff  +0.52% [ -0.72%, +1.95%]  P(first better)  77% | diff  +0.48% [ -1.32%, +2.54%]  P(first better)  68%
  z>=1.28 & >=5%:   6% flagged | diff  +0.26% [ -1.31%, +2.04%]  P(first better)  61% | diff  -0.10% [ -2.28%, +2.33%]  P(first better)  46%
  z>=1.5  & >=6%:   4% flagged | diff  +1.17% [ -1.17%, +3.86%]  P(first better)  83% | diff  +0.94% [ -2.11%, +4.39%]  P(first better)  71%
  z>=2    & >=8%:   1% flagged | diff  -1.71% [ -6.51%, +4.70%]  P(first better)  27% | diff  -2.84% [ -9.12%, +5.17%]  P(first better)  22%

== 2. Gradient by run-up z (the 5-day gain before the signal, in standard deviations) ==
z in [-99, 0)                                signals   2471  filled  59%  win  41%  per filled  +3.02% [ +1.49%, +4.47%]  per signal  +1.79% [ +0.86%, +2.69%]
z in [0, 0.5)                                signals   2325  filled  61%  win  38%  per filled  +3.12% [ +1.61%, +4.57%]  per signal  +1.91% [ +0.96%, +2.85%]
z in [0.5, 1)                                signals   1459  filled  63%  win  38%  per filled  +2.64% [ +1.06%, +4.32%]  per signal  +1.66% [ +0.65%, +2.79%]
z in [1, 1.5)                                signals    650  filled  64%  win  36%  per filled  +2.88% [ +0.51%, +5.48%]  per signal  +1.86% [ +0.33%, +3.53%]
z in [1.5, 2.5)                              signals    275  filled  73%  win  42%  per filled  +3.10% [ +0.35%, +6.02%]  per signal  +2.26% [ +0.25%, +4.42%]
z in [2.5, 99)                               signals     43  filled  77%  win  24%  per filled  +4.82% [ -6.77%,+21.14%]  per signal  +3.70% [ -5.26%,+16.34%]
rank correlation of z with the after-cost return of closed trades: -0.050 [-0.083,-0.015] (a slightly negative number means a slightly lower win rate; the average is not lower when the winners are bigger)

== 3. Swings: are already-spiked trades more volatile? (closed trades) ==
flagged (current)    n=  564  std of net 23.1%  std of R 2.45  10/25/50/75/90th percentile of net: -11.5% -9.0% -5.8% +10.3% +26.5%  average ATR 3.44%
not flagged          n= 3889  std of net 17.3%  std of R 2.20  10/25/50/75/90th percentile of net: -9.7% -7.6% -4.7% +9.0% +24.0%  average ATR 2.93%

== 4. 200-day trend (change of the 200-day average over 20 sessions) ==
trend in [-900%, 0%)                         signals   1105  filled  54%  win  39%  per filled  +1.77% [ -0.08%, +3.73%]  per signal  +0.96% [ -0.04%, +2.10%]
trend in [0%, 1%)                            signals   1063  filled  55%  win  43%  per filled  +2.48% [ +1.03%, +3.93%]  per signal  +1.37% [ +0.55%, +2.24%]
trend in [1%, 2%)                            signals   1146  filled  55%  win  41%  per filled  +1.90% [ +0.65%, +3.17%]  per signal  +1.04% [ +0.35%, +1.76%]
trend in [2%, 3%)                            signals   1040  filled  61%  win  39%  per filled  +2.33% [ +0.83%, +3.85%]  per signal  +1.41% [ +0.50%, +2.35%]
trend in [3%, 5%)                            signals   1393  filled  66%  win  38%  per filled  +2.49% [ +0.77%, +4.18%]  per signal  +1.63% [ +0.50%, +2.77%]
trend in [5%, 8%)                            signals    929  filled  72%  win  37%  per filled  +4.41% [ +1.99%, +6.78%]  per signal  +3.17% [ +1.42%, +4.92%]
trend in [8%, 900%)                          signals    547  filled  79%  win  33%  per filled  +6.69% [ +2.61%,+10.94%]  per signal  +5.28% [ +2.04%, +8.69%]
rising (>= 1.0%) - not rising (per signal)   diff  +0.97% [ +0.01%, +1.97%]  P(first better)  98%  <-- clear
rising (>= 1.0%) - not rising (per filled)   diff  +1.16% [ -0.40%, +2.79%]  P(first better)  92%
-- strong uptrend (>= 5%, 20% of signals) vs the rest:
strong uptrend                               signals   1476  filled  75%  win  35%  per filled  +5.30% [ +2.76%, +7.81%]  per signal  +3.95% [ +2.02%, +5.93%]
the rest                                     signals   5747  filled  58%  win  40%  per filled  +2.22% [ +1.03%, +3.36%]  per signal  +1.29% [ +0.59%, +2.00%]
strong - rest (per signal)                   diff  +2.66% [ +1.02%, +4.41%]  P(first better) 100%  <-- clear
strong - rest (per filled)                   diff  +3.08% [ +0.96%, +5.33%]  P(first better) 100%  <-- clear
strong - rest (per signal, R)                diff +0.210R [+0.042R,+0.392R]  P(first better)  99%  <-- clear
  2016-2019                                  diff  +3.06% [ +0.59%, +5.86%]  P(first better)  99%  <-- clear   (strong signals: 222)
  2020-2022                                  diff  +2.79% [ -0.04%, +5.74%]  P(first better)  97%   (strong signals: 531)
  2023-2026                                  diff  +2.25% [ -0.18%, +5.11%]  P(first better)  96%   (strong signals: 731)
  first half (2016-2021)                     diff  +4.06% [ +1.95%, +6.31%]  P(first better) 100%  <-- clear
  second half (2022-2026)                    diff  +1.54% [ -0.69%, +4.03%]  P(first better)  90%
  weak market                                diff  +1.65% [ -3.76%, +8.43%]  P(first better)  69%   (strong signals: 114)
  healthier market                           diff  +2.69% [ +1.00%, +4.49%]  P(first better) 100%  <-- clear
strong-uptrend trades: 1100 closed in 343 different stocks; average ATR 3.76% vs 2.64% for the rest (they are more volatile, but the gain holds in R, which is measured per unit of risk)
by year (trades, average): 2016: 25 / -1.5%, 2017: 81 / +10.3%, 2018: 34 / -2.5%, 2019: 10 / +11.9%, 2020: 61 / +11.5%, 2021: 271 / +7.3%, 2022: 77 / -5.7%, 2023: 164 / +16.2%, 2024: 292 / -1.2%, 2025: 30 / -0.6%, 2026: 55 / +8.8%

== 5. Spike and trend together ==
rising, not spiked                           signals   4344  filled  64%  win  39%  per filled  +3.31% [ +1.83%, +4.78%]  per signal  +2.11% [ +1.15%, +3.10%]
rising, spiked                               signals    711  filled  71%  win  36%  per filled  +3.16% [ +0.70%, +5.76%]  per signal  +2.23% [ +0.51%, +4.09%]
not rising, not spiked                       signals   2045  filled  55%  win  40%  per filled  +1.94% [ +0.50%, +3.44%]  per signal  +1.07% [ +0.26%, +1.94%]
not rising, spiked                           signals    123  filled  50%  win  48%  per filled  +5.39% [ +2.15%, +9.52%]  per signal  +2.72% [ +1.06%, +4.83%]
rising: spiked - not spiked                  diff  +0.12% [ -1.34%, +1.73%]  P(first better)  55%
strong uptrend: spiked - not spiked          diff  +0.21% [ -2.73%, +3.38%]  P(first better)  55%

== 6. WAITING FOR A PULL-BACK vs the buy-stop (only signals that are finished in all three entry styles) ==
7191 of 7258 signals finished in every style. Fill rates: buy-stop 62%, buy-limit 1 ATR below (5 sessions) 81%, buy-limit 2 ATR below (10 sessions) 53%
                                                         (result per signal after costs: buy-stop | pull-back 1 | pull-back 2)
ALL signals                                  n=  7191   +1.86% |  +1.79% |  +0.96%
    pull-back 1 minus buy-stop               n=  7191   -0.07% [ -0.46%, +0.33%]
    pull-back 2 minus buy-stop               n=  7191   -0.91% [ -1.53%, -0.28%]  <-- clear
flagged (current)                            n=   828   +2.37% |  +1.81% |  +1.07%
    pull-back 1 minus buy-stop               n=   828   -0.56% [ -1.74%, +0.56%]
    pull-back 2 minus buy-stop               n=   828   -1.31% [ -2.79%, +0.04%]
not flagged                                  n=  6363   +1.80% |  +1.79% |  +0.94%
    pull-back 1 minus buy-stop               n=  6363   -0.01% [ -0.41%, +0.40%]
    pull-back 2 minus buy-stop               n=  6363   -0.85% [ -1.47%, -0.23%]  <-- clear
flagged (old)                                n=   447   +2.15% |  +1.52% |  +0.67%
    pull-back 1 minus buy-stop               n=   447   -0.64% [ -2.54%, +1.04%]
    pull-back 2 minus buy-stop               n=   447   -1.49% [ -3.32%, +0.28%]
flagged and rising                           n=   705   +2.32% |  +1.84% |  +1.22%
    pull-back 1 minus buy-stop               n=   705   -0.48% [ -1.82%, +0.79%]
    pull-back 2 minus buy-stop               n=   705   -1.09% [ -2.73%, +0.46%]
strong uptrend                               n=  1466   +4.03% |  +2.47% |  +1.04%
    pull-back 1 minus buy-stop               n=  1466   -1.56% [ -2.63%, -0.60%]  <-- clear
    pull-back 2 minus buy-stop               n=  1466   -2.99% [ -4.77%, -1.27%]  <-- clear
-- by run-up z bucket (pull-back 1 minus buy-stop, per signal):
  z in [-99, 0)                              n=  2467   +0.57% [ -0.07%, +1.17%]
  z in [0, 0.5)                              n=  2317   -0.28% [ -0.87%, +0.33%]
  z in [0.5, 1)                              n=  1447   -0.66% [ -1.41%, +0.13%]
  z in [1, 1.5)                              n=   644   +0.10% [ -0.98%, +1.15%]
  z in [1.5, 2.5)                            n=   275   -0.69% [ -2.76%, +1.23%]
  z in [2.5, 99)                             n=    41   -5.16% [-18.74%, +4.98%]
-- for flagged stocks, period by period (pull-back 1 | pull-back 2, minus buy-stop):
  2016-2019    n=  186   -0.82% [ -3.99%, +1.50%] |  -0.96% [ -3.87%, +1.52%]
  2020-2022    n=  230   -0.50% [ -2.71%, +1.52%] |  -1.06% [ -3.62%, +1.20%]
  2023-2026    n=  412   -0.48% [ -2.04%, +1.04%] |  -1.60% [ -4.00%, +0.60%]
-- what the trades that DID fill looked like (flagged stocks):
  buy-stop     filled  559 of 828  average +3.52%  win 38%  share of trades >= +3R 12.2%
  pull-back 1  filled  614 of 828  average +2.45%  win 37%  share of trades >= +3R 9.4%
  pull-back 2  filled  430 of 828  average +2.06%  win 37%  share of trades >= +3R 9.1%

== 7. Level and lumpiness ==
closed 4453: average +2.98%, median -4.81%, win rate 39%, 12.2% of trades reach +3R; the best 10% of trades earn 1.5x the total
without 2023: +1.81%;  without 2020 and 2023: +1.25%  (a few strong years carry the average)
by market on the signal day (closed trades): healthy +3.40% (n=3037), mixed +2.40% (n=962), weak +1.40% (n=454)
by year: 2016 +2.0%, 2017 +4.2%, 2018 -1.9%, 2019 +1.7%, 2020 +6.4%, 2021 +4.2%, 2022 -1.1%, 2023 +9.8%, 2024 -0.3%, 2025 +0.5%, 2026 +1.8%
```

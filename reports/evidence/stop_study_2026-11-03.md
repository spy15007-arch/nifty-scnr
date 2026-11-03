# Stop-loss study: follow the price more or less closely?

Source: `backtest_swing_yahoo_2026-11-03_trades.csv`. How to read it: see the top of `backtest/stop_study.py` and `backtest/spike_study.py`.

```
Signals 7264, finished under every plan 7223, 126 months, 2016-06-08 to 2026-11-02

The plans (same signals, same entries, same first stop; only the trail differs):
  shipped  first stop 2.5 ATR below the buy price, trail 5 ATR below the highest close
  tr3      trail 3 ATR
  tr4      trail 4 ATR
  tr7      trail 7 ATR
  be1      trail 5 ATR; once a close is +1R the stop is at least the buy price
  lk2      trail 5 ATR; once a close is +1R the stop is at least the buy price; once a close is +2R the stop is at least +1R above it

== 1. Every plan on the same finished signals (after 0.35% costs) ==
shipped                                      signals   7223  filled  62%  win  39%  per filled  +2.98% [ +1.63%, +4.33%]  per signal  +1.84% [ +0.99%, +2.71%]
tr3                                          signals   7223  filled  62%  win  38%  per filled  +1.63% [ +0.70%, +2.55%]  per signal  +1.01% [ +0.43%, +1.60%]
tr4                                          signals   7223  filled  62%  win  39%  per filled  +2.49% [ +1.30%, +3.68%]  per signal  +1.54% [ +0.79%, +2.30%]
tr7                                          signals   7223  filled  62%  win  38%  per filled  +3.19% [ +1.78%, +4.58%]  per signal  +1.97% [ +1.07%, +2.87%]
be1                                          signals   7223  filled  62%  win  32%  per filled  +2.63% [ +1.40%, +3.86%]  per signal  +1.62% [ +0.86%, +2.41%]
lk2                                          signals   7223  filled  62%  win  34%  per filled  +2.29% [ +1.17%, +3.43%]  per signal  +1.41% [ +0.71%, +2.14%]

How the filled trades ended (share of filled trades) and how big the typical loser / winner was:
plan       first stop   trail  time limit   median  avg loser  avg winner
shipped           52%     15%         34%   -4.81%     -7.25%     +19.11%
tr3               33%     54%         13%   -3.16%     -6.19%     +14.16%
tr4               46%     29%         25%   -4.30%     -6.88%     +17.42%
tr7               56%      4%         40%   -5.19%     -7.46%     +20.23%
be1               50%     24%         26%   -1.08%     -5.84%     +20.66%
lk2               50%     28%         21%   -0.85%     -6.01%     +18.35%

== 2. Each variant minus the shipped plan, per signal (what a person who follows every card earns per card) ==
tr3                                          n=  7223   -0.83% [ -1.22%, -0.48%]  <-- clear
tr4                                          n=  7223   -0.30% [ -0.47%, -0.15%]  <-- clear
tr7                                          n=  7223   +0.13% [ +0.02%, +0.24%]  <-- clear
be1                                          n=  7223   -0.21% [ -0.33%, -0.10%]  <-- clear
lk2                                          n=  7223   -0.42% [ -0.63%, -0.23%]  <-- clear
-- the same in R (profit as a multiple of the amount risked):
tr3        n=  7223  -0.110R [-0.163R,-0.060R]  <-- clear
tr4        n=  7223  -0.041R [-0.065R,-0.020R]  <-- clear
tr7        n=  7223  +0.015R [+0.000R,+0.030R]  <-- clear
be1        n=  7223  -0.031R [-0.047R,-0.015R]  <-- clear
lk2        n=  7223  -0.057R [-0.084R,-0.032R]  <-- clear

== 3. The same difference by period, market condition and trend ('*' = the 95% range excludes 0) ==
plan              2016-2019        2020-2022        2023-2026   healthy market     mixed market      weak market   strong uptrend     other trends
tr3                -0.49%*          -0.93%*          -0.95%*          -1.06%*          -0.50%           -0.13%           -1.44%*          -0.67%* 
tr4                -0.18%           -0.17%           -0.44%*          -0.34%*          -0.22%           -0.20%           -0.55%*          -0.24%* 
tr7                +0.00%           +0.33%*          +0.08%           +0.21%*          +0.04%           -0.21%*          +0.54%*          +0.02%  
be1                -0.04%           -0.27%*          -0.27%*          -0.25%*          -0.06%           -0.29%           -0.20%           -0.22%* 
lk2                -0.16%           -0.46%*          -0.54%*          -0.51%*          -0.20%           -0.34%           -0.55%*          -0.39%* 
signals in each cut: 2016-2019 1779, 2020-2022 1962, 2023-2026 3482, healthy market 4799, mixed market 1627, weak market 797, strong uptrend 1476, other trends 5747

== 4. What a rally gives back: filled trades that, under the shipped plan, had a CLOSE at least +1R / +2R / +3R above the buy price ==
-- peak close >= +1R: 2184 trades (49% of filled trades)
plan        avg result  ended below 0  below +1R  at +3R or more
shipped        +13.89%            24%        43%             25%
tr3            +10.45%            23%        54%             16%
tr4            +12.81%            24%        47%             22%
tr7            +14.32%            24%        41%             26%
be1            +13.18%            38%        51%             23%
lk2            +12.49%            33%        59%             20%
-- peak close >= +2R: 1424 trades (32% of filled trades)
plan        avg result  ended below 0  below +1R  at +3R or more
shipped        +21.14%             7%        22%             38%
tr3            +15.21%            12%        35%             24%
tr4            +19.12%             6%        28%             33%
tr7            +21.73%            10%        21%             40%
be1            +19.16%            18%        32%             35%
lk2            +18.09%            12%        46%             30%
-- peak close >= +3R: 885 trades (20% of filled trades)
plan        avg result  ended below 0  below +1R  at +3R or more
shipped        +28.83%             0%         8%             61%
tr3            +20.28%             9%        22%             39%
tr4            +26.12%             3%         9%             53%
tr7            +29.44%             4%        10%             63%
be1            +26.79%             9%        16%             56%
lk2            +24.36%             8%        27%             49%
```

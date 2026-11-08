# Late-pick study: do picks that had already risen a lot earn less?

Source: `backtest_swing_yahoo_2026-11-04_trades.csv`. How to read it: see the top of `backtest/late_pick_study.py`.

```
Finished signals 7227 (4456 filled and sold, 2771 orders that never filled), 2016-06-08 to 2026-11-03 (10.4 years), 125 calendar months.

The question: did the picks that had ALREADY RISEN 50% OR MORE in the 12 months before the signal (EXT) earn less per filled trade than the others?
The rule (fixed before the run; the full text is at the top of backtest/late_pick_study.py). It can come out three ways:
  WORSE (EXT earned less) needs all of: W1 EXT minus other is -0.10R or less and the upper end of the 98% range is below 0; W2 the same inside each calendar month;
    W3 EXT is below other in each of 2016-19, 2020-22, 2023-26 (with 100+ filled trades of both kinds in it).
  BETTER (the late ones did NOT earn less; they earned more) needs the mirror image: B1 +0.10R or more with the lower end above 0, B2 the same inside each month, B3 EXT above other in each part.
  UNCLEAR: neither. Only WORSE changes anything (a plain note on the swing cards); the order of the list and the size of the amounts are never changed by this test.
Ranges: whole calendar months drawn at random 5000 times (the trades of one rally move together); each end of a range = the 1% percentile of those draws.

== 1. How far had the picks already come? (12-month gain at the close of the signal day) ==
12-month gain     signals  % of all   fresh  filled trades  average R   (+-)  average result
under 0%              824       11%      1%            438      +0.16   0.13          +1.16%
0% to 25%            2035       28%      2%           1114      +0.44   0.11          +2.84%
25% to 50%           1607       22%      4%            961      +0.28   0.10          +1.93%
50% to 75%           1017       14%      5%            651      +0.36   0.15          +2.79%
75% to 100%           602        8%      6%            424      +0.42   0.15          +3.31%
100% to 150%          532        7%      8%            403      +0.55   0.19          +5.01%
150% or more          587        8%     15%            450      +0.52   0.14          +5.52%
(+-) = one standard error of the average R, from drawing whole months at random; fresh = share of fresh breakouts in the band (the rest are about-to-break stocks).
Up 50% or more in 12 months: 2738 of 7204 signals (38%); 1928 of the 4441 filled trades. Median 12-month gain of a signal: +35%.
Of those, 8% were fresh breakouts and 92% about-to-break stocks.
Within 5% of their 52-week high on the signal day: 61% of the signals (the list is built from stocks near their highs). Already above the prior 50-day high: 9%.

== 2. Rule 1 (W1 / B1): EXT minus the others, every filled trade ==
+0.12R (standard error 0.10; 98% range -0.11 to +0.36)
worse side: needs -0.10R or less and the upper end below 0: no;   better side: needs +0.10R or more and the lower end above 0: no
For context only (the rule uses the 1% percentile): 11% of the resamples show a gap of 0 or less.

== 3. Rule 2 (W2 / B2): EXT minus the others inside the same month ==
109 of 125 months have at least 3 filled trades of both kinds; they hold 93% of the other trades and 96% of the EXT ones.
+0.22R (standard error 0.10; 98% range +0.01 to +0.44)
worse side: no;   better side: yes
The raw gap is +0.12R and the same-month gap +0.22R. The difference, -0.09R, is what the month a trade was made in (the market's mood) explains; the rest belongs to the stock's earlier gain.

== 4. Rule 3 (W3 / B3): in each part of the history ==
                         EXT trades  other trades    EXT R  other R  EXT - other
2016-19                         350           678    +0.44    +0.18        +0.26   better side ok
2020-22                         654           663    +0.37    +0.35        +0.02   better side ok
2023-26                         924          1172    +0.51    +0.40        +0.11   better side ok
needs EXT below the others in every part (worse) or above them in every part (better), with at least 100 filled trades of each kind in it.

== 5. Year by year (not part of the rule) ==
year      signals   % EXT  EXT trades  other trades    EXT R  other R  EXT - other
2016          275     23%          47           111    +0.50    +0.31        +0.19
2017          618     46%         196           179    +0.72    +0.42        +0.30
2018          414     29%          69           169    -0.36    -0.20        -0.17
2019          467     14%          38           219    +0.38    +0.20        +0.18
2020          604     31%         135           283    +0.73    +0.71        +0.02
2021          699     70%         354           133    +0.54    +0.26        +0.28
2022          654     33%         165           247    -0.27    -0.01        -0.26
2023          996     35%         259           387    +1.85    +1.15        +0.70
2024          875     68%         409           159    -0.09    +0.09        -0.18
2025          911     20%         127           382    -0.12    +0.12        -0.24
2026          691     29%         129           244    +0.36    -0.14        +0.50

== 6. What could explain it (not part of the rule): the same gap inside each kind of setup, market regime and third of the quality score ==
                         EXT trades  other trades    EXT R  other R  EXT - other
fresh breakout                  186            92    +0.59    +0.16        +0.42
about to break                 1742          2421    +0.44    +0.33        +0.10
healthy market                 1361          1671    +0.54    +0.37        +0.17
mixed market                    404           551    +0.36    +0.19        +0.17
weak market                     163           291    -0.11    +0.34        -0.45
score, lowest third             531           930    +0.66    +0.36        +0.30
score, middle third             626           870    +0.25    +0.36        -0.11
score, highest third            771           713    +0.48    +0.25        +0.23
(thirds of the quality score cut at 57 and 72; the score gives credit for a strong 12-month return, so EXT picks score higher)

== 7. Would another line do the same? (not part of the rule) ==
  line at  EXT trades  other trades  EXT - other  lower end  upper end
      25%        2889          1552        +0.04      -0.20      +0.26
      35%        2431          2010        +0.07      -0.16      +0.29
      50%        1928          2513        +0.12      -0.11      +0.36   <- the rule
      75%        1277          3164        +0.16      -0.07      +0.41
     100%         853          3588        +0.19      -0.05      +0.47
     150%         450          3991        +0.16      -0.13      +0.45

== 8. What taking the EXT picks at half the risk would have meant for an account (information only) ==

account: risk 1.0%, up to 8 stocks (luck band +-3.2 points of growth a year)
                                                     growth      worst   filled   average
                                                     a year   drawdown   trades per trade
as shipped                                           +12.1%     -22.7%      474    +0.35R
EXT picks at half the risk                           +10.0%     -13.3%      481    +0.39R
EXT picks left out                                    +8.4%     -17.1%      363    +0.35R
the same number of signals (2738) chosen at random and taken at half the risk, 30 draws: growth a year middle +10.6% (10th to 90th percentile +9.2% to +12.1%), worst drawdown middle -17.5% (deepest tenth -19.8%).
EXT at half the risk against the account as shipped: growth -2.1 points a year (luck band +-3.2: smaller than that cannot be told from luck), worst drawdown +9.4 points.
against arbitrary halving: growth +10.0% against the 10th percentile of the random draws +9.2%; worst drawdown -13.3% against -19.8%  -> no worse than arbitrary halving

account: risk 0.5%, up to 12 stocks (luck band +-1.6 points of growth a year)
                                                     growth      worst   filled   average
                                                     a year   drawdown   trades per trade
as shipped                                            +9.9%     -12.8%      716    +0.35R
EXT picks at half the risk                            +8.1%      -9.2%      711    +0.39R
EXT picks left out                                    +7.5%     -12.1%      616    +0.35R
the same number of signals (2738) chosen at random and taken at half the risk, 30 draws: growth a year middle +9.2% (10th to 90th percentile +8.5% to +9.9%), worst drawdown middle -11.9% (deepest tenth -13.6%).
EXT at half the risk against the account as shipped: growth -1.8 points a year (luck band +-1.6: smaller than that cannot be told from luck), worst drawdown +3.6 points.
against arbitrary halving: growth +8.1% against the 10th percentile of the random draws +8.5%; worst drawdown -9.2% against -13.6%  -> worse than 9 in 10 arbitrary halvings on growth
These are single histories: a difference smaller than the luck band cannot be told from luck, and the accounts are path-dependent (a different size changes which later orders find a free room). They are information for a sizing decision, not part of the rule.

== 9. The live picks, measured on their own candles (descriptive only; not part of the rule) ==
84 of the 85 picks in the live ledger (2026-10-21 to 2026-11-08) have candles in the history cache and are measured on them.

                                                             all picks   swing picks    BTST picks
picks measured                                                      84            35            49
up 50%+ in 3 months                                            0 of 84       0 of 35       0 of 49
up 50%+ in 6 months                                            9 of 84       2 of 35       7 of 49
up 50%+ in 12 months                                          12 of 83       3 of 35       9 of 48
up 50%+ from the lowest low of the last 6 months              10 of 84       3 of 35       7 of 49

median 3-month gain                                                +5%           +4%           +7%
median 6-month gain                                               +11%           +9%          +13%
median 12-month gain                                               +7%           +1%          +18%
median gain from the 6-month low                                  +20%          +19%          +22%

The 10 most extended live picks (by the gain from the 6-month low):
stock         scan   picked      setup     3 months  6 months  12 months  from 6-mo low
ATHERENERG    btst   2026-10-30  fresh         +42%      +87%      +139%          +101%
LAURUSLABS    btst   2026-10-26  primed        +29%      +82%      +136%           +91%
SAILIFE       btst   2026-11-06  primed        +26%      +64%       +94%           +88%
GLAND         btst   2026-10-29  primed        +21%      +75%       +60%           +78%
PAYTM         btst   2026-11-08  primed        +25%      +52%       +38%           +78%
COFORGE       btst   2026-10-26  primed        +34%      +63%       +13%           +75%
ACUTAAS       swing  2026-10-27  primed        +13%      +58%      +130%           +59%
SONACOMS      btst   2026-10-27  fresh         +18%      +51%       +84%           +53%
PVRINOX       swing  2026-10-21  primed        +42%      +51%       +29%           +52%
PPLPHARMA     swing  2026-11-05  primed        +15%      +44%       +17%           +51%

== 10. The verdict by the rule fixed above ==
rule 1, raw gap: +0.12R (standard error 0.10; 98% range -0.11 to +0.36): worse side no, better side no
rule 2, same-month gap: +0.22R (standard error 0.10; 98% range +0.01 to +0.44): worse side no, better side yes
rule 3, each part of the history: 2016-19 +0.26R, 2020-22 +0.02R, 2023-26 +0.11R: worse side no, better side yes

RESULT: neither side is shown by this data (the better-side rule 1 not met), so nothing changes in the scanner. The picks that had already risen 50% or more earned 0.12R more per filled trade over all, but that gap is too small, or too uncertain, to rely on.

== 11. Read this before relying on any of it ==
- The feeling was formed on the same market the replay covers. The rule above was written down (and committed to GitHub) before the numbers were calculated, which makes the test honest about what it asks; it does not make it a test on new data. The only new data is what comes next.
- The replay's stocks are today's NIFTY 500. A stock that rose a lot and then collapsed or was dropped from the index is not in it, which flatters the picks that had already risen a lot more than the others: a result of 'not worse' is weaker than it looks, a result of 'worse' is stronger.
- 12 months is not 'since the breakout'. A stock up 50% in a year may have made it last month or long ago; the replay's file holds no gain-since-the-base column, so the exact thing seen on a chart is not measured here (section 9 measures it for the live picks, over several windows).
- One history, not a forecast; the price-only score, no taxes and no slippage beyond the 0.35% cost.
- Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether late BTST picks do worse.
- The intervals draw whole calendar months: they allow for the trades of one rally moving together, not for anything longer than a month.
```

*Run note:* the study was run on `backtest_swing_yahoo_2026-11-04_trades.csv` with exactly the code of GitHub commit `f881c27`, which holds the rule and was pushed before the first run. Nothing in the printout was added or changed after that run, and no second run was made. The live-picks section (9) reads the ledger `tracking/trades.csv` and the candle cache `data_cache/btst_history.csv.gz` as they stood on 8 Nov 2026 (after the 2:56 PM BTST scan of that day).

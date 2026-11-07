# Pick-cap study: would fewer new picks a day have earned more, or made the list holdable?

Source: `backtest_swing_yahoo_2026-11-04_trades.csv`. How to read it: see the top of `backtest/pick_cap_study.py`.

```
Finished signals 7227 (4456 filled and sold, 2771 orders that never filled), 2016-06-08 to 2026-11-03 (10.4 years). The list as shipped: up to 15 new picks a day.

The test (fixed before the run): keep the first K signals of each day in the scanner's order, drop the rest, and run the two reference accounts of the portfolio study over what is left. The rule that decides is in section 5.

== 1. How many signals a day, and what a cap takes away ==
1985 days with a signal; signals a day: average 3.64, middle 3, most 15
cap a day     days it bites  signals dropped  signals a day after
as shipped                -                -                 3.64
8                        6%               5%                 3.44
5                       20%              17%                 3.01
3                       41%              36%                 2.31
2                       56%              52%                 1.75
1                       75%              73%                 1.00

== 2. What the two accounts would have earned with each cap (the scanner's order; 'random picks' = K signals of each day drawn at random) ==

account: risk 1.0%, up to 8 stocks (luck band: +-3.2 points of growth a year)
cap a day      growth      worst   orders  filled   average     100   random picks of that size: growth a year   shipped
               a year   drawdown   a year  trades per trade  became   middle (10th to 90th percentile of 30 draws)    beat
as shipped     +12.1%     -22.7%       74     474    +0.35R    3.3x   +12.3% (+9.0% to +15.4%)                       47%
8              +11.4%     -22.7%       75     478    +0.32R    3.1x   +12.1% (+9.1% to +15.4%)                       40%
5              +11.5%     -21.5%       75     477    +0.33R    3.1x   +12.4% (+9.3% to +14.5%)                       37%
3              +11.4%     -18.9%       72     473    +0.34R    3.1x   +11.3% (+8.3% to +13.9%)                       53%
2              +12.3%     -16.1%       74     471    +0.35R    3.4x   +11.0% (+8.2% to +14.2%)                       73%
1              +14.8%     -21.7%       70     452    +0.42R    4.2x   +11.2% (+6.5% to +14.5%)                       90%

account: risk 0.5%, up to 12 stocks (luck band: +-1.6 points of growth a year)
cap a day      growth      worst   orders  filled   average     100   random picks of that size: growth a year   shipped
               a year   drawdown   a year  trades per trade  became   middle (10th to 90th percentile of 30 draws)    beat
as shipped      +9.9%     -12.8%      115     716    +0.35R    2.7x   +9.1% (+7.4% to +10.6%)                        73%
8               +9.8%     -12.8%      114     716    +0.35R    2.6x   +9.0% (+7.3% to +10.6%)                        73%
5               +9.9%     -12.8%      114     716    +0.35R    2.7x   +8.9% (+7.3% to +10.9%)                        67%
3               +9.4%     -14.9%      113     720    +0.33R    2.6x   +9.1% (+7.4% to +11.1%)                        60%
2              +10.9%     -14.0%      113     720    +0.37R    2.9x   +9.3% (+7.1% to +11.1%)                        83%
1               +9.4%     -20.7%      107     695    +0.32R    2.5x   +7.8% (+5.8% to +10.5%)                        77%
worst drawdown is from the highest weekly value to the lowest after it; a cap only changes what an account does on days it would have had a free room for a signal beyond the K-th - an account that is usually full barely notices it.

== 3. Would the whole list become holdable? (every listed signal bought, at the message's sizing) ==
cap a day     stocks open at once: average  90th percentile  peak   money needed: average  90th percentile
as shipped                              60              105   176                    817%            1483%
8                                       56               96   154                    762%            1373%
5                                       48               80   125                    652%            1141%
3                                       37               59    88                    498%             842%
2                                       28               43    68                    368%             628%
1                                       16               25    40                    202%             352%
100% = all of the capital. A list that needs more than that cannot be held whole by anyone; a cap that leaves it far above 100% shortens the list but does not make it holdable - the limit that does is the number of stocks one holds (the capital plan on the dashboard).

== 4. Are the first picks of a day better than the later ones? (filled trades, after costs) ==
place in its day    signals  filled  filled %  average R    (+-)  average result
1st                    1985    1253       63%      +0.32    0.06          +2.72%
2nd                    1492     913       61%      +0.37    0.08          +2.98%
3rd                    1116     669       60%      +0.38    0.08          +3.08%
4th-5th                1382     823       60%      +0.31    0.07          +2.35%
6th-8th                 863     541       63%      +0.49    0.09          +3.50%
9th or lower            389     257       66%      +0.72    0.15          +4.89%
(+-) = one standard error of the average R; trades of the same day move together, so the real uncertainty is larger than it shows.

The same trades by how many signals their DAY had (a 9th place can only exist on a busy day, so the table above mixes the place with the kind of day):
signals that day     days  signals  filled  average R    (+-)  average result
1 signal              493      493     288      +0.22    0.12          +1.54%
2 signals             376      752     423      +0.16    0.10          +1.51%
3 signals             307      921     551      +0.30    0.08          +2.23%
4-5 signals           411     1819    1104      +0.41    0.07          +3.55%
6-8 signals           272     1845    1141      +0.36    0.07          +2.73%
9 or more             126     1397     949      +0.56    0.08          +4.15%
On days with 1 signal a filled trade averaged +0.22R, on days with 9 or more +0.56R. This was found after looking at the data, in a table nobody fixed beforehand, and the trades of one day move together (there are only 126 days in the last row): nothing uses it, and it would need its own test, fixed in advance, before anything did.

== 5. The verdict by the rule fixed above ==

cap 1 a day
  risk 1.0%, up to 8: growth +2.6 points a year (luck band +-3.2): same; drawdown +1.0 points: ok
  risk 0.5%, up to 12: growth -0.5 points a year (luck band +-1.6): same; drawdown -7.9 points: deeper than allowed
  the picks it drops average +0.41R (3203 trades), the ones it keeps +0.32R (1253 trades): the kept ones are 1.1 standard errors below the dropped ones (the rule needs them 2 or more above): no
  -> does not pass

cap 2 a day
  risk 1.0%, up to 8: growth +0.2 points a year (luck band +-3.2): same; drawdown +6.6 points: ok
  risk 0.5%, up to 12: growth +1.0 points a year (luck band +-1.6): same; drawdown -1.2 points: ok
  the picks it drops average +0.42R (2290 trades), the ones it keeps +0.34R (2166 trades): the kept ones are 1.2 standard errors below the dropped ones (the rule needs them 2 or more above): no
  -> does not pass

cap 3 a day
  risk 1.0%, up to 8: growth -0.7 points a year (luck band +-3.2): same; drawdown +3.8 points: ok
  risk 0.5%, up to 12: growth -0.4 points a year (luck band +-1.6): same; drawdown -2.1 points: deeper than allowed
  the picks it drops average +0.44R (1621 trades), the ones it keeps +0.35R (2835 trades): the kept ones are 1.3 standard errors below the dropped ones (the rule needs them 2 or more above): no
  -> does not pass

cap 5 a day
  risk 1.0%, up to 8: growth -0.6 points a year (luck band +-3.2): same; drawdown +1.2 points: ok
  risk 0.5%, up to 12: growth +0.0 points a year (luck band +-1.6): same; drawdown -0.0 points: ok
  the picks it drops average +0.57R (798 trades), the ones it keeps +0.34R (3658 trades): the kept ones are 2.6 standard errors below the dropped ones (the rule needs them 2 or more above): no
  -> does not pass

cap 8 a day
  risk 1.0%, up to 8: growth -0.7 points a year (luck band +-3.2): same; drawdown -0.0 points: ok
  risk 0.5%, up to 12: growth -0.1 points a year (luck band +-1.6): same; drawdown -0.0 points: ok
  the picks it drops average +0.72R (257 trades), the ones it keeps +0.36R (4199 trades): the kept ones are 2.4 standard errors below the dropped ones (the rule needs them 2 or more above): no
  -> does not pass

RESULT: no cap passes. The data cannot show that a cap earns more, so the swing list stays at up to 15 new picks a day.

== 6. Read this before relying on any of it ==
- One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost: the levels are flattered, the comparison between caps less so.
- Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list (its cap is 8 a day): nothing here says what a cap on BTST would do.
- The cap is applied to the signals that were listed anyway; a stock dropped by the cap could have been listed again on a later day and the cool-down would then have worked differently. A full re-run is not needed unless a cap passes.
- Differences smaller than the luck band are noise: which signals came on which days changes an account's yearly growth by about that much.
- A cap also slows the live record: fewer picks a day means fewer sold trades to judge the scanner by.
```

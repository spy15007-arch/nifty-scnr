# Market-strength study: do trades made in a very strong market earn more?

Source: `backtest_swing_yahoo_2026-11-04_trades.csv`. How to read it: see the top of `backtest/market_strength_study.py`.

```
Finished signals 7227 (4456 filled and sold, 2771 orders that never filled), 2016-06-08 to 2026-11-03 (10.4 years), 125 calendar months.

The question: do the filled trades made when 77% or more of the market is above its 50-day average (a VERY STRONG market) earn more than the rest?
The rule (fixed before the run; the full text is at the top of backtest/market_strength_study.py). All four parts must hold:
  1. strong minus other is at least +0.20R per filled trade, and luck is ruled out (the lower end of the 98% range is above 0);
  2. not a few years: strong beats other in two thirds of the calendar years that have 30+ filled trades of both (at least 5 such years), and the gap is still +0.15R or more after dropping any one of them;
  3. the gap is +0.10R or more in each of the parts of the history (2016-19, 2020-22, 2023-26), with 100+ filled trades of both in it;
  4. with the line drawn at 72% and 82% instead of 77%, the gap is still +0.10R or more.
Ranges: whole calendar months drawn at random 5000 times (the trades of one rally move together); 'lower end' = the 1% percentile of those draws.

== 1. The two kinds of market ==
                             days  signals  filled %  filled trades  average R   (+-)  average result    healthy / mixed / weak
very strong (77%+)            513     2347       65%           1519      +0.70   0.14          +5.26%            100% / 0% / 0%
the rest (under 77%)         1472     4880       60%           2937      +0.22   0.11          +1.80%           50% / 33% / 16%
(+-) = one standard error of the average R, from drawing whole months at random. Very strong markets were 26% of the 1985 days with a signal.

== 2. Rule 1: very strong minus the rest, every filled trade ==
+0.48R (standard error 0.16; 98% range +0.09 to +0.85)   needs at least +0.20R and the lower end above 0: yes
For context only (the rule uses the 1% percentile): at the usual 5% the lower end would be +0.20R, and 0% of the resamples show a gap of 0 or less.

== 3. Rule 2: year by year ==
year     strong trades  other trades  strong R  other R     gap  without this year
2016                74            84     +0.43    +0.31   +0.11              +0.49
2017               150           226     +1.01    +0.29   +0.72              +0.45
2018                25           213     -0.64    -0.20   -0.44                  -   (under 30 trades of one kind: not judged)
2019                31           227     -0.12    +0.27   -0.39              +0.50
2020               242           177     +0.76    +0.66   +0.10              +0.49
2021               200           290     +0.48    +0.45   +0.04              +0.54
2022               124           288     +0.11    -0.21   +0.32              +0.48
2023               375           272     +1.35    +1.55   -0.20              +0.40
2024               150           421     +0.57    -0.27   +0.84              +0.41
2025               129           382     +0.06    +0.06   +0.01              +0.51
2026                19           357     -0.04    +0.05   -0.09                  -   (under 30 trades of one kind: not judged)
9 judgeable years (needs 5); strong beats other in 7 of them (needs 6); the smallest gap left after dropping one year is +0.40R (without 2023) (needs +0.15R or more): yes

== 4. Rule 3: in each part of the history ==
           strong trades  other trades  strong R  other R     gap
2016-19              280           750     +0.58    +0.15   +0.43   ok
2020-22              566           755     +0.52    +0.24   +0.28   ok
2023-26              673          1432     +0.89    +0.24   +0.65   ok
needs +0.10R or more in every part, with at least 100 filled trades of each kind in it.

== 5. Rule 4: is the line a knife-edge? (and the gap for other lines, shown only) ==
  line at  strong trades  other trades      gap
      50%           3617           839    +0.19
      55%           3343          1113    +0.21
      60%           3041          1415    +0.21
      65%           2588          1868    +0.35
      70%           2140          2316    +0.35
      72%           1954          2502    +0.41   <- rule 4
      75%           1719          2737    +0.41
      77%           1519          2937    +0.48   <- the rule
      80%           1183          3273    +0.55
      82%           1014          3442    +0.53   <- rule 4
      85%            678          3778    +0.58
      90%            234          4222    +0.33
needs +0.10R or more with the line at 72% and at 82%: yes

== 6. Average R by bucket of breadth (shown only: a step, or a slope?) ==
breadth        days  filled trades  average R   (+-)  average result
  0% to 30%     172            203      +0.25   0.17          +1.54%
 30% to 40%     172            252      +0.13   0.19          +1.23%
 40% to 50%     224            384      +0.28   0.22          +2.80%
 50% to 60%     266            576      +0.25   0.19          +2.12%
 60% to 70%     392            901      +0.17   0.14          +1.52%
 70% to 77%     246            621      +0.24   0.11          +1.62%
 77% to 85%     290            841      +0.55   0.17          +4.11%
 85% to 100%    223            678      +0.87   0.18          +6.68%

== 7. What using it would have meant for an account (information only, not part of the rule) ==

account: risk 1.0%, up to 8 stocks (luck band +-3.2 points of growth a year)
                                                     growth      worst   filled   average   random accounts that flag as many signals: growth a year, worst drawdown
                                                     a year   drawdown   trades per trade   (middle; 10th to 90th percentile of 30); this one beat
as shipped                                           +12.1%     -22.7%      474    +0.35R
1.5x the risk in a very strong market                +12.9%     -20.8%      449    +0.34R   +11.7% (+9.3% to +14.4%), -22.7% (-26.7% to -19.7%); beat 73% of them
0.5x the risk when the market is not very strong      +8.0%     -14.2%      485    +0.32R   +9.4% (+7.8% to +10.2%), -13.0% (-15.7% to -11.4%); beat 13% of them
only the signals made in a very strong market         +5.4%     -23.2%      201    +0.32R   +9.8% (+7.6% to +13.3%), -24.8% (-28.5% to -20.2%); beat 3% of them

account: risk 0.5%, up to 12 stocks (luck band +-1.6 points of growth a year)
                                                     growth      worst   filled   average   random accounts that flag as many signals: growth a year, worst drawdown
                                                     a year   drawdown   trades per trade   (middle; 10th to 90th percentile of 30); this one beat
as shipped                                            +9.9%     -12.8%      716    +0.35R
1.5x the risk in a very strong market                 +9.2%     -15.6%      717    +0.29R   +10.8% (+9.7% to +11.9%), -16.3% (-18.6% to -13.8%); beat 3% of them
0.5x the risk when the market is not very strong      +7.7%      -8.6%      710    +0.40R   +7.6% (+6.7% to +8.1%), -9.4% (-11.3% to -8.4%); beat 57% of them
only the signals made in a very strong market         +5.6%     -15.9%      309    +0.41R   +8.3% (+6.0% to +9.7%), -17.6% (-20.7% to -15.1%); beat 10% of them
These are single histories: a difference smaller than the luck band cannot be told from luck, and the accounts are path-dependent (a different size changes which later orders find a free room). They say what the pattern would have been worth if it is real; they do not make it real.

== 8. The verdict by the rule fixed above ==
rule 1, raw gap: +0.48R (standard error 0.16; 98% range +0.09 to +0.85): yes
rule 2, not a few years: 7 of 9 judgeable years positive (needs 6), smallest gap after dropping a year +0.40R: yes
rule 3, each part of the history: 2016-19 +0.43R, 2020-22 +0.28R, 2023-26 +0.65R: yes
rule 4, other lines: 72% +0.41R, 82% +0.53R: yes

RESULT: every rule is met. The pattern is shown by this data: the swing message may add one plain sentence on days when 77% or more of the market is above its 50-day average. The sizing rules are not changed by this test.

== 9. Read this before relying on any of it ==
- The observation was found in this same data, in a table of six groups nobody had fixed beforehand, and the 77% line is where the top third of the trades begins. The rule above was written down (and committed to GitHub) before the numbers were calculated, which makes the test honest about what it asks; it does not make it a test on new data.
- Very strong markets come in a few long spells (see the year table): the number of independent episodes behind the gap is far smaller than the number of trades, which is why rules 2 and 3 exist and why the ranges draw whole months.
- One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost.
- Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether a very strong market helps BTST.
- Breadth here is the share of the replay's universe (today's liquid NIFTY 500 stocks with enough history) above their 50-day average; the live message's breadth line is computed the same way from the live universe, but the two are not the same list of stocks.
```

*Run note:* the study was run on `backtest_swing_yahoo_2026-11-04_trades.csv` with exactly the code of GitHub commit `f881c27`, which holds the rule and was pushed before the first run. Nothing in the printout was added or changed after that run, and no second run was made.

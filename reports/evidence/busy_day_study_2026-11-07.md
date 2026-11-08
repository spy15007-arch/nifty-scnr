# Busy-day study: do busy days pay more than quiet ones, beyond the market's mood?

Source: `backtest_swing_yahoo_2026-11-04_trades.csv`. How to read it: see the top of `backtest/busy_day_study.py`.

```
Finished signals 7227 (4456 filled and sold, 2771 orders that never filled), 2016-06-08 to 2026-11-03 (10.4 years), 125 calendar months.

The question: do the filled trades of BUSY days (4 or more new signals) earn more than those of QUIET days (1-2)? A day in between is only shown.
The rule (fixed before the run; the full text is at the top of backtest/busy_day_study.py). All four parts must hold:
  1. busy minus quiet is at least +0.10R per filled trade, and luck is ruled out (the lower end of the 98% range is above 0);
  2. the same, taken inside each calendar month (so the mood of a month cannot make it);
  3. busy beats quiet in each of the parts of the history (2016-19, 2020-22, 2023-26), with at least 100 filled trades of each kind in it;
  4. an account that takes the quiet-day signals at half the risk is no worse (growth a year, worst drawdown) than the 10th percentile of 30 accounts that halve as many random signals.
Ranges: whole calendar months drawn at random 5000 times (the trades of one rally move together); 'lower end' = the 1% percentile of those draws.

== 1. The three kinds of day ==
kind of day        days  signals  filled %  filled trades  average R   (+-)  average result
quiet (1-2)         867     1242       57%            710      +0.19   0.09          +1.55%
typical (3)         307      921       60%            551      +0.30   0.10          +2.23%
busy (4+)           811     5064       63%           3195      +0.44   0.11          +3.43%
(+-) = one standard error of the average R, from drawing whole months at random.

What the signals of each kind of day were like:
kind of day        healthy / mixed / weak market   fresh  quality  breadth   stop
quiet (1-2)                      47% / 27% / 25%      4%       68      56%   7.2%
typical (3)                      61% / 24% / 15%      4%       67      63%   7.2%
busy (4+)                         72% / 21% / 7%      5%       68      68%   7.2%
fresh = share of fresh breakouts (the rest are about-to-break stocks); quality = average quality score; breadth = share of the market above its 50-day average on the signal day; stop = average distance to the first stop.

== 2. Rule 1: busy minus quiet, every filled trade ==
+0.25R (standard error 0.12; 98% range -0.02 to +0.54)   needs at least +0.10R and the lower end above 0: no
This mixes two things: the day's count, and the market the day happened in (a strong month has busy days and pays more).
For context only (the rule uses the 1% percentile): at the usual 5% the lower end would be +0.05R, and 2% of the resamples show a gap of 0 or less.

== 3. Rule 2: busy minus quiet inside the same month ==
102 of 125 months have at least 3 filled trades of both kinds; they hold 89% of the quiet-day trades and 84% of the busy-day ones.
+0.08R (standard error 0.09; 98% range -0.13 to +0.30)   needs at least +0.10R and the lower end above 0: no
For context only: at the usual 5% the lower end would be -0.07R, and 19% of the resamples show a gap of 0 or less.
The raw gap is +0.25R and the same-month gap +0.08R. The difference, +0.17R, is what the month a day fell in (the market's mood) explains; the rest belongs to the day itself.

== 4. Rule 3: in each part of the history (raw difference) ==
                       quiet trades busy trades    quiet R    busy R busy - quiet
2016-19                         268         584      +0.25     +0.28        +0.02   ok
2020-22                         218         921      +0.12     +0.44        +0.32   ok
2023-26                         224        1690      +0.17     +0.49        +0.32   ok
needs busy above quiet in every part, with at least 100 filled trades of each kind in it.

== 5. What could explain it (not part of the rule): the same difference inside each market regime and each third of breadth ==
                       quiet trades busy trades    quiet R    busy R busy - quiet   all trades     all R
healthy market                  346        2351      +0.29     +0.48        +0.19         3041     +0.45
mixed market                    189         647      +0.06     +0.32        +0.26          960     +0.26
weak market                     175         197      +0.11     +0.33        +0.21          455     +0.19
breadth, lowest third           378         891      +0.05     +0.30        +0.25         1484     +0.22
breadth, middle third           197        1102      +0.03     +0.26        +0.23         1485     +0.22
breadth, highest third          135        1202      +0.81     +0.71        -0.10         1487     +0.71
(thirds of breadth cut at 61% and 77% of the market above its 50-day average; 'all trades' = every filled trade of the class, whatever kind of day it came on)

== 6. Would another cut-off do the same? (not part of the rule) ==
quiet is up to    busy is from  quiet trades  busy trades    rule 1  lower end    rule 2  lower end
1                            4           287         3195     +0.22      -0.17     +0.07      -0.35
2                            4           710         3195     +0.25      -0.02     +0.08      -0.13   <- the rule
2                            6           710         2090     +0.27      -0.04     -0.04      -0.29
2                            9           710          949     +0.38      +0.01     -0.01      -0.35
3                            4          1261         3195     +0.20      -0.03     +0.07      -0.11
3                            6          1261         2090     +0.22      -0.05     -0.01      -0.22

== 7. Rule 4: the two accounts with the quiet-day signals at half the risk ==

account: risk 1.0%, up to 8 stocks
                                                 growth      worst   filled   average
                                                 a year   drawdown   trades per trade
as shipped                                       +12.1%     -22.7%      474    +0.35R
quiet days at half the risk (the rule)            +8.6%     -23.4%      491    +0.29R
quiet days left out (information only)           +10.6%     -29.2%      417    +0.36R
the same number of signals (1242) chosen at random and taken at half the risk, 30 draws: growth a year middle +11.1% (10th to 90th percentile +9.3% to +12.6%), worst drawdown middle -18.7% (deepest tenth -23.2%).
against the account as shipped (information): growth -3.5 points a year (luck band +-3.2: smaller than that cannot be told from luck), worst drawdown -0.7 points.
the rule: growth +8.6% against the 10th percentile of the random draws +9.3%: no; worst drawdown -23.4% against -23.2%: no  -> HARM

account: risk 0.5%, up to 12 stocks
                                                 growth      worst   filled   average
                                                 a year   drawdown   trades per trade
as shipped                                        +9.9%     -12.8%      716    +0.35R
quiet days at half the risk (the rule)            +9.6%     -11.9%      711    +0.39R
quiet days left out (information only)            +8.6%     -23.0%      666    +0.33R
the same number of signals (1242) chosen at random and taken at half the risk, 30 draws: growth a year middle +10.2% (10th to 90th percentile +9.3% to +10.9%), worst drawdown middle -12.0% (deepest tenth -13.9%).
against the account as shipped (information): growth -0.3 points a year (luck band +-1.6: smaller than that cannot be told from luck), worst drawdown +0.9 points.
the rule: growth +9.6% against the 10th percentile of the random draws +9.3%: yes; worst drawdown -11.9% against -13.9%: yes  -> no harm
Halving trades that earn something on average can only lower what an account earns, so the account cannot show a gain here; and its luck is wide. What the rule asks is that halving the QUIET days is not clearly more costly than halving arbitrary signals.

== 8. The verdict by the rule fixed above ==
rule 1, raw difference: +0.25R (standard error 0.12; 98% range -0.02 to +0.54): no
rule 2, same-month difference: +0.08R (standard error 0.09; 98% range -0.13 to +0.30): no
rule 3, each part of the history: 2016-19 +0.02R, 2020-22 +0.32R, 2023-26 +0.32R: yes
rule 4, risk 1.0%, up to 8: growth +8.6% (floor +9.3%), worst drawdown -23.4% (floor -23.2%): no
rule 4, risk 0.5%, up to 12: growth +9.6% (floor +9.3%), worst drawdown -11.9% (floor -13.9%): yes

RESULT: the pattern is not shown by this data (rules 1 and 2 not met), so nothing changes in the scanner. The gap between busy and quiet days is too small, or too uncertain, to rely on.

== 9. Read this before relying on any of it ==
- The observation was found in this same data, in a table nobody had fixed beforehand. The rule above was written down (and committed to GitHub) before the new numbers were calculated, which makes the test honest about what it asks; it does not make it a test on new data. The only new data is what comes next.
- One history, not a forecast; today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost.
- Swing scan only. The BTST scan cannot be replayed on daily candles, and it supplies most of the live list: nothing here says whether a quiet or a busy BTST day differs.
- Day size here is every signal the replay made that day. The live evening message only lists the NEW picks (those BTST had not already put on the list), so it would have to count the whole day's signals, not the ones it prints.
- The intervals draw whole calendar months: they allow for the trades of one rally moving together, not for anything longer than a month.
- After the first run the printout gained a few descriptive lines: the share of resamples at or below 0 and the 5% lower end in sections 2 and 3, and the 'all trades' columns in section 5. They show how close the calls were and what the market's mood does by itself; the rule, its numbers and its verdict were not touched (the first run was made with the code of the GitHub commit before this one).
```

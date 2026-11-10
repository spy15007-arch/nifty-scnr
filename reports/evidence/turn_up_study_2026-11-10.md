# Early turn-up study: would buying the first strong close back above the averages have paid more?

Source: `backtest_swing_yahoo_2026-11-10_trades.csv and backtest_swing_turnup_yahoo_2026-11-10_trades.csv`. How to read it: see the top of `backtest/turn_up_study.py`.

```
Finished signals: 2706 of the early turn-up lane and 7233 shipped picks, 2016-06-08 to 2026-11-09 (10.4 years), 126 calendar months.

The question: would buying the FIRST STRONG CLOSE BACK ABOVE THE AVERAGES (a stock 3-15% below its 50-day high, up 3%+ on the day) have earned more per filled trade than the shipped picks?
The rule (fixed before the run; the full text is at the top of backtest/turn_up_study.py):
  L1  the lane pays on its own: its average is +0.20R or more and the lower end of the 98% range is above 0.
  BETTER than the shipped picks needs all of: G1 lane minus shipped is +0.10R or more with the lower end above 0; G2 the same inside each calendar month;
    G3 the lane is above the shipped picks in each of 2016-19, 2020-22, 2023-26 (with 100+ filled trades of both kinds in it). WORSE is the mirror image.
  Lane worth adding = L1 met AND BETTER; it is then proposed to the user, never switched on by itself. Anything else: nothing changes in the scanner.
Ranges: whole calendar months drawn at random 5000 times; each end of a range = the 1% percentile of those draws.

== 1. What each kind of signal did (filled and sold trades, after costs) ==
                        signals   filled      avg R      won  lost money      avg result
early turn-up lane         2706     2366      +0.34      40%         60%          +3.52%
shipped picks              7233     4457      +0.38      39%         61%          +2.98%
(lost money = share of filled trades that ended below their buy price after costs; signals include orders that never filled.)

== 2. Rule L1: does the lane pay on its own? ==
+0.34R (standard error 0.08; 98% range +0.15 to +0.53)
needs +0.20R or more and the lower end above 0: yes

== 3. Rule G1: lane minus shipped picks, every filled trade ==
-0.04R (standard error 0.07; 98% range -0.21 to +0.12)
better side: needs +0.10R or more and the lower end above 0: no;   worse side: no

== 4. Rule G2: the same inside each calendar month ==
122 of 126 months have at least 3 filled trades of both kinds.
+0.11R (standard error 0.06; 98% range -0.02 to +0.25)
better side: no;   worse side: no

== 5. Rule G3: in each part of the history ==
             lane trades   shipped   lane R  shipped R      gap
2016-19              481      1030    +0.20      +0.27    -0.07
2020-22              795      1321    +0.38      +0.36    +0.02
2023-26             1090      2106    +0.36      +0.45    -0.08
lane above the shipped picks in every part: no;   below in every part: no

== 6. The answer ==
RESULT: The lane averaged +0.34R per filled trade (meeting the +0.20R bar with its range above 0) and -0.04R less than the shipped picks. Neither side is shown (the worse-side rule G1, G2 and G3 not met), so nothing changes in the scanner and the data cannot say whether entering at the turn-up beats the shipped entry.

== 7. For information only (not part of the rule) ==
The lane year by year (shipped picks for comparison):
year      lane signals  lane filled   lane R  shipped filled  shipped R      gap
2016                76           59    -0.36             158      +0.37    -0.73
2017               160          144    +0.72             376      +0.58    +0.15
2018               171          148    -0.13             238      -0.25    +0.11
2019               160          130    +0.24             258      +0.22    +0.01
2020               261          235    +0.82             419      +0.72    +0.11
2021               324          295    +0.41             490      +0.46    -0.05
2022               309          265    -0.04             412      -0.12    +0.08
2023               236          217    +1.51             647      +1.44    +0.07
2024               381          346    -0.02             571      -0.05    +0.03
2025               325          279    +0.14             511      +0.06    +0.08
2026               303          248    +0.15             377      +0.04    +0.11

The lane by how deep the pullback was:
pullback below the 50-day high      signals   filled      avg R
3% to 6%                               1044      898      +0.30
6% to 10%                              1122      998      +0.35
10% to 15%                              539      469      +0.37

The lane by the day's volume against the 15-day average:
                        signals   filled      avg R      won  lost money      avg result
volume 1.5x or more        1110      944      +0.31      37%         63%          +2.82%
volume under 1.5x          1596     1422      +0.35      42%         58%          +3.98%

The lane by the market regime of the signal day:
                        signals   filled      avg R      won  lost money      avg result
healthy                    1392     1233      +0.36      39%         61%          +3.47%
mixed                       731      633      +0.31      39%         61%          +3.52%
weak                        583      500      +0.31      41%         59%          +3.64%

Matched episodes: of 2758 lane signals, the shipped rule listed the same stock within 60 calendar days after 1590 (58%); the rest never got back near their old high in that time (or were not listed).
For those, the shipped buy price was +6.7% above the lane's (median; mean +9.1%), 17 calendar days later (median).
(Only the stocks that did recover appear here, so this is the cost of the later entry on the winners; the lane's own failures are in section 1.)
```

**In plain words.** Buying the first strong close back above the averages paid (+0.34R per filled trade, bar +0.20R, range clear of 0), but it did **not** earn more per trade than the shipped entry (+0.38R). Over every filled trade the lane was 0.04R behind (range -0.21R to +0.12R); inside the same calendar month it was 0.11R ahead (range -0.02R to +0.25R); and by part of the history it was ahead in one of three. The rule fixed in advance needed all three to point the same way with the ranges clear of 0, so the answer is **unclear**: nothing is proposed and nothing changes in the scanner. Where the shipped rule did list the same stock later (58% of the lane's signals, within 60 days) its buy price was a median 6.7% higher, 17 days on; but the other 42% of turn-ups were not followed by a shipped listing in that time, 60% of all the lane's filled trades lost money after costs (61% of the shipped picks'), and over all of them the average per trade came out about even. PAYTM and NH were turn-ups that worked; most do not.

**Seen in the tables, not part of the rule, so not relied on** (looked at after the run; no rule was changed because of it):
- *Why the all-trades gap and the same-month gap disagree:* the shipped picks bunch in the strong year 2023 (14.5% of their filled trades against 9.2% of the lane's; every filled trade of that year averaged +1.45R), which props up their all-trades average; inside one month that head start is gone. Month by month the lane was ahead in 65 of the 122 months and the shipped picks in 57; the median month was +0.05R against the weighted average of +0.11R, so a few large months pull the average up.
- *By year* the lane was ahead in 9 of 11 years (by +0.01R to +0.15R), slightly behind in 2021 (-0.05R) and far behind in 2016 (59 trades, -0.36R against +0.37R). Years are not independent, and this is no part of the rule.
- *Depth of the pullback* made little difference (+0.30R at 3%-6% below the high, +0.35R at 6%-10%, +0.37R at 10%-15%), nor did the day's volume (+0.31R at 1.5 times the usual or more, +0.35R under) or the market regime (+0.36R healthy, +0.31R mixed, +0.31R weak).

**Run note.** The study was run once on `backtest_swing_yahoo_2026-11-10_trades.csv` (the shipped replay) and `backtest_swing_turnup_yahoo_2026-11-10_trades.csv` (the lane), both written by the Backtest & Calibrate run of 10 Nov 2026 (backtest, swing, yahoo), with exactly the analysis code of GitHub commit `b1cbe9f`, which holds the rule and was pushed before these numbers existed; the later commit `869769d` only added lines to the printout of the first look on the saved candles. Nothing in the printout was added or changed after the run and no second run was made. The "matched episodes" line counts all 2,758 lane signals, including the 52 not yet finished (48 still open, 4 waiting for their buy price); everything else counts the 2,706 finished ones. The numbers were then recalculated from the two files with a separate script (its own month-by-month resampling, two other random seeds, 20,000 draws): same trade counts, averages, parts-of-history table and episode figures, and ranges within 0.01R of the ones above. Against the 4 Nov run of the same replay, the shipped file differs only in the last three months (trades that were still open or waiting then have moved on) and in a handful of 3 Nov signals (3 new, 6 with quality scores a few points different); no older signal changed, so the engine's new hook left the shipped replay alone.

**Limits.** The idea came from two charts that worked, and the thresholds were set by reading those two charts, not any outcome statistic. The replay's stocks are today's NIFTY 500: stocks that turned up, failed and then fell out of the index are missing, which flatters an early entry more than the shipped one, so "pays" is weaker than it looks. The quality score is price-only. Swing signals only (the BTST scan cannot be replayed on daily candles). The test compares trades one at a time; it does not say what the early lane would add to an account that can hold only a few stocks - 58% of its signals are followed within 60 days by a shipped listing of the same stock, so many of its trades would be earlier entries into stocks the scanner lists anyway rather than new stocks, and the account would have to drop weaker shipped picks to make room. One history of ten years, not a forecast.

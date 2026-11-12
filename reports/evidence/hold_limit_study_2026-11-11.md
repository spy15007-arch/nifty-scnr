# Hold-limit study: is the 60-session time limit selling winners too early?

Source: `backtest_swing_yahoo_2026-11-11_trades.csv`. The rule and how to read it: see the top of `backtest/hold_limit_study.py`.

```
Signals 7308, filled and sold under the shipped plan 4457, 2016-06-08 to 2026-11-09; time limits tried: 60, 90, 120, 180, 250 sessions (same entries, same first stop, same trail, same costs)

== 1. The plan as shipped: how the trades end ==
ended by        trades   share  average R  median R  share of all the profit
gap_stop           274      6%      -0.83     -1.14                     -13%
same_day_stop       43      1%      -1.07     -1.07                      -3%
stop              1980     44%      -1.05     -1.05                    -123%
time              1501     34%      +2.48     +1.95                     219%
trail              659     15%      +0.51     -0.01                      20%
The trades the 60-session limit sold, on average, in their last weeks (R after costs):
  30 sessions after the purchase: +1.33R over 1501 trades
  35 sessions after the purchase: +1.53R over 1501 trades
  40 sessions after the purchase: +1.73R over 1501 trades
  45 sessions after the purchase: +1.95R over 1501 trades
  50 sessions after the purchase: +2.16R over 1501 trades
  55 sessions after the purchase: +2.35R over 1501 trades

== 2. 60 sessions -> 90 sessions: the trades still open at session 60 are kept longer ==
matched filled trades 4340 (120 calendar months), signals up to 2026-06-22; the longer limit changed 1481 of them (34%), the ones the earlier limit had sold
left out of the comparison: 0 trades the earlier limit sold that the longer limit could not follow, because the data jumps more than 35% in one session inside the extra weeks (the replay treats that as a data fault)
check: of the 2859 trades the earlier limit did not sell, 0 have another R under the longer one (expected 0)
RULE 1 pays per trade: average change +0.095R (98% range +0.028R to +0.166R); needs at least +0.05R and the lower end above 0 -> met
RULE 2 pays for the room it takes: the extra +0.0111R per extra session held against the earlier limit's own +0.0121R per session; difference -0.0009R (98% range -0.0105R to +0.0090R) -> NOT met
RULE 3 in each part of the history (average change per matched trade): 2016-19 +0.002R (1030 trades); 2020-22 +0.200R (1321 trades); 2023-26 +0.074R (1989 trades) -> met
RULE 4 the accounts (same signals, one position per stock; growth a year / worst drawdown / stocks held / money in stocks):
  risk 1.0%, up to 8: 60 sessions +12.6% / -22.7% / 6.2 / 61%;  90 sessions +13.7% / -20.7% / 6.4 / 60%  (growth +1.1 points against a luck band of 3.3; drawdown +2.0 points against 3.6; signals dropped because the stock was still held: 0 / 114) -> ok
  risk 0.5%, up to 12: 60 sessions +10.3% / -12.8% / 9.5 / 52%;  90 sessions +8.0% / -14.8% / 9.8 / 50%  (growth -2.3 points against a luck band of 1.5; drawdown -2.1 points against 2.7; signals dropped because the stock was still held: 0 / 168) -> NOT ok
  -> NOT met
STEP: UNCLEAR
shown only - the 1481 trades the longer limit changed: average R +2.49 -> +2.77; ended by time 67%, trail 26%, gap_stop 3%, stop 3%; held 60 -> 85 sessions; at +3R or more 32% -> 36%
shown only - the average change by calendar year (R per matched trade): 2016:-0.14 2017:+0.04 2018:+0.11 2019:-0.07 2020:+0.40 2021:+0.18 2022:+0.02 2023:+0.18 2024:-0.00 2025:+0.07 2026:-0.00

== 3. 90 sessions -> 120 sessions: the trades still open at session 90 are kept longer ==
matched filled trades 4287 (119 calendar months), signals up to 2026-05-11; the longer limit changed 980 of them (23%), the ones the earlier limit had sold
left out of the comparison: 1 trades the earlier limit sold that the longer limit could not follow, because the data jumps more than 35% in one session inside the extra weeks (the replay treats that as a data fault)
check: of the 3307 trades the earlier limit did not sell, 0 have another R under the longer one (expected 0)
RULE 1 pays per trade: average change +0.037R (98% range -0.017R to +0.089R); needs at least +0.05R and the lower end above 0 -> NOT met
RULE 2 pays for the room it takes: the extra +0.0066R per extra session held against the earlier limit's own +0.0121R per session; difference -0.0055R (98% range -0.0144R to +0.0036R) -> NOT met
RULE 3 in each part of the history (average change per matched trade): 2016-19 -0.051R (1030 trades); 2020-22 +0.095R (1321 trades); 2023-26 +0.044R (1936 trades) -> NOT met
RULE 4 the accounts (same signals, one position per stock; growth a year / worst drawdown / stocks held / money in stocks):
  risk 1.0%, up to 8: 90 sessions +14.2% / -20.7% / 6.5 / 60%;  120 sessions +12.9% / -24.6% / 6.6 / 57%  (growth -1.3 points against a luck band of 2.7; drawdown -3.9 points against 4.9; signals dropped because the stock was still held: 112 / 136) -> ok
  risk 0.5%, up to 12: 90 sessions +8.0% / -14.8% / 9.9 / 50%;  120 sessions +7.6% / -21.3% / 10.0 / 48%  (growth -0.4 points against a luck band of 1.5; drawdown -6.4 points against 3.1; signals dropped because the stock was still held: 166 / 196) -> NOT ok
  -> NOT met
STEP: UNCLEAR
shown only - the 980 trades the longer limit changed: average R +3.49 -> +3.65; ended by time 64%, trail 31%, gap_stop 4%, stop 1%; held 90 -> 114 sessions; at +3R or more 45% -> 46%
shown only - the average change by calendar year (R per matched trade): 2016:-0.12 2017:+0.02 2018:-0.08 2019:-0.09 2020:+0.27 2021:+0.06 2022:-0.04 2023:+0.20 2024:+0.03 2025:-0.09 2026:-0.05

== 4. 120 sessions -> 180 sessions: the trades still open at session 120 are kept longer ==
matched filled trades 4156 (116 calendar months), signals up to 2026-02-16; the longer limit changed 591 of them (14%), the ones the earlier limit had sold
left out of the comparison: 0 trades the earlier limit sold that the longer limit could not follow, because the data jumps more than 35% in one session inside the extra weeks (the replay treats that as a data fault)
check: of the 3565 trades the earlier limit did not sell, 0 have another R under the longer one (expected 0)
RULE 1 pays per trade: average change +0.099R (98% range +0.024R to +0.183R); needs at least +0.05R and the lower end above 0 -> met
RULE 2 pays for the room it takes: the extra +0.0159R per extra session held against the earlier limit's own +0.0115R per session; difference +0.0045R (98% range -0.0059R to +0.0142R) -> NOT met
RULE 3 in each part of the history (average change per matched trade): 2016-19 +0.036R (1030 trades); 2020-22 +0.196R (1321 trades); 2023-26 +0.064R (1805 trades) -> met
RULE 4 the accounts (same signals, one position per stock; growth a year / worst drawdown / stocks held / money in stocks):
  risk 1.0%, up to 8: 120 sessions +11.7% / -24.6% / 6.5 / 57%;  180 sessions +6.3% / -29.0% / 6.5 / 55%  (growth -5.5 points against a luck band of 1.7; drawdown -4.5 points against 4.5; signals dropped because the stock was still held: 133 / 154) -> NOT ok
  risk 0.5%, up to 12: 120 sessions +6.7% / -21.3% / 9.8 / 48%;  180 sessions +6.1% / -17.7% / 9.8 / 46%  (growth -0.6 points against a luck band of 1.8; drawdown +3.6 points against 3.2; signals dropped because the stock was still held: 191 / 226) -> ok
  -> NOT met
STEP: UNCLEAR
shown only - the 591 trades the longer limit changed: average R +4.42 -> +5.11; ended by time 52%, trail 44%, gap_stop 4%, stop 0%; held 120 -> 164 sessions; at +3R or more 56% -> 59%
shown only - the average change by calendar year (R per matched trade): 2016:+0.05 2017:+0.14 2018:-0.07 2019:-0.04 2020:+0.60 2021:-0.01 2022:+0.03 2023:+0.28 2024:-0.12 2025:+0.01 2026:-0.06

== 5. Shown only (not judged): 180 sessions -> 250 sessions ==
matched trades 4029, changed 304: average change +0.054R (98% range +0.010R to +0.106R); extra +0.0144R per extra session against +0.0124R per session

== 6. The answer, by the rule fixed in advance ==
RESULT: UNCLEAR - the data does not show that a longer limit than 60 sessions pays (the first step did not meet all four rules). Nothing changes.

== Read this before relying on any of it ==
- One history of ten years, not a forecast; the rule was fixed before the numbers existed, but the idea came from looking at the same data.
- The replay's stocks are today's NIFTY 500: survivors. A longer hold gains more than a short one from that, because a stock that kept rising for a year or two is more likely to be in the index today than one that did not - the live record, one trade at a time, is the real test of a longer limit.
- The score is price-only; swing signals only; no taxes and no slippage beyond the 0.35%; the trades of the last (limit + 10) sessions are left out.
- A longer limit ties the same money up for longer: rule 2 and the accounts of rule 4 look at that, but a real account's other uses of the money are not in them.
```

**In plain words.** Letting the trades the 60-session limit sold run on to 90 sessions **did add something per trade**: +0.095R on every matched trade (98% range +0.028R to +0.166R), which is +0.28R on each of the 1,481 trades (34%) the longer limit changed, and above zero in each part of the history (2016-19 barely, +0.002R; 2020-22 +0.200R; 2023-26 +0.074R). But it **did not pay for the room it takes**. The changed trades were held 25 sessions longer on average (60 to 85), and a trade earns about +0.0121R for each session it is held anyway; the extra earned per extra session (+0.0111R) came out a little below that (difference -0.0009R, range -0.0105R to +0.0090R), so the honest reading is a wash. In the two accounts that can hold only a few stocks the result was mixed: the 8-stock account grew 1.1 points a year more (inside its luck band of 3.3 points) with a shallower worst drawdown (-20.7% against -22.7%), the 12-stock account 2.3 points a year less (outside its luck band of 1.5 points); 114 and 168 later signals were dropped because the stock was still held. Rules 1 and 3 were met, rules 2 and 4 were not, and the rule needed all four, so the first step is **unclear**, the chain stops there, and **nothing changes: the limit stays 60 sessions**. The later steps did no better (120 against 90: +0.037R per trade, range -0.017R to +0.089R; 180 against 120: +0.099R per trade, but again not for its room, and its 8-stock account grew 5.5 points a year less), and 250 against 180 is shown only (+0.054R per trade). "Unclear" is not "no": the data does not show that a longer limit pays, and it does not show that it costs; on a full account it is a wash.

**Seen in the tables, not part of the rule, so not relied on** (looked at after the run; no rule was changed because of it):
- *What the extra weeks did to the 1,481 trades the 90-session limit changed* (their results at the 60-session sale against at the end): 994 (67%) were sold again by the new limit at 90 sessions and gained +1.03R each (+2.44R to +3.46R); 389 (26%) were taken out by the trail between sessions 60 and 90 and gave back 1.28R each (+2.91R to +1.64R); 50 ended by a gap stop (-1.46R each) and 48 by the first stop (-0.81R each). Together +414R over the 4,340 matched trades.
- *Half up, half down, a few big winners:* 49% of the changed trades ended higher than at 60 sessions and 51% lower; the median change was -0.05R and the mean +0.28R. The 15 biggest gains (1% of the changed trades) add up to 149R of the 414R (36%), the 60 biggest to 374R (90%); leaving out the top and bottom 1% the mean change is +0.21R.
- *By year* (average change per matched trade, 90 against 60): 2016 -0.14R, 2017 +0.04R, 2018 +0.11R, 2019 -0.07R, 2020 +0.40R, 2021 +0.18R, 2022 +0.02R, 2023 +0.18R, 2024 0.00R, 2025 +0.07R, 2026 0.00R: above zero in 7 of 11 years, and 2020 alone (419 matched trades) supplies about 167R of the 414R (40%). Years are not independent, and this is no part of the rule.

**Run note.** The study was run once, on `backtest_swing_yahoo_2026-11-11_trades.csv`, written by the Backtest & Calibrate run of 11 Nov 2026 (backtest, swing, yahoo), with exactly the code of GitHub commit `42c9280`, which holds the rule and was pushed before that run existed (the files under `backtest/`, `scanner/`, `tests/`, `config.py` and `main.py` were identical to that commit when the study ran). Nothing in the printout was added or changed after the run and no second run was made. Before the run the file was checked for its structure only: it has the 28 new columns and `held`, the same 7,308 signals as the run of 10 Nov, and every outcome column of the shipped plan (status, buy and sale dates, exit reason, net, R, weekly marks) identical to that run's; four quality scores on the last signal day differ by 0.1 to 0.3 points, the same kind of latest-bar revision as in the run before. No result of a longer limit was looked at before the study ran. The numbers were then recalculated by two separate scripts that do not use the study's code. One takes rules 1 to 3 from the file alone, with its own matching and its own month-by-month resampling (another seed, 20,000 draws): the same matched trades (4,340 / 4,287 / 4,156 / 4,029) and changed trades (1,481 / 980 / 591 / 304) for the four steps, the same averages and rule-3 table, and ranges within 0.002R (within 0.0003R a session for rule 2). The other is a plain event loop written from the account rules, for the four account lines of the first step: growth, worst drawdown, stocks held, money in stocks and the dropped-signal counts are identical to the printout; its own 30 random orders give luck bands of 2.8 and 1.5 points of growth (printout 3.3 and 1.5), the same verdicts. The first step fails rule 2 on its own (the lower end of the range, -0.0105R a session, is below 0), so the answer does not depend on the accounts.

**Limits.** The replay's stocks are today's NIFTY 500: a stock that kept rising for a year or two is more likely to be in the index today than one that did not, which flatters a long hold more than a short one, so the +0.095R per trade is if anything an over-estimate of what the same rule would have earned in real time - and it still did not pay for its room. The idea came from the table of exits in this same data (section 1), so fixing the rule first makes the question honest but does not make the data independent of that first look. One history of ten years, and 2020 supplies 40% of the gain. The two accounts are the reference accounts of item 8: one with more places than signals, or another risk per trade, could come out differently, and their luck bands (about 3 and 1.5 points a year) are as large as the differences tested, so a gain of a point or two could not have been proven in either direction. The quality score is price-only; swing signals only (the BTST scan cannot be replayed on daily candles, and both scans share the plan after the purchase); no taxes and no slippage beyond the 0.35% round trip.

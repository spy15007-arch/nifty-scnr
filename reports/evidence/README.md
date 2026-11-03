# Evidence: what seven years of NSE data say about this rule

**Short version.** There is no sure shot, and this evidence does not contain one. Replayed over 2019-2026, the rule had a real edge that was **concentrated in 2019-2023**; **2024-2026 was about break-even after costs**. The scans are a ranked shortlist with a disciplined exit, not a promise. Treat the live scoreboard (`tracking/trades.csv`, shown in the dashboard) as the judge once it has 30+ closed trades.

All numbers below were produced by the production code itself (`scanner/footprint.py`, `scanner/pipeline.py`, `scanner/tradesim.py` via `backtest/engine.py`), so the back-test and the live scan follow the same rule.

## 1. Data and method

* **Data:** NSE end-of-day prices for about 1,500 stocks, 2019-2026, adjusted for splits and bonuses, with delivery quantity. Design period up to 2024-12-31; validation 2025-01 to 2026-10 (never used to choose anything).
* **Replay:** every day the rule is applied exactly as live - setup test, quality score from that day's data only (no look-ahead), buy-stop entries, 2.5 x ATR stop, 5 x ATR trailing stop, 60-session limit, same-session stop-first assumption, **0.35% round-trip cost**, a trade is dropped (VOID) if the price series jumps more than 35% overnight (split / demerger).
* **Unit of result:** closed trades after costs. A buy-stop that never triggered is neither a win nor a loss (36% of signals never triggered).

## 2. The headline (full history, with delivery %)

4,303 closed trades: **39% won, average +3.08% per trade after costs (+0.37R), profit factor 1.66, median trade -4.97%.** A typical loser lost about 1R (-1.04R median); a typical winner made about +1.9R; the best trades made +18R.

| Year | Closed | Win rate | Avg per trade (after costs) | Profit factor |
| :-- | --: | --: | --: | --: |
| 2019 | 138 | 48% | +3.77% | 2.01 |
| 2020 | 568 | 44% | +6.10% | 2.37 |
| 2021 | 660 | 39% | +4.80% | 1.99 |
| 2022 | 519 | 27% | **-1.68%** | 0.70 |
| 2023 | 789 | 53% | +8.68% | 3.85 |
| 2024 | 674 | 31% | +0.12% | 1.02 |
| 2025 | 553 | 35% | **-0.33%** | 0.93 |
| 2026 (to Oct) | 402 | 31% | +0.52% | 1.10 |

Grouped: **2019-2023** 2,674 trades, 42% won, +4.91% average, PF 2.14. **2024-2026** 1,629 trades, 33% won, +0.07% average, **PF 1.01**. In plain words: the average trade over the last three years paid for its costs and nothing more.

A second, independent replay on today's NIFTY-500 universe, last 600 sessions, price/volume factors only (what the system can compute without delivery data): 1,189 closed trades, 32% won, +0.19% average, PF 1.04 (2024 -2.2%, 2025 +1.0%, 2026 +1.5%).

## 3. The profits are lumpy

* About **1 trade in 8 reached +3R or more**; the top 10% of trades produced about 1.5x the whole net profit - the other 90% together lost money.
* Exits: 1,917 initial-stop exits, 614 trailing-stop exits, 1,472 time-limit exits (median hold about 46 calendar days), 265 gaps through the stop and 35 same-day stop-outs.
* Only 50 of 86 signal months had a positive average trade. Long losing runs are normal for this style: expect to lose on most trades and to go many weeks without a clear winner.

## 4. What did and did not matter

| Question | Finding |
| :-- | :-- |
| Does a **higher quality rank** mean a better result? | **No, not reliably.** Inside the top-half gate the buckets were not ordered (full history, avg/trade: 50-65 +3.10%, 65-80 +3.24%, 80-90 +2.20%, 90-100 +3.67%; 2024+: +0.2%, +1.0%, **-1.8%**, -0.4%). Quality is therefore used as a **filter** (top half), and the messages show the rank only. Letter grades were removed for this reason. |
| Does the **market regime** matter? | Yes. Healthy breadth (60%+ of stocks above the 50-day average): +3.48%/trade. Mixed: +2.62%. **Weak (under 40%): +0.20%, PF 1.04 - essentially no edge.** 2024+: healthy +0.5%, mixed -0.5%, weak -1.6%. That is why weak markets get **half size** and a warning. |
| **Fresh breakout** vs **about to break out**? | Fresh was better on the full history (+5.03% vs +2.90% per trade) but has far fewer trades (356 vs 3,947), and on the recent price-only sample it was not better (74 trades). Both are kept; within the same trend group fresh ones are listed first (strong uptrends lead everything, see section 7). |
| Does **delivery %** help? | A little, not consistently: +3.08% vs +2.93% average per trade over the full history (2024 +0.12% vs -0.60%, but 2026 +0.52% vs +1.53%). It remains one of the strongest cross-sectional predictors (see below), so it stays in the score. |
| Does a **price cap** help? | No (+3.05% with the old cap vs +3.08% without). The cap was removed. |
| **Fixed targets / partial profit-taking?** | They lowered expectancy: the money came from the minority of trades allowed to run. No fixed target is used; +1R/+2R/+3R are reference levels only. |
| **BTST (sell next day)?** | Selling the next day was about break-even after costs. The plan therefore holds with the trailing stop rather than selling next morning. |

## 5. Which traits predicted the next 20 days (all stocks, rank correlation in %, design / validation)

| Trait | Design | Validation | Used in the score as |
| :-- | --: | --: | :-- |
| Delivery % (20-day average) | +3.0 | +6.6 | + |
| Closeness to 52-week high | +2.9 | +5.2 | + |
| Rising 200-day average | +2.4 | +4.1 | + |
| 12-month return | +2.2 | +4.4 | + |
| Daily volatility (ATR %) | -1.8 | -4.9 | - |
| 5-day return (spike) | -1.1 | -0.9 | - |
| Stretch above the 21-day average | -0.9 | -1.4 | - |

Signs were stable between the design and validation periods, but the sizes are small (a few percent) - these traits tilt the odds, they do not decide any single trade. That is exactly what the back-test shows.

## 6. Limits of this evidence

* **Survivorship and selection:** the panel contains the stocks that still trade; names that were delisted or fell out of the data are missing, which flatters old years. The live universe (NIFTY 500) is narrower than the back-test panel.
* **Fills:** stops are assumed to fill at the stop (or the open after a gap). Fast markets, circuit limits and illiquid names will fill worse. The 0.35% cost is an average, not a guarantee.
* **Regimes change:** an edge that was strong in 2019-2023 faded after; nothing guarantees it returns, and nothing guarantees it will not fade further.
* **Intraday BTST fills** (buying near 2:45 PM; the run was moved from 2:00 PM on 2 Nov 2026 at the user's request, in the belief that more of the day having printed gives better BTST picks) cannot be replayed from end-of-day data; the live scoreboard is the only honest test of that part, and no result exists yet for either time.
* Taxes, brokerage tiers and slippage beyond the 0.35% are not modelled.

## 7. The "already spiked" and "pull-back" study (30 Oct 2026)

**Questions** (they come from the ⚠️ line on a card): does a stock that has *already spiked* in the 5 days before the signal do worse after costs? Does *waiting for a pull-back* beat buying at the **Buy at** price? And does a rising 200-day trend help?

**Method.** The same rule, replayed on Yahoo Finance daily bars (split-adjusted; 474 of today's 501 NIFTY-500 stocks; signals June 2016 to October 2026): **7,258 signals, 4,453 closed trades - 39% won, +2.98% per trade after costs (+0.38R), profit factor 1.67, median trade -4.81%**, in line with the 7-year panel above (+3.08%, PF 1.66). Every signal was also simulated with two "wait for a dip" orders (a buy-limit 1 ATR below the trigger, live 5 sessions; and 2 ATR below, live 10 sessions) with the same stop, trail and time limit. Every comparison is between groups of the same signals; the 95% ranges come from a bootstrap over calendar months (trades from the same weeks move together). "Per card" = the group's total result divided by its number of signals, a buy-stop that never triggered counting as 0. Full output: [spike_study_2026-10-30.md](spike_study_2026-10-30.md); to reproduce, run **Backtest & Calibrate** with `data_source = yahoo`, then `python -m backtest.spike_study reports/output/backtest_swing_yahoo_<date>_trades.csv`.

| Question | What the data say (after 0.35% costs) |
| :-- | :-- |
| Does an **already-spiked** setup do worse? | **No measurable difference.** Flagged by today's cut-offs (gain of at least 4% and 1 standard deviation; 12% of signals): **+2.30% per card vs +1.78%** for the rest, difference +0.52% (95% range -0.72% to +1.95%). With the old cut-offs (5% and 1.28): +0.26% (-1.31% to +2.04%). No cut-off from "z 0.5, 3%" to "z 2, 8%" separated worse trades, and the buckets by size of the spike are flat. The same holds in 2016-19, 2020-22 and 2023-26, and in weak markets. So stricter vs looser changes how often the line appears, not what you earn. |
| How are they different, then? | They **swing more**: the spread of results is wider (standard deviation 23% vs 17%; average ATR 3.4% vs 2.9%; 10th-percentile trade -11.5% vs -9.7%), the win rate a little lower (37% vs 39%) and the winners bigger. Same average, bigger swings - which is what the stop-distance sizing already handles. |
| Does **waiting for a pull-back** beat the buy-stop? | **No.** Per card, all signals: buy-stop **+1.86%**, 1-ATR dip order +1.79%, 2-ATR dip order +0.96% (clearly worse: -0.91%, range -1.53% to -0.28%). For flagged stocks: +2.37% vs +1.81% vs +1.07%, lower in every one of the three periods. The dip orders do fill more often (1-ATR: 81% vs 62%), but the trades they fill are worse (flagged stocks: +2.45% vs +3.52% per filled trade; 9.4% vs 12.2% reach +3R) - consistent with the stocks that run straight on, the big winners, being missed. |
| Does a **rising 200-day trend** help? | **Yes, and the stronger the better - the clearest result.** Where the 200-day average had risen **5% or more in 20 sessions** (20% of signals): **+3.95% per card vs +1.29%** for the rest, difference +2.66% (range +1.02% to +4.41%), +0.21R (the gain holds per unit of risk although these stocks are more volatile: ATR 3.8% vs 2.6%). Positive in all three periods (+3.1%, +2.8%, +2.3%), but weaker in 2022-26 (+1.5%, range includes 0), and not shown in weak markets (only 114 such signals). Below 5% the trend made little difference. For these stocks waiting for a dip was clearly worse (-1.56%, range -2.63% to -0.60%): buy them at the trigger. |

**What changed in the product** (30 Oct: nothing; 31 Oct 2026, at the user's request, after reading this study):
1. The cut-offs of the "already spiked" warning stay at the stricter values (no profit difference either way).
2. The warning now says **"buy in continuation, not on a dip"** instead of "buy on a pull-back or in continuation": the study found waiting for a dip did not beat the **Buy at** price (and was clearly worse for a 2-ATR dip and for strong uptrends), so the card no longer suggests it.
3. A stock whose 200-day average is up **5% or more in 20 sessions** is tagged **💪 strong uptrend** (first item of its "why" line; the footer explains the tag on days a card carries it) and **listed first** - before fresh-vs-primed and quality. Nothing is added to the list; the only effect on *which* stocks appear is when more qualify than a message can carry (15 swing, 8 BTST; the replay averaged 3.6 swing signals a day, so this is rare) - then the strong ones are kept first. For a strong stock the ⚠️ line no longer repeats "200-day trend rising" (the tag says it).
4. Every new pick's trend (`trend200`), run-up (`run_up`, `z_run_up`) and the two flags (`strong_trend`, `spiked`) are now recorded in `tracking/trades.csv` and the scan CSV. `reports/output/live_scoreboard.md` and the dashboard compare strong with other picks and spiked with not-spiked on **real closed trades**. Read them only once each group has **30+ closed trades** (100+ for a gap of a percent or two to mean anything); until then this section's replay is the only evidence, with the caveats below. If the strong group is not ahead after that, set `STRONG_TREND_200D_MIN = 9.99` in `config.py` to switch the tag and the ordering off.
5. **Earlier picks that are still strong** (1 Nov 2026, again at the user's request): a stock picked on an earlier day whose buy-stop order is still waiting, and that still passes today's tests and ranks above every one of today's new picks (or there is no new pick), is shown in a short reminder block above the new picks - the best 3 by the same order as the list. It is a reminder only: **nothing is picked or logged a second time**, because the replay counted one trade per stock per 15 sessions and re-issuing a stock would be a rule this study never tested. This study did not test "still stronger today than yesterday" either, so the only evidence behind that order is the 💪 result above and fresh-vs-about-to-break in section 4; quality inside the top half is not a ladder. A stock whose order expired unfilled is not shown again. Whether it should be (a re-issue after an expired order) is a rule change that would need its own replay and its own live tracking first.
6. **🔭 Building up near a breakout** (2 Nov 2026, at the user's request - "stocks building up strongly with momentum and near breakout"): the Swing message (best 5), `summary_swing.md`, `reports/output/building_up_swing.csv` and the dashboard list the strong stocks that sit **3%-6% below** their 50-day high - top-half quality, a 200-day average up 1%+ in 20 sessions, a strong 12-month return - and are not a pick, an earlier pick or on the list. It is **watch only**: no order, nothing logged, nothing scored. **This rule has no back-test.** The replay only ever traded stocks within 3% of their 50-day high (7,258 signals, 6,930 of them "about to break out"), so the 3%-6% band was never bought and no number below says what buying one earns. The "strong 12-month return" condition (at least 0.25 standard deviations above the average stock's) is a judgment call, added after a flat stock (+1% in 12 months) passed the other tests; it was not tested. What the replay does say about being *further below the high* ([gap_study_2026-10-30.md](gap_study_2026-10-30.md); reproduce with `python -m backtest.gap_study reports/output/backtest_swing_yahoo_<date>_trades.csv`):

   | About-to-break-out signals | Finished signals* | Order triggered | Per filled trade | Per card |
   | :-- | --: | --: | --: | --: |
   | nearer half (under 1.5% below the high) | 1,963 | 77% | +2.77% | +2.14% |
   | further half (1.5%-3% below) | 4,940 | 54% | +3.02% | +1.63% |
   | further minus nearer (95% range) | | | +0.25% (-0.84% to +1.26%) | -0.51% (-1.35% to +0.28%) |
   | further half, 💪 strong uptrend (200-day average up 5%+) | 859 | 69% | **+6.09%** | **+4.17%** |
   | further half, trend rising but not strong | 2,498 | 53% | +2.34% | +1.24% |
   | further half, trend not rising | 1,583 | 47% | +1.83% | +0.86% |

   \* Signals whose order either traded and closed or never triggered; the 27 about-to-break-out signals still open, pending or voided when the replay ended are left out.

   So being further below the high mostly changed **how often the buy order triggered** (a signal under 1% below the high triggered after a median of 1 day, one 2%-3% below after 3 days), not what a trade that did trigger earned (the two halves are not clearly different). Inside the further half the 💪 strong uptrends did clearly better than the not-rising ones: +4.26% per filled trade (range +1.12% to +7.44%) and +3.31% per card (+1.17% to +5.50%), positive in all three periods (+4.55%, +6.51%, +6.20% per filled trade). That cut was made after looking at the data, so its size is probably optimistic, and it describes stocks *within* 3% of the high, not 3%-6% below it. It is the reason the list asks for a rising 200-day trend and lists 💪 stocks first. How the list does is **not** measured live either - it is not scored - so the first test is simply whether the stocks on it later become picks and how those picks do in the scoreboard. A proper test needs a replay that also buys stocks 3%-6% below the high (a change to the replay's signal rule, to be run with **Backtest & Calibrate**, `data_source = yahoo`).
7. **Should the stop-loss follow the price more closely?** (3 Nov 2026, the user's question: *"shouldn't the stop-loss move up when the price has rallied?"* - on the day's list 15 of 17 bought stocks were still on their first stop, some up 5%-7%.) How the plan works: first stop 2.5 ATR below the buy price; trail = 5 ATR below the **highest close**, never lowered, updated after each completed session. The trail only passes the first stop once a close is about +1.1R to +1.3R above the buy price (roughly +5% to +15%, depending on the stock's volatility; the dashboard now shows the exact level), then it sits 11%-22% below the highest close. Earlier research had tested fixed targets and partial profit-taking (both lowered the average, section 4) but never a stop that follows the price more closely, so every signal of the 10-year Yahoo replay was re-run on the same entry and first stop under five other rules, fixed **before** the run (`STOP_VARIANTS` in `backtest/engine.py`; full output [stop_study_2026-11-03.md](stop_study_2026-11-03.md); reproduce with `python -m backtest.stop_study reports/output/backtest_swing_yahoo_<date>_trades.csv`):

   | Stop rule (same 7,223 finished signals, 4,452 filled trades) | Win rate | Per filled trade | **Per card** | Per card minus the shipped plan (95% range) |
   | :-- | --: | --: | --: | :-- |
   | **Shipped:** trail 5 ATR below the highest close | 39% | +2.98% | **+1.84%** | - |
   | Trail 4 ATR | 39% | +2.49% | +1.54% | -0.30% (-0.47% to -0.15%) |
   | Trail 3 ATR | 38% | +1.63% | +1.01% | **-0.83%** (-1.22% to -0.48%) |
   | Shipped + stop at the buy price once a close is +1R ("break-even") | 32% | +2.63% | +1.62% | -0.21% (-0.33% to -0.10%) |
   | ... and the stop at +1R once a close is +2R | 34% | +2.29% | +1.41% | -0.42% (-0.63% to -0.23%) |
   | Trail 7 ATR (looser) | 38% | +3.19% | +1.97% | +0.13% (+0.02% to +0.24%) |

   **Every tighter rule earned less per card, and the range excludes zero for all of them.** The direction is the same in 2016-19, 2020-22 and 2023-26 for each (the 3 ATR trail clearly so in all three: -0.49%, -0.93%, -0.95%; the 4 ATR trail is clear only in 2023-26 and the break-even step only from 2020 on, both small before that), in healthy markets (3 ATR -1.06%) and for 💪 strong uptrends (3 ATR -1.44%, the 4 ATR trail -0.55%); in weak markets (797 signals) the differences are small and not clear (-0.13% to -0.34%) - tighter stops did **not** clearly help there either. What the tighter rules do change is the ride: the typical trade loses much less (median -4.81% shipped, -3.16% with a 3 ATR trail, -1.08% with break-even, -0.85% with the lock) and the average loser is smaller (-7.25% shipped, -5.84% break-even) - but they cut off the runs: of the 1,424 trades that had a close at least +2R above the buy price, 38% went on to +3R or more under the shipped plan against 24% (3 ATR), 33% (4 ATR), 35% (break-even) and 30% (lock), and those trades averaged +21.1% shipped against +15.2%, +19.1%, +19.2% and +18.1%. Giving back a rally is real - **24% of the trades whose close reached +1R still ended below zero** under the shipped plan - but the stocks that dip and then run are the same stocks that pay for everything else, and a tighter stop cannot tell them apart.
   The only rule that did a little better was the looser 7 ATR trail (+0.13% per card, range +0.02% to +0.24%, i.e. barely clear of zero; clearly worse in weak markets, -0.21%). That is too small and too fragile - five rules were compared, so one "clear" mark by chance is not unlikely - to change a rule that now has a live record, so **the live stop is unchanged**. If the priority were a smoother ride rather than the highest average, the break-even step is the cheapest way to get it (-0.21% per card, about a ninth of the average profit, in exchange for a typical trade near zero instead of -4.8%); that is a choice, not an improvement in profit.
   *Limits:* the replay marks the stop at the daily close and assumes the stop is hit first when one bar could have hit both (the same pessimism for every rule); the universe is today's NIFTY 500 (survivors), so levels are flattered but the differences between rules on the same signals much less so; the score is the price-only one; BTST cannot be replayed; these five rules are not the only possible ones (a trail based on a swing low, a rule that tightens as a trade ages and a partial sale at a profit mark are untested here - the last was found to lower the average in the earlier research, section 4).

**Limits - read before relying on any of this.**
* *Survivors:* the universe is today's NIFTY 500; stocks that fell out of it or were delisted are missing, which flatters the levels (and could flatter spiked stocks that later collapsed). The average is also carried by a few strong years: without 2023 (+9.8%) it is +1.81%; 2018, 2022 and 2024 lost money.
* *Price-only score:* delivery % history is not available here, and Yahoo volume may not be split-adjusted.
* *Detectable size:* the 95% range for "spiked vs not" is about ±1.3% per card, so a real difference of a few tenths of a percent cannot be ruled out - only a large one can.
* *Many comparisons were looked at,* so a few "clear" marks are expected by chance. The comparison that decides the warning (flagged vs not flagged) was fixed in advance; the 5% trend line was chosen after seeing the data, so its size is probably optimistic. It agrees in direction with the independent 7-year panel (a rising 200-day average is one of the four strongest positive traits in section 5), which is why it is worth a live test rather than ignoring.
* *The dip orders are mechanical* (a fixed distance below the trigger). A trader who waits for a pull-back to a level that looks right may do better; this test cannot say.
* No portfolio limits, no taxes, no slippage beyond the 0.35%.

## 8. How to use this

1. Treat every scan as a **shortlist** and risk at most 1% of your capital per trade: shares = the money you are willing to lose ÷ (buy price − stop). The message gives no share count because it depends on the money you still have free. In a weak market use half size - or sit out. A pick that has **already spiked** in the last 5 days is flagged on its card (buy in continuation, not on a dip). In the 10-year replay (section 7) flagged picks did not do worse after costs and waiting for a dip did not beat the **Buy at** price; they do swing more, so keep sizing by the stop distance. Picks tagged **💪 strong uptrend** are listed first because they did better in that replay; treat that as a hint until the live scoreboard confirms it (30+ closed trades per group), and size them exactly like any other pick.
2. Never widen a stop or add to a loser; exit only through the stop as it trails up.
3. Expect about 6 trades in 10 to lose. Do not judge the system on 5 trades - judge it on **30+ closed trades** in the scoreboard, after costs.
4. If after 50+ closed trades the scoreboard is clearly negative (average below about -0.2R and profit factor under 1), cut the size or pause and re-run **Backtest & Calibrate** rather than hoping. For a lumpy system even 50 trades is a small sample - 100+ is much more reliable.

*Reproduce on your own data: the **Backtest & Calibrate** workflow (or `python main.py backtest`) replays the same code on the broker's history for today's universe. The research panel used for the full-history numbers is a large local dataset and is not stored in the repository.*

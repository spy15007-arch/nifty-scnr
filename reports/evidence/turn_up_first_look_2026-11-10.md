# Early turn-up lane: a first look on the saved candles

Source: `btst_history.csv.gz`. How to read it: see the top of `backtest/turn_up_study.py`.

```
A FIRST LOOK on the saved candles of the BTST watchlist (no quality gate; every one of these stocks was near its 50-day high on 8 Nov, which flatters buy-the-turn rules; nothing is decided on it).

                          signals   filled  still open    avg R     won  lost money   avg result
early turn-up lane            367      236          61    +0.15     36%         64%       +2.04%
shipped (fresh+primed)        754      352          69    +0.04     36%         64%       +1.00%
(filled = bought and sold; still open = bought, not sold yet; avg R and the rest are over the filled trades only.)

Matched episodes: of 367 early signals, the shipped rule listed the same stock within 60 calendar days after 266 (72%).
For those, the shipped buy price was +4.9% above the early one (median; mean +5.7%), 12 calendar days later (median). Only stocks that did get back near their high are in this line, so it is the cost of the later entry on the winners.

The same, in the two halves of the history (filled trades only):
  first half  early 122 trades, average +0.13R; shipped 176 trades, average -0.02R
  second half early 114 trades, average +0.16R; shipped 176 trades, average +0.11R

PAYTM: the early rule fired 3 times in the saved history:
  2025-10-06  CLOSED    +1.19R (+8.5%)
  2026-04-08  CLOSED    +0.79R (+9.1%)
  2026-11-03  OPEN      +0.44R (+3.5%)

NH: the early rule fired 5 times in the saved history:
  2025-11-17  UNFILLED  never bought or still open
  2025-12-31  CLOSED    -1.05R (-6.9%)
  2026-02-11  CLOSED    -1.04R (-8.4%)
  2026-09-18  UNFILLED  never bought or still open
  2026-11-02  OPEN      +0.20R (+1.5%)
```

**Run note.** The rule (T1-T5) and the test were pushed to GitHub in commit `b1cbe9f` before this was run. The table and the per-stock lists were printed by that commit's code; the lines on matched episodes and on the two halves of the history were added to the printout afterwards (same candles, same numbers in the table; no rule changed). Candles: 25 Jul 2025 to 9 Nov 2026, 150 stocks; the Sunday 8 Nov Diwali session is in as a short bar. Both lanes are simulated one stock at a time with the shipped trade plan (buy-stop just above the signal day's high or the 50-day high, stop 2.5 ATR, trail 5 ATR, 0.35% costs, 15-session cool-down) and without the quality gate. A trade marked OPEN is marked at 9 Nov's close and is not counted in the averages.

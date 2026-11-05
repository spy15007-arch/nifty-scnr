# Portfolio study: what an account that can hold only a few stocks would have earned

Source: `backtest_swing_yahoo_2026-11-04_trades.csv`. How to read it: see the top of `backtest/portfolio_study.py`.

```
Finished signals 7227 (4456 filled and sold, 2771 orders that never filled), 2016-06-08 to 2026-11-03 (10.4 years)

The account (fixed before the run): start 100; work down each day's signals in the scanner's order; buy an order only if a room is free (fewer than N stocks bought-or-waiting) and the money fits the account (no borrowing); risk R% of the account to the first stop (half in a weak market); trades run exactly as in the replay; marked to market every Friday.

== 1. If every signal were bought: what that would take ==
stocks open at once: average 60, median 55, 90th percentile 105, peak 176
money in them at the message's sizing (1% risk, half in a weak market): average 817% of the capital, 90th percentile 1483%
so the per-signal averages in the other studies describe an account that cannot exist; the rest of this page is about accounts that can.

== 2. Accounts that can hold only a few stocks (the scanner's order of preference) ==
account              stocks money in orders  of all  growth     worst     worst 12 months    100
                       held   stocks a year signals  a year  drawdown 12 months   below 0 became
risk 0.5%, up to 5      4.1      22%     50      7%   +3.4%    -13.2%     -8.1%       40%   1.4x
risk 0.5%, up to 8      6.5      35%     78     11%   +7.3%     -8.7%     -5.5%       27%   2.1x
risk 0.5%, up to 12     9.7      53%    115     16%   +9.9%    -12.8%     -7.2%       26%   2.7x
risk 0.5%, up to 20    13.6      68%    156     22%  +12.0%    -23.8%    -16.5%       28%   3.3x
risk 1.0%, up to 5      4.1      43%     50      7%   +6.3%    -24.7%    -15.4%       40%   1.9x
risk 1.0%, up to 8      6.3      62%     74     11%  +12.1%    -22.7%    -14.5%       29%   3.3x
risk 1.0%, up to 12     7.8      71%     88     13%   +8.7%    -29.2%    -22.1%       31%   2.4x
risk 1.0%, up to 20     8.5      73%     91     13%  +13.4%    -19.2%    -15.0%       26%   3.7x
stocks held = the average number bought at once (a buy order that is still waiting also takes a room); money in stocks = what they cost as a part of the account; 'worst 12 months' and 'below 0' look at every rolling 52-week window; growth is a compound yearly rate; worst drawdown is from the highest weekly value to the lowest after it.
where 'stocks held' stops growing as the cap rises (look at the 1% rows), the money limit - not the cap - is what holds the account back, and the different results of those rows are the luck of which signals came when.

== 3. Does the order of preference matter once capital is limited? (the same signals; random = 30 random orders) ==
account                    order        growth/yr  worst drawdown  orders/yr
risk 1.0%, up to 8         shipped         +12.1%          -22.7%         74
risk 1.0%, up to 8         quality         +11.8%          -27.4%         74
risk 1.0%, up to 8         random          +12.3%          -20.3%         73   (growth +9.0% to +15.4%, drawdown -24.5% to -17.3%, 10th to 90th percentile)
                           -> the shipped order beat 47% of the 30 random orders on growth and 27% of them had a deeper drawdown
risk 0.5%, up to 12        shipped          +9.9%          -12.8%        115
risk 0.5%, up to 12        quality          +8.2%          -17.5%        115
risk 0.5%, up to 12        random           +9.1%          -14.9%        114   (growth +7.4% to +10.6%, drawdown -17.3% to -11.8%, 10th to 90th percentile)
                           -> the shipped order beat 73% of the 30 random orders on growth and 73% of them had a deeper drawdown

== 4. Year by year (the shipped order; '*' = a part-year) ==
year            risk 1.0%, up to 8       risk 0.5%, up to 12
2016   *                     -1.3%                     +1.8%
2017                        +45.3%                    +24.9%
2018                         -5.8%                     -3.2%
2019                         +6.4%                     +4.3%
2020                        +21.0%                    +18.4%
2021                        +82.8%                    +39.4%
2022                         -7.8%                     -3.9%
2023                        +16.3%                    +26.1%
2024                         +2.3%                     +8.2%
2025                         -6.2%                     -1.0%
2026   *                     +0.7%                     -3.1%

== 5. How well do the accounts' trades do against the average signal? ==
all signals: average +0.38R per filled trade (4456 trades)
risk 1.0%, up to 8: 474 filled trades taken, average +0.35R; 4809 signals skipped because every room was taken, 1645 because the money did not fit
risk 0.5%, up to 12: 716 filled trades taken, average +0.35R; 5939 signals skipped because every room was taken, 96 because the money did not fit

== 6. Read this before relying on any of it ==
- One history, not a forecast: the year-by-year table shows how much of the growth came from a few years.
- Today's NIFTY 500 only (survivors), the price-only score, no taxes and no slippage beyond the 0.35% cost: the LEVELS are flattered; the comparison between accounts and between orders is less affected.
- Open trades are marked from weekly points, so a fall inside a week is not seen; the real drawdown can be deeper than shown.
- Rooms count bought stocks AND buy orders still waiting (38% of the orders never fill), which is cautious; sizes follow the account's realised value, not its marked value.
- Which of a day's signals an account takes changes its yearly growth by about the spread of the random orders in section 3: differences between two accounts that are smaller than that are noise.
```

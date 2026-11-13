# Why a weekday Swing scan takes 45 minutes, and what "Partial scan" means

*Written 12-13 Nov 2026, after the Swing scan of 12 Nov said "Partial scan: 450 of 501 stocks loaded". The numbers are from the fetch reports every scan commits (`reports/output/fetch_report_swing.json`, one per day in the git history).*

## What the last two weeks looked like

Every Swing scan asks for 501 stocks; the broker's list has 495 of them (the other six - EMBASSY, BIRET, STLTECH, BAGMANE, HFCL and a dummy symbol - are never found there, so they can never load and are not counted as missing).

| Run | Day | Calls | Refused | Time | Of the 495 listed, loaded | What happened |
| :-- | :-- | --: | --: | --: | --: | :-- |
| Fri 30 Oct | trading | 723 | 32% | 43 min | 495 | all loaded (the second pass recovered 13) |
| Sun 1 Nov | no trading | 495 | 0% | 11 min | 495 | all loaded |
| Mon 2 Nov | trading | 716 | 31% | 43 min | 495 | all loaded (17 recovered) |
| Tue 3 Nov | trading | 708 | 30% | 42 min | 495 | all loaded (17 recovered) |
| Wed 4 Nov | trading | 715 | 35% | 45 min | **464** | limit reached: 11 never tried, 20 refused for good; no note in the message |
| Thu 5 Nov | trading | 720 | 35% | 45 min | **471** | 24 refused for good, no time left for the second pass; no note |
| Fri 6 Nov | trading | 701 | 29% | 42 min | 495 | all loaded (13 recovered) |
| Mon 9 Nov | trading | 705 | 30% | 43 min | 495 | all loaded (13 recovered) |
| Tue 10 Nov | market shut | 522 | 5% | 17 min | 495 | all loaded |
| Wed 11 Nov | trading | 728 | 34% | 44 min | **478** | 17 refused for good, no time left for the second pass; no note |
| Thu 12 Nov | trading | 729 | 38% | 45 min | **450** | limit reached: 22 never tried, 23 refused for good; the note appeared (450 is just under 90% of 501) |

So on **4 of the 9 trading days** the scan missed 17 to 45 stocks, and on three of those it said nothing, because the message only warned when fewer than 90% had loaded.

## How to read it

* **It is not our speed.** On a weekday evening the scan makes about one call every 3.7 seconds (a quarter of a call a second; the broker's own limit is 3 a second), and a third of the calls were still refused, always in the same words: *"Access denied because of exceeding access rate"*. On the Sunday the scan called nearly three times as fast (three quarters of a call a second) and nothing was refused. The words say "rate", but they are not a sign that we call too fast.
* **It is random.** The refused stocks are different every day, and the numbers fit a retry 3 or 6 seconds later being refused as often as a first try: on 12 Nov 23 stocks were refused three times in a row, where 27 would be expected if every try were an independent coin with that day's refusal share (11 Nov: 17 against 20; 5 Nov: 24 against 20). So waiting does not make the broker say yes - it only costs time.
* **It follows trading days.** Nine of nine trading-day evenings had 29-38% refused; the Sunday had none and the market holiday (10 Nov) 5%. It looks like load on the broker's side (or on the shared GitHub machines that make the calls), not something this program does. That is a reading of the numbers, not something the broker has confirmed; threads on Angel One's own forum report the same message at tiny request rates, without an official answer.
* **The 45 minutes are mostly our own waiting.** The broker answers in under a second. The scan then waits 2.5 seconds before each call (two "rate" refusals among the last 20 calls put it at its slowest pace) and 3 s, then 6 s, before each retry. About 730 calls and roughly four fifths of the 45 minutes go to those waits (an estimate from the pacing rules and the call counts, which reproduce the real runs).
* **Open and waiting trades were not hurt.** The scan fetches the stocks of your open trades and last evening's watchlist first, and fetches any of them the first pass missed again afterwards: all 90 open or waiting trades were updated on 12 Nov. What a partial scan misses is stocks that might have shown a *new* signal that evening - and they get another look the next evening.

## What was changed (13 Nov 2026) - and what was not

* **The time limit is 75 minutes, not 45** (`main.FETCH_CAP_S`; the job itself is allowed 120, and a test keeps 25 minutes of room after the fetch). A normal weekday now takes about 42-49 minutes including the second pass, so the Swing message arrives at about 5 PM.
  A simulated broker that refuses a share of calls at random in the same words and takes 0.75 s to answer reproduces the real numbers (12 Nov: 729 calls, 279 refused, 2,706 s; the quiet 10 Nov: 522 calls, 27 refused, 1,047 s) and shows that 75 minutes gets at least 492 of the 495 listed stocks at 30%, 36% and 45% refused (45% takes about 56 minutes). At 65% refused the run still stops early and says the broker kept refusing (`tests/test_broker.py::WeekdayEvening`).
* **The message says what was missed and why**, from 5 stocks upward (before: only under 90% loaded): *"Partial scan: 450 of 501 stocks loaded - the broker kept refusing requests, so the 45-minute limit ran out and 45 stocks were not checked. The broker said: Access denied because of exceeding access rate"*. The six stocks the broker does not list are never counted.
* **The pacing was not changed.** Whether calling faster would bring more refusals cannot be told from these runs (every trading-day run sat at the slowest pace), the refusals look independent of our speed but that is not proof, and the account is not worth a gamble to save 20 minutes. A faster scan would not load more stocks, only finish earlier.

## If a shorter scan is ever wanted

The one clean way to find out is a small manual "probe" workflow: on a weekday evening, **after the Swing scan has finished** (never during a scan or any other job that uses the broker), it would request the same few hundred candles at three speeds in turn and write down the share refused at each. If the share is the same at the fast speed, the waits can be cut (a run of about 20 minutes); if it rises, nothing is lost. It is not built; say so if you want it.

## Where to look when a scan is short

`reports/output/fetch_report_swing.json` (`_btst.json` for the afternoon scan) holds, for the latest run: stocks asked for and loaded, seconds, `store.pace.calls` and `store.pace.refused`, `store.refused_n` (refused for good), `store.not_reached` (never tried), `store.not_found_n` (not listed at the broker), `store.recovered` (rescued by the second pass), `store.cooldowns` and the broker's most common replies. Older runs are in the git history of the same file.

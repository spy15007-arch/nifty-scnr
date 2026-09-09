# Backtest Report - EOD - 2026-09-09

**3943 total signals** (3019 resolved, 924 still open at horizon end)
**Overall hit rate: 20.9%** (target hit before stop)

## Hit rate by signal-agreement count
| Signals confirming | Count | Hit rate |
| :--- | :--- | :--- |
| 1/6 | 167 | 12.0% |
| 2/6 | 490 | 15.7% |
| 3/6 | 970 | 21.3% |
| 4/6 | 1003 | 23.7% |
| 5/6 | 333 | 22.5% |
| 6/6 | 56 | 25.0% |

## What happened to the unresolved trades?
- **924** trades hit neither target nor stop within the horizon
- Average max progress toward target: **55%** of the way there
- Average gain/loss at horizon end: **+1.1%**
- Got 80%+ of the way to target (target may be slightly too aggressive): **208**
- Went essentially nowhere, flat (-10% to +10%, setup wasn't genuinely predictive): **711**
- Drifted up meaningfully but still short of target: **5**
- Drifted down meaningfully without quite hitting stop (a warning sign): **0**

*If hit rate clearly rises with signal count, the grading system is working as intended. If unresolved trades mostly 'went nowhere flat', the entry signal itself isn't very predictive of an imminent move, even when it's not wrong about direction. If most 'got close to target', the target may simply be set too far out - a lower target_1 could convert many of these into real wins.*
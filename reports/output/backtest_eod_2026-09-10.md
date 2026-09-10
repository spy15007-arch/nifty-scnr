# Backtest Report - EOD - 2026-09-10

**3912 total signals** (3360 resolved, 552 still open at horizon end)
**Overall hit rate: 24.7%** (target hit before stop)

## Hit rate by signal-agreement count
| Signals confirming | Count | Hit rate |
| :--- | :--- | :--- |
| 1/6 | 183 | 16.9% |
| 2/6 | 542 | 19.2% |
| 3/6 | 1076 | 24.9% |
| 4/6 | 1129 | 28.1% |
| 5/6 | 368 | 24.7% |
| 6/6 | 62 | 29.0% |

## What happened to the unresolved trades?
- **552** trades hit neither target nor stop within the horizon
- Average max progress toward target: **62%** of the way there
- Average gain/loss at horizon end: **+1.3%**
- Got 80%+ of the way to target (target may be slightly too aggressive): **149**
- Went essentially nowhere, flat (-10% to +10%, setup wasn't genuinely predictive): **394**
- Drifted up meaningfully but still short of target: **9**
- Drifted down meaningfully without quite hitting stop (a warning sign): **0**

*If hit rate clearly rises with signal count, the grading system is working as intended. If unresolved trades mostly 'went nowhere flat', the entry signal itself isn't very predictive of an imminent move, even when it's not wrong about direction. If most 'got close to target', the target may simply be set too far out - a lower target_1 could convert many of these into real wins.*
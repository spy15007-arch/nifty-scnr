# Backtest Report - EOD - 2026-09-16

**3902 total signals** (3542 resolved, 360 still open at horizon end)
**Overall hit rate: 26.2%** (target hit before stop)

## Hit rate by signal-agreement count
| Signals confirming | Count | Hit rate |
| :--- | :--- | :--- |
| 1/6 | 189 | 18.5% |
| 2/6 | 562 | 20.1% |
| 3/6 | 1121 | 27.1% |
| 4/6 | 1198 | 29.4% |
| 5/6 | 408 | 25.7% |
| 6/6 | 64 | 29.7% |

## What happened to the unresolved trades?
- **360** trades hit neither target nor stop within the horizon
- Average max progress toward target: **66%** of the way there
- Average gain/loss at horizon end: **+0.9%**
- Got 80%+ of the way to target (target may be slightly too aggressive): **114**
- Went essentially nowhere, flat (-10% to +10%, setup wasn't genuinely predictive): **239**
- Drifted up meaningfully but still short of target: **7**
- Drifted down meaningfully without quite hitting stop (a warning sign): **0**

*If hit rate clearly rises with signal count, the grading system is working as intended. If unresolved trades mostly 'went nowhere flat', the entry signal itself isn't very predictive of an imminent move, even when it's not wrong about direction. If most 'got close to target', the target may simply be set too far out - a lower target_1 could convert many of these into real wins.*
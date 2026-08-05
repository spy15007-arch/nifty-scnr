"""
Reports. Three kinds:
  1. Daily scan report - what the scanner/AI flagged today, why, and
     exactly how/when to execute (entry/stop/5 targets + NSE timing)
  2. Portfolio report - current holdings, exposure, P&L
  3. Calibration report - did "high probability" picks actually work?
"""
from __future__ import annotations
import pandas as pd
from datetime import date
from pathlib import Path

from ai.explain import Recommendation


def _targets_line(targets: list[float], risk_rewards: list[float]) -> str:
    parts = [f"T{i+1} **{t}** (R:R {rr})" for i, (t, rr) in enumerate(zip(targets, risk_rewards))]
    return " | ".join(parts)


def daily_scan_report(recommendations: list[Recommendation], out_dir: str = "./reports/output") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = f"{out_dir}/scan_{date.today().isoformat()}.md"

    by_style = {"INTRADAY": [], "BTST": [], "SWING": []}
    for rec in recommendations:
        style = rec.execution.style.value if rec.execution else "SWING"
        by_style.setdefault(style, []).append(rec)

    lines = [f"# Scan Report — {date.today().isoformat()}", ""]
    lines.append(f"**{len(recommendations)} candidates** — "
                 f"{len(by_style.get('INTRADAY', []))} intraday, "
                 f"{len(by_style.get('BTST', []))} BTST, "
                 f"{len(by_style.get('SWING', []))} swing")
    lines.append("")

    for style_name in ["INTRADAY", "BTST", "SWING"]:
        group = sorted(by_style.get(style_name, []), key=lambda r: r.probability, reverse=True)
        if not group:
            continue
        lines.append(f"## {style_name}")
        for rec in group:
            lines.append(f"### {rec.symbol} — {rec.probability:.0%} breakout probability")
            if rec.levels:
                lv = rec.levels
                lines.append(f"**Trade plan:** buy trigger **{lv.entry_trigger}** | stop **{lv.stop_loss}**")
                lines.append(_targets_line(lv.targets, lv.risk_rewards))
                lines.append(f"*Basis: {lv.basis}*")
            if rec.execution:
                ex = rec.execution
                lines.append(f"**Timing (IST):** entry {ex.entry_window_ist} | exit {ex.exit_window_ist} | max hold: {ex.max_hold}")
                lines.append(f"*{ex.reasoning}*")
            lines.append("**Why:**")
            for reason in rec.top_reasons:
                lines.append(f"- {reason}")
            lines.append("**Caveats:**")
            for c in rec.caveats:
                lines.append(f"- {c}")
            lines.append("")

    Path(path).write_text("\n".join(lines))
    return path


def daily_options_report(plans: list, out_dir: str = "./reports/output") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = f"{out_dir}/options_{date.today().isoformat()}.md"

    by_index: dict[str, list] = {}
    for plan in plans:
        by_index.setdefault(plan.index, []).append(plan)

    lines = [f"# Index Options Report — {date.today().isoformat()}", "",
             f"**{len(plans)} setup(s) across {len(by_index)} index(es)**", ""]

    for index_symbol, index_plans in by_index.items():
        lines.append(f"## {index_symbol}")
        for plan in index_plans:
            lines.append(f"### {plan.direction}")
            lines.append(f"**Strike:** {plan.suggested_strike} ({plan.strike_type})")
            lines.append(f"**Spot plan:** entry **{plan.spot_entry}** | stop **{plan.spot_stop}**")
            lines.append(_targets_line(plan.spot_targets, plan.risk_rewards))
            lines.append(f"**Timing (IST):** entry {plan.execution.entry_window_ist} | exit {plan.execution.exit_window_ist}")
            lines.append(f"*{plan.execution.reasoning}*")
            lines.append("**Caveats:**")
            for c in plan.caveats:
                lines.append(f"- {c}")
            lines.append("")

    Path(path).write_text("\n".join(lines))
    return path


def portfolio_report(portfolio, out_dir: str = "./reports/output") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = f"{out_dir}/portfolio_{date.today().isoformat()}.md"
    summary = portfolio.summary()
    sector_exp = portfolio.sector_exposure()

    lines = [f"# Portfolio Report — {date.today().isoformat()}", ""]
    lines.append(f"- Equity: ${summary['equity']:,.2f}")
    lines.append(f"- Cash: ${summary['cash']:,.2f}")
    lines.append(f"- Open positions: {summary['n_positions']}")
    lines.append(f"- Unrealized P&L: ${summary['unrealized_pnl']:,.2f}")
    lines.append(f"- Realized P&L: ${summary['realized_pnl']:,.2f}")
    lines.append("")
    lines.append("## Sector Exposure")
    for sector, pct in sector_exp.items():
        lines.append(f"- {sector}: {pct:.1%}")

    Path(path).write_text("\n".join(lines))
    return path


def calibration_report(predictions: list[dict], out_dir: str = "./reports/output") -> str:
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path = f"{out_dir}/calibration_{date.today().isoformat()}.md"

    df = pd.DataFrame(predictions)
    df["bucket"] = pd.cut(df["predicted_prob"], bins=[0, 0.2, 0.4, 0.6, 0.8, 1.0])
    grouped = df.groupby("bucket", observed=True).agg(
        n=("actual_outcome", "count"),
        predicted_avg=("predicted_prob", "mean"),
        actual_rate=("actual_outcome", "mean"),
    )

    lines = [f"# Calibration Report — {date.today().isoformat()}", "",
             "| Bucket | N | Predicted Avg | Actual Hit Rate |",
             "|---|---|---|---|"]
    for bucket, row in grouped.iterrows():
        lines.append(f"| {bucket} | {int(row['n'])} | {row['predicted_avg']:.2f} | {row['actual_rate']:.2f} |")

    Path(path).write_text("\n".join(lines))
    return path

"""
Sends the scan summary to Telegram. Set up once:
  1. Message @BotFather on Telegram, /newbot, get a bot token
  2. Message your new bot anything, then visit
     https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat_id
  3. Store both as GitHub secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

Kept dependency-free (plain requests) so it doesn't add another SDK.
"""
from __future__ import annotations
import logging
from ai.explain import Recommendation

logger = logging.getLogger(__name__)


def format_message(recommendations: list[Recommendation], max_items: int = 25) -> str:
    if not recommendations:
        return "📊 Breakout scan complete — no candidates passed filters today."

    by_style = {"INTRADAY": [], "BTST": [], "SWING": []}
    for rec in recommendations:
        style = rec.execution.style.value if rec.execution else "SWING"
        by_style.setdefault(style, []).append(rec)

    lines = [f"📊 *Breakout Scan* — {len(recommendations)} candidates\n"]

    for style_name, emoji in [("INTRADAY", "⚡"), ("BTST", "🌙"), ("SWING", "📈")]:
        group = sorted(by_style.get(style_name, []), key=lambda r: r.probability, reverse=True)
        if not group:
            continue
        lines.append(f"{emoji} *{style_name}* ({len(group)})")
        for rec in group[:max_items]:
            reasons = ", ".join(rec.top_reasons[:2])
            entry_line = ""
            if rec.levels:
                lv = rec.levels
                entry_line = f"\nBuy > {lv.entry_trigger} | SL {lv.stop_loss} | T1 {lv.target_1} | T2 {lv.target_2} | T3 {lv.target_3}"
            timing_line = ""
            if rec.execution:
                timing_line = f"\n🕐 In: {rec.execution.entry_window_ist} | Out: {rec.execution.exit_window_ist}"
            lines.append(f"*{rec.symbol}* — {rec.probability:.0%}{entry_line}{timing_line}\n_{reasons}_")

    lines.append("\n⚠️ Probabilities, not guarantees — see full report for caveats.")
    return "\n\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    import requests

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured (missing bot token or chat id) - skipping notification")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    # Telegram caps message length at 4096 chars - split into chunks if needed
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

    ok = True
    for chunk in chunks:
        resp = requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}, timeout=15)
        if not resp.ok:
            logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
            ok = False
    return ok


def format_options_message(plans: list) -> str:
    if not plans:
        return "📈 Index options scan complete — no setups today."

    lines = [f"📈 *Index Options Scan* — {len(plans)} setups\n"]
    for plan in plans:
        lines.append(
            f"*{plan.index}* {plan.direction} — Strike {plan.suggested_strike} ({plan.strike_type})\n"
            f"Spot > {plan.spot_entry} | SL {plan.spot_stop} | "
            f"T1 {plan.spot_target_1} | T2 {plan.spot_target_2} | T3 {plan.spot_target_3}\n"
            f"🕐 {plan.execution.entry_window_ist}"
        )
    lines.append("\n⚠️ Spot-level plan only — check live option premium before entering.")
    return "\n\n".join(lines)


def notify_option_results(plans: list, bot_token: str, chat_id: str):
    message = format_options_message(plans)
    send_telegram_message(bot_token, chat_id, message)


def notify_scan_results(recommendations: list[Recommendation], bot_token: str, chat_id: str):
    message = format_message(recommendations)
    send_telegram_message(bot_token, chat_id, message)

"""
Sends the scan summary to Telegram using parse-safe HTML entities.
Set up once:
  1. Message @BotFather on Telegram, /newbot, get a bot token
  2. Message your new bot anything, then visit
     https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat_id
  3. Store both as GitHub secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""
from __future__ import annotations
import logging
import html
from ai.explain import Recommendation

logger = logging.getLogger(__name__)


def _targets_compact(targets: list[float]) -> str:
    return " | ".join(f"T{i+1} {t}" for i, t in enumerate(targets))


def format_message(recommendations: list[Recommendation], max_items: int = 25) -> str:
    if not recommendations:
        return "📊 <b>Breakout scan complete</b> — no candidates passed filters today."

    by_style = {"INTRADAY": [], "BTST": [], "SWING": []}
    for rec in recommendations:
        style = rec.execution.style.value if rec.execution else "SWING"
        by_style.setdefault(style, []).append(rec)

    # Replaced loose Markdown notation syntax with strict, safe HTML text formatting blocks
    lines = [f"📊 <b>Breakout Scan</b> — {len(recommendations)} candidates\n"]

    for style_name, emoji in [("INTRADAY", "⚡"), ("BTST", "🌙"), ("SWING", "📈")]:
        group = sorted(by_style.get(style_name, []), key=lambda r: r.probability, reverse=True)
        if not group:
            continue
        lines.append(f"{emoji} <b>{style_name}</b> ({len(group)})")
        for rec in group[:max_items]:
            reasons = html.escape(", ".join(rec.top_reasons[:2]))
            symbol_clean = html.escape(rec.symbol)
            entry_line = ""
            if rec.levels:
                lv = rec.levels
                # HTML entities dynamically insulate special formatting tags like '>' from crashing Telegram
                entry_line = f"\nBuy &gt; {lv.entry_trigger} | SL {lv.stop_loss}\n{_targets_compact(lv.targets)}"
            timing_line = ""
            if rec.execution:
                timing_line = f"\n🕐 In: {rec.execution.entry_window_ist} | Out: {rec.execution.exit_window_ist}"
            lines.append(f"<b>{symbol_clean}</b> — {rec.probability:.0%}{entry_line}{timing_line}\n<i>{reasons}</i>\n")

    lines.append("\n⚠️ <i>Probabilities, not guarantees — see full report for caveats.</i>")
    return "\n\n".join(lines)


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    import requests

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured (missing bot token or chat id) - skipping notification")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    # Chunk messages tightly under the boundary mark to avoid parsing failures
    max_len = 4000
    chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)] or [text]

    ok = True
    for chunk in chunks:
        payload = {
            "chat_id": chat_id, 
            "text": chunk, 
            "parse_mode": "HTML", # Swapped out volatile Markdown processing structure
            "disable_web_page_preview": True
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                logger.error(f"Telegram send failed: {resp.status_code} {resp.text}")
                ok = False
        except Exception as e:
            logger.error(f"Connection exception hitting Telegram gateways: {str(e)}")
            ok = False
    return ok


def format_options_message(plans: list) -> str:
    if not plans:
        return "📈 <b>Index options scan complete</b> — no setups today."

    by_index: dict[str, list] = {}
    for plan in plans:
        by_index.setdefault(plan.index, []).append(plan)

    lines = [f"📈 <b>Index Options Scan</b> — {len(plans)} setup(s)\n"]
    for index_symbol, index_plans in by_index.items():
        lines.append(f"<b>{html.escape(index_symbol)}</b>")
        for plan in index_plans:
            emoji = "🟢" if "CE" in plan.direction else "🔴"
            dir_clean = html.escape(plan.direction)
            strike_clean = html.escape(str(plan.suggested_strike))
            type_clean = html.escape(str(plan.strike_type))
            
            lines.append(
                f"{emoji} {dir_clean} — Strike {strike_clean} ({type_clean})\n"
                f"Spot {plan.spot_entry} | SL {plan.spot_stop}\n"
                f"{_targets_compact(plan.spot_targets)}\n"
                f"🕐 {plan.execution.entry_window_ist}"
            )
    lines.append("\n⚠️ <i>Spot-level plan only — check live option premium before entering.</i>")
    return "\n\n".join(lines)


def notify_option_results(plans: list, bot_token: str, chat_id: str):
    message = format_options_message(plans)
    send_telegram_message(bot_token, chat_id, message)


def notify_scan_results(recommendations: list[Recommendation], bot_token: str, chat_id: str):
    message = format_message(recommendations)
    send_telegram_message(bot_token, chat_id, message)

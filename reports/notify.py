"""
Telegram messages (HTML parse mode; every dynamic piece is escaped).

The scan message is short on purpose. For each pick it says only what is needed to act - where to buy, where the stop is,
how far the stop trails - plus a few words on why. It never states a share count or an amount of money to invest: that
depends on the money the reader still has free, which no scan can know (a fixed-capital assumption gave wrong numbers as
soon as other stocks had been bought). The standing rules are said once, in the last lines. Everything longer lives in the
dashboard and in the Markdown files the scan commits.

SECURITY: the bot token is part of Telegram's URL, so any exception text from
the HTTP library could contain it. Everything that is logged here is passed
through `scrub()` first.
"""
from __future__ import annotations

import html
import logging
import math
import os
from datetime import datetime

import config
from scanner import session
from scanner.pipeline import fmt_price

logger = logging.getLogger(__name__)

STAGE_EMOJI = {"fresh": "🔥", "primed": "👀"}
MAX_CHUNK = 3900                    # Telegram's hard limit is 4096 characters per message
MAX_STOPS_SHOWN = 24                # open trades listed with their current stop
MAX_WAITING_SHOWN = 16              # buy-stops still waiting (names only)
MAX_EVENTS_SHOWN = 12


# --------------------------------------------------------------------------- #
def scrub(text: str, *secrets: str) -> str:
    """Replace any secret value found in `text` with ***."""
    out = str(text)
    for s in secrets:
        if s and len(s) >= 6:
            out = out.replace(s, "***")
    return out


def _e(x) -> str:
    return html.escape(str(x), quote=False)


def _pct(x: float, signed: bool = False) -> str:
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _rows(items: list[str], per_row: int = 2) -> list[str]:
    """Short items laid out `per_row` to a line (each line stays narrow enough for a phone screen)."""
    return [" · ".join(items[i:i + per_row]) for i in range(0, len(items), per_row)]


# --------------------------------------------------------------------------- #
def format_pick(p, idx: int) -> str:
    """
    One compact card of 4-6 short lines (no blank lines inside, so the message splitter keeps it whole):
    the setup, where to buy, the stop, a few words on why, and one line per specific warning (e.g. already spiked in 5 days).
    The buy line of a buy-stop pick just says "Buy at": the Stop line right under it is the stop-loss, and the section heading
    above the cards says these are trigger orders.
    """
    sector = f" · {_e(p.sector)}" if p.sector else ""
    lines = [f"<b>{idx}. {_e(p.symbol)}</b> {STAGE_EMOJI.get(p.setup, '')} {_e(p.stage_label)} · Quality {p.quality_pct:.0f}/100{sector}"]

    stats = ""
    if p.setup == "fresh":
        stats = f"up {_pct(p.gain_pct)}{' today' if p.entry_mode == 'close' else ''} on {p.vol_ratio:.1f}x volume"
    if p.entry_mode == "close":
        lines.append(f"Buy near <b>{fmt_price(p.entry_price)}</b>" + (f" · {stats}" if stats else ""))
        stats = ""                                                          # said on the buy line already
    else:
        lines.append(f"Buy at <b>{fmt_price(p.entry_price)}</b> · skip if it opens above {fmt_price(p.max_entry)}")
    lines.append(f"Stop <b>{fmt_price(p.stop)}</b> (-{_pct(p.risk_pct)}) · trail {_pct(p.trail_pct)}")

    context = ([stats] if stats else []) + [_e(w) for w in p.why]
    if context:
        lines.append(" · ".join(context[:4]))
    for warning in p.watch[:2]:
        lines.append("⚠️ " + _e(warning))
    return "\n".join(lines)


def format_tracked_block(events: list[dict], open_rows: list[dict], snapshot: bool = True, max_events: int = MAX_EVENTS_SHOWN) -> str:
    """
    What happened to trades already on the list, and (snapshot=True, the evening scan) where every open trade's stop is now.
    The afternoon scan leaves the snapshot out: the stops only change after a completed session, so it would repeat last night.
    """
    holding = [r for r in open_rows if r.get("status") == "OPEN"]
    waiting = [r for r in open_rows if r.get("status") == "PENDING"]
    raised = {ev.get("symbol"): ev.get("new") for ev in events if ev.get("kind") == "trail_raised"}
    in_snapshot = {r["symbol"] for r in holding} if snapshot else set()

    # a raised stop of an open trade is shown as an arrow in the stop list below, not as a line of its own
    evs = [ev for ev in events if not (ev.get("kind") == "trail_raised" and ev.get("symbol") in in_snapshot)]
    lines: list[str] = []
    for ev in evs[:max_events]:
        k = ev.get("kind")
        s = _e(ev.get("symbol", ""))
        if k == "filled":
            lines.append(f"• <b>{s}</b> - buy-stop filled at {fmt_price(ev.get('price'))}")
        elif k == "closed":
            why = {"stop": "stopped out", "trail": "trailing stop hit", "gap_stop": "gapped through the stop",
                   "same_day_stop": "bought and stopped out the same day", "time": "60-session limit reached"}.get(ev.get("reason"), "closed")
            r, net = ev.get("R"), ev.get("net")
            res = f" ({r:+.1f}R, {net:+.1%} after costs)" if r is not None and net is not None else ""
            lines.append(f"• <b>{s}</b> - {why} at {fmt_price(ev.get('price'))}{res}")
        elif k == "unfilled":
            lines.append(f"• <b>{s}</b> - buy-stop {'skipped, it opened too far above the trigger' if 'gap' in str(ev.get('reason')) else 'expired'}; no trade")
        elif k == "trail_raised":
            lines.append(f"• <b>{s}</b> - raise stop {fmt_price(ev.get('old'))} → <b>{fmt_price(ev.get('new'))}</b>")
        elif k == "void":
            lines.append(f"• <b>{s}</b> - price series jumped (split/demerger?), dropped from the record")
    if len(evs) > max_events:
        lines.append(f"• ...and {len(evs) - max_events} more update(s) in the dashboard")

    if snapshot and holding:
        up = any(r["symbol"] in raised for r in holding[:MAX_STOPS_SHOWN])
        items = [f"{_e(r['symbol'])} {fmt_price(r['trail'])}{'↑' if r['symbol'] in raised else ''}" for r in holding[:MAX_STOPS_SHOWN]]
        lines.append(f"<b>Open - current stops ({len(holding)})</b>" + (" · ↑ = raised today" if up else ""))
        lines.extend(_rows(items))
        if len(holding) > MAX_STOPS_SHOWN:
            lines.append(f"...and {len(holding) - MAX_STOPS_SHOWN} more in the dashboard")
    if snapshot and waiting:
        # names only: the trigger prices were in the message that issued each pick (and are in your broker's order list and the dashboard)
        names = ", ".join(_e(r["symbol"]) for r in waiting[:MAX_WAITING_SHOWN])
        more = f", +{len(waiting) - MAX_WAITING_SHOWN} more" if len(waiting) > MAX_WAITING_SHOWN else ""
        lines.append(f"<b>Buy-stops still waiting ({len(waiting)}):</b> {names}{more}")
    if not lines:
        return ""
    return "📒 <b>Your list</b>\n" + "\n".join(lines)


def _breadth_line(result) -> str:
    b50 = result.breadth.get("breadth50") if result.breadth else None
    if not (isinstance(b50, (int, float)) and math.isfinite(b50)):
        return ""
    verdict = {"weak": "<b>weak market: half size</b>", "healthy": "healthy market"}.get(result.regime, "mixed market")
    return f"📊 Breadth {b50:.0%} (stocks above their 50-day average) - {verdict}"


def _no_pick_line(result) -> str:
    f = result.funnel or {}
    if f.get("loaded", 0) == 0:
        return "No price data was loaded this run - check the broker connection in the run log."
    setups = f.get("fresh", 0) + f.get("primed", 0)
    passed, repeats = f.get("passed_quality", 0), f.get("repeats", 0)
    tail = ""
    if repeats:
        tail = " - all already on your list" if repeats >= passed else f" - {repeats} of them already on your list"
    return (f"<b>No new pick.</b> {f.get('loaded', 0)} stocks checked, {setups} in a breakout setup, "
            f"{passed} passed the quality filter{tail}.")


def format_scan_message(result, scan_mode: str, now: datetime | None = None, market_line: str = "",
                        tracked_block: str = "", scoreboard_line: str = "", notes: list[str] | None = None) -> str:
    """The whole message as a string of blocks separated by blank lines."""
    now = now or session.now_ist()
    t = now.astimezone(session.IST)
    title = {"btst": "🌙 <b>BTST scan</b>", "swing": "📈 <b>Swing scan</b>"}.get(scan_mode, f"<b>{_e(scan_mode)} scan</b>")
    if result.live:
        when, basis = t.strftime("%a %d %b") + ", " + t.strftime("%I:%M %p").lstrip("0"), "live prices"
    else:
        when = t.strftime("%a %d %b")
        if not result.ref_date:
            basis = "no price data"
        elif result.ref_date == t.date():
            basis = "today's close"
        else:
            basis = f"close of {result.ref_date.strftime('%a %d %b')}"
    head = [f"{title} · {when} · {basis}"]
    if market_line:
        head.append(market_line)                 # already Telegram-HTML, built and escaped upstream
    breadth = _breadth_line(result)
    if breadth:
        head.append(breadth)
    blocks = ["\n".join(head)]

    notice = []
    if not result.used_delivery:
        notice.append("No delivery-% data today - stocks were ranked on price and volume only.")
    notice += list(notes or []) + list(result.notes)
    if notice:
        blocks.append("\n".join(f"ℹ️ {_e(n)}" for n in notice))

    picks = result.picks
    buy_now = [p for p in picks if p.entry_mode == "close"]
    stops = [p for p in picks if p.entry_mode != "close"]
    n = 0
    if buy_now:
        blocks.append("<b>BUY NOW</b> (near the close, only if it still holds the breakout level)")
        for p in buy_now:
            n += 1
            blocks.append(format_pick(p, n))
    if stops:
        blocks.append(f"<b>BUY-STOP ORDERS</b> (GTT / stop-limit, valid {stops[0].valid_sessions} sessions)")
        for p in stops:
            n += 1
            blocks.append(format_pick(p, n))
    if not picks:
        blocks.append(_no_pick_line(result))
    elif result.repeats:
        blocks.append(f"+{len(result.repeats)} more qualify but are already on your list.")

    if tracked_block:
        blocks.append(tracked_block)

    footer = (f"ℹ️ <i>Trail = keep the stop that % below the highest close; only raise it, there is no fixed target. "
              f"Risk at most {config.RISK_PER_TRADE_PCT:g}% of your capital per trade, half in a weak market. "
              f"A ranked shortlist, not a guarantee.</i>")
    if scoreboard_line:
        footer = f"🧾 <i>{_e(scoreboard_line)}</i>\n" + footer
    blocks.append(footer)
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
def split_message(text: str, max_len: int = MAX_CHUNK) -> list[str]:
    """Split on blank lines into chunks <= max_len; an oversized block is split on single line breaks."""
    pieces: list[str] = []
    for block in text.split("\n\n"):
        if len(block) <= max_len:
            pieces.append(block)
            continue
        cur = ""
        for line in block.split("\n"):
            while len(line) > max_len:                 # a single enormous line: hard cut
                if cur:
                    pieces.append(cur)
                    cur = ""
                pieces.append(line[:max_len])
                line = line[max_len:]
            cand = f"{cur}\n{line}" if cur else line
            if len(cand) > max_len and cur:
                pieces.append(cur)
                cur = line
            else:
                cur = cand
        if cur:
            pieces.append(cur)

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n\n{piece}" if current else piece
        if len(candidate) > max_len and current:
            chunks.append(current)
            current = piece
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> bool:
    import requests

    if not bot_token or not chat_id:
        logger.warning("Telegram not configured (missing bot token or chat id) - skipping notification")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    ok = True
    for chunk in split_message(text):
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True}
        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                logger.error(scrub(f"Telegram send failed: {resp.status_code} {resp.text}", bot_token))
                ok = False
        except Exception as e:  # noqa: BLE001 - the library's message can contain the URL, which contains the token
            logger.error(scrub(f"Connection exception hitting Telegram gateways: {type(e).__name__}: {e}", bot_token))
            ok = False
    return ok


def notify_scan(result, scan_mode: str, bot_token: str, chat_id: str, now: datetime | None = None,
                market_line: str = "", tracked_block: str = "", scoreboard_line: str = "",
                notes: list[str] | None = None, save_to: str | None = None) -> bool:
    """Build the scan message and send it. `save_to`: also keep the exact text that was sent (the run log is not always reachable)."""
    try:
        message = format_scan_message(result, scan_mode, now=now, market_line=market_line,
                                      tracked_block=tracked_block, scoreboard_line=scoreboard_line, notes=notes)
    except Exception:  # noqa: BLE001 - a formatting slip must never cost the whole message
        logger.exception("Could not format the scan message")
        message = _e(f"The {scan_mode.upper()} scan finished, but its message could not be formatted. "
                     f"The picks are in summary_{scan_mode}.md in the repository.")
    if save_to:
        try:
            os.makedirs(os.path.dirname(save_to) or ".", exist_ok=True)
            with open(save_to, "w", encoding="utf-8") as fh:
                fh.write(message + "\n")
        except OSError:
            logger.warning("Could not save a copy of the message", exc_info=True)
    return send_telegram_message(bot_token, chat_id, message)


def notify_text(bot_token: str, chat_id: str, text: str) -> bool:
    """A short plain notice (e.g. a failed run), HTML-escaped."""
    return send_telegram_message(bot_token, chat_id, _e(text))


# --------------------------------------------------------------------------- #
# Index options (manual command only; unchanged)
def _targets_compact(targets: list[float]) -> str:
    return " | ".join(f"T{i+1} {t}" for i, t in enumerate(targets))


def format_options_message(plans: list) -> str:
    if not plans:
        return "📈 <b>Index options scan complete</b> — no setups today."

    by_index: dict[str, list] = {}
    for plan in plans:
        by_index.setdefault(plan.index, []).append(plan)

    lines = [f"📈 <b>Index Options Scan</b> — {len(plans)} setup(s)\n"]
    for index_symbol, index_plans in by_index.items():
        lines.append(f"<b>{html.escape(index_symbol)}</b>")
        for idx, plan in enumerate(index_plans, 1):
            emoji = "🟢" if "CE" in plan.direction else "🔴"
            dir_clean = html.escape(plan.direction)
            strike_clean = html.escape(str(plan.suggested_strike))
            type_clean = html.escape(str(plan.strike_type))

            lines.append(
                f"{emoji} <b>{idx}.</b> {dir_clean} — Strike {strike_clean} ({type_clean})\n"
                f"Spot {plan.spot_entry} | SL {plan.spot_stop}\n"
                f"{_targets_compact(plan.spot_targets)}\n"
                f"🕐 {plan.execution.entry_window_ist}"
            )
    lines.append("\n⚠️ <i>Spot-level plan only — check live option premium before entering.</i>")
    return "\n\n".join(lines)


def notify_option_results(plans: list, bot_token: str, chat_id: str, diagnostic: str = ""):
    message = format_options_message(plans)
    if diagnostic:
        message += f"\n\n🔎 <i>{html.escape(diagnostic)}</i>"
    send_telegram_message(bot_token, chat_id, message)

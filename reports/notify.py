"""
Telegram messages (HTML parse mode; every dynamic piece is escaped).

The scan message is built from `scanner.pipeline.Pick` objects: for each pick
it says what to buy, where the stop is, how the stop trails, and how many
shares fit a 1% risk - the whole plan, not just a ticker.

SECURITY: the bot token is part of Telegram's URL, so any exception text from
the HTTP library could contain it. Everything that is logged here is passed
through `scrub()` first.
"""
from __future__ import annotations

import html
import logging
import math
from datetime import datetime

import config
from scanner import session
from scanner.pipeline import fmt_price

logger = logging.getLogger(__name__)

STAGE_EMOJI = {"fresh": "🔥", "primed": "👀"}
MAX_CHUNK = 3900                    # Telegram's hard limit is 4096 characters per message


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


def _inr(x: float) -> str:
    """Rupee amount in Indian style (lakh / crore)."""
    if x >= 1e7:
        return f"₹{x / 1e7:.2f} crore"
    if x >= 1e5:
        return f"₹{x / 1e5:.1f} lakh"
    return f"₹{x:,.0f}"


def _pct(x: float, signed: bool = False) -> str:
    return f"{x:+.1%}" if signed else f"{x:.1%}"


# --------------------------------------------------------------------------- #
def format_pick(p, idx: int) -> str:
    """One compact card (no blank lines inside, so the message splitter keeps it whole)."""
    sector = f" · {_e(p.sector)}" if p.sector else ""
    lines = [f"<b>{idx}. {_e(p.symbol)}</b> {STAGE_EMOJI.get(p.setup, '')} {_e(p.stage_label)} · Quality {p.quality_pct:.0f}/100{sector}"]

    if p.entry_mode == "close":
        lines.append(f"💰 <b>Buy near ₹{fmt_price(p.entry_price)}</b> - up {_pct(p.gain_pct)} today on {p.vol_ratio:.1f}x volume, "
                     f"{_pct(p.ext_pct, True)} vs its 50-day high ₹{fmt_price(p.pivot)}")
    else:
        lines.append(f"💰 Buy-stop <b>₹{fmt_price(p.entry_price)}</b> (valid {p.valid_sessions} sessions; skip it if it opens above ₹{fmt_price(p.max_entry)})")
    lines.append(f"🛑 Stop <b>₹{fmt_price(p.stop)}</b> (-{_pct(p.risk_pct)}) → trail {_pct(p.trail_pct)} below the highest close, raise only, never lower")

    if p.qty > 0:
        risk_amt = p.qty * p.entry_price * p.risk_pct
        unit = "share" if p.qty == 1 else "shares"
        size = f"📐 {p.qty} {unit} (₹{p.position_value:,.0f}) risks ≈₹{risk_amt:,.0f} = {risk_amt / config.CAPITAL_INR:.1%} of {_inr(config.CAPITAL_INR)}"
    else:
        size = "📐 One share is above the position cap for this capital - skip it or size by hand"
    if p.size_note:
        size += f" ({_e(p.size_note)})"
    size += f" · reference levels +1R ₹{fmt_price(p.r1)} / +2R ₹{fmt_price(p.r2)} / +3R ₹{fmt_price(p.r3)}, no fixed target"
    lines.append(size)

    # the stage label already says "about to break out / at the breakout level", so its repeat is dropped
    reasons = p.reasons[1:] if p.setup == "primed" else p.reasons
    if reasons:
        lines.append("✅ " + _e("; ".join(reasons[:4])))
    cautions = [c for c in p.cautions if not c.startswith("Weak market")]       # the header already says it
    if cautions:
        lines.append("⚠️ " + _e("; ".join(cautions[:3])))
    return "\n".join(lines)


def format_tracked_block(events: list[dict], open_rows: list[dict], max_events: int = 12) -> str:
    """What happened to trades already on the list, and the stop levels to keep updated."""
    lines: list[str] = []
    for ev in events[:max_events]:
        s = _e(ev.get("symbol", ""))
        k = ev.get("kind")
        if k == "filled":
            lines.append(f"• <b>{s}</b> - buy-stop filled at ₹{fmt_price(ev.get('price'))}")
        elif k == "closed":
            why = {"stop": "stopped out", "trail": "trailing stop hit", "gap_stop": "gapped through the stop",
                   "same_day_stop": "filled and stopped out the same day", "time": "60-session time limit"}.get(ev.get("reason"), "closed")
            r, net = ev.get("R"), ev.get("net")
            res = f" ({r:+.1f}R, {net:+.1%} after costs)" if r is not None and net is not None else ""
            lines.append(f"• <b>{s}</b> - {why} at ₹{fmt_price(ev.get('price'))}{res}")
        elif k == "unfilled":
            lines.append(f"• <b>{s}</b> - buy-stop never triggered ({'gapped too far' if 'gap' in str(ev.get('reason')) else 'expired'}); no trade")
        elif k == "trail_raised":
            lines.append(f"• <b>{s}</b> - raise your stop ₹{fmt_price(ev.get('old'))} → <b>₹{fmt_price(ev.get('new'))}</b>")
        elif k == "void":
            lines.append(f"• <b>{s}</b> - price series jumped (split/demerger?) - trade dropped from the scoreboard")
    extra = len(events) - max_events
    if extra > 0:
        lines.append(f"• ...and {extra} more update(s) in the dashboard")

    holding = [r for r in open_rows if r.get("status") == "OPEN"]
    waiting = [r for r in open_rows if r.get("status") == "PENDING"]
    if holding:
        chunks = [f"{_e(r['symbol'])} ₹{fmt_price(r['trail'])}" for r in holding[:24]]
        rows = [" · ".join(chunks[i:i + 3]) for i in range(0, len(chunks), 3)]
        lines.append(f"<b>Open - current stops ({len(holding)}):</b>")
        lines.extend(rows)
        if len(holding) > 24:
            lines.append(f"...and {len(holding) - 24} more")
    if waiting:
        names = ", ".join(f"{_e(r['symbol'])} ₹{fmt_price(r['trigger'])}" for r in waiting[:12])
        lines.append(f"<b>Waiting for their buy-stop ({len(waiting)}):</b> {names}" + (" ..." if len(waiting) > 12 else ""))
    if not lines:
        return ""
    return "📒 <b>Trades already on your list</b>\n" + "\n".join(lines)


def format_scan_message(result, scan_mode: str, now: datetime | None = None, market_line: str = "",
                        tracked_block: str = "", scoreboard_line: str = "", notes: list[str] | None = None) -> str:
    """The whole message as a string of blocks separated by blank lines."""
    now = now or session.now_ist()
    t = now.astimezone(session.IST)
    title = {"btst": "🌙 <b>BTST scan</b>", "swing": "📈 <b>Swing scan</b>"}.get(scan_mode, f"<b>{_e(scan_mode)} scan</b>")
    clock = t.strftime("%I:%M %p").lstrip("0")
    when = t.strftime("%a %d %b") + (f", {clock} IST" if result.live else "")
    if result.live:
        basis = f"live prices at {clock} IST (today's volume projected to a full day)"
    elif result.ref_date:
        basis = f"based on the close of {result.ref_date.strftime('%a %d %b')}"
    else:
        basis = "no price data"
    blocks = [f"{title} - {when}\n<i>{_e(basis)}</i>"]
    if market_line:
        blocks.append(market_line)      # already Telegram-HTML, built and escaped upstream

    b50 = result.breadth.get("breadth50") if result.breadth else None
    if isinstance(b50, (int, float)) and math.isfinite(b50):
        if result.regime == "weak":
            blocks.append(f"📊 Breadth: only {b50:.0%} of liquid stocks are above their 50-day average - a <b>weak</b> market. "
                          f"Breakouts fail more often now: <b>half size</b>, be pickier.")
        elif result.regime == "healthy":
            blocks.append(f"📊 Breadth: {b50:.0%} of liquid stocks are above their 50-day average - a healthy market.")
        else:
            blocks.append(f"📊 Breadth: {b50:.0%} of liquid stocks are above their 50-day average - a mixed market.")
    if not result.used_delivery:
        blocks.append("ℹ️ Delivery-% data was not available, so stocks were ranked on the 7 price/volume factors only.")
    for n in (notes or []) + list(result.notes):
        blocks.append(f"ℹ️ {_e(n)}")

    picks = result.picks
    buy_now = [p for p in picks if p.entry_mode == "close"]
    stops = [p for p in picks if p.entry_mode != "close"]
    n = 0
    if buy_now:
        blocks.append("<b>BUY NOW - broke out today on volume</b> (buy near the close, only if it still holds the breakout level)")
        for p in buy_now:
            n += 1
            blocks.append(format_pick(p, n))
    if stops:
        head = ("<b>BUY-STOP ORDERS - fresh breakouts to buy on confirmation, and stocks about to break out</b>"
                if not buy_now else "<b>BUY-STOP ORDERS - about to break out</b>")
        blocks.append(head + "\nPlace a buy-stop (GTT / stop-limit) at the price shown; it only triggers if the stock really breaks out.")
        for p in stops:
            n += 1
            blocks.append(format_pick(p, n))
    if not picks:
        f = result.funnel
        why = (f"{f.get('loaded', 0)} stocks checked, {f.get('fresh', 0) + f.get('primed', 0)} in a breakout setup "
               f"({f.get('fresh', 0)} fresh, {f.get('primed', 0)} about to break), {f.get('passed_quality', 0)} also in the top half "
               f"by quality")
        if f.get("repeats"):
            why += f", {f['repeats']} already on your list"
        if f.get("loaded", 0) == 0:
            why = "No price data was loaded this run - check the broker connection in the run log."
        blocks.append("No new pick: every pick must be in a breakout setup AND in the top half of stocks by quality.\n"
                      f"<i>{_e(why)}</i>")
    if result.repeats:
        names = ", ".join(_e(s) for s, _ in result.repeats[:14])
        blocks.append(f"📋 Already on your list (not repeated): {names}" + (" ..." if len(result.repeats) > 14 else ""))

    if tracked_block:
        blocks.append(tracked_block)

    blocks.append("ℹ️ Quality = rank among today's liquid stocks (100 = best). It is a filter (top half only), not a probability and "
                  "not a ladder: a higher number did not mean a better result in the back-test. Risk at most 1% of capital per trade "
                  "(half in a weak market) and exit only through the stop, which trails up behind the price. Back-test 2019-2026: about "
                  "4 trades in 10 won and a few big winners paid for the many small stop-outs; 2019-23 was strong, 2024-26 was about "
                  "break-even after costs. A ranked shortlist, not a guarantee - judge it over 30+ trades.")
    if scoreboard_line:
        blocks.append(f"📒 <i>{_e(scoreboard_line)}</i>")
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
                notes: list[str] | None = None) -> bool:
    message = format_scan_message(result, scan_mode, now=now, market_line=market_line,
                                  tracked_block=tracked_block, scoreboard_line=scoreboard_line, notes=notes)
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

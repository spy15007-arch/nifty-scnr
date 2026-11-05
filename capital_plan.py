"""
The capital plan on the dashboard ("Your list" tab): how much of the list can ONE account hold?

WHY (4 Nov 2026): the list grows with every scan - 24 bought and 18 waiting stocks after two weeks - while the messages say "risk at most 1% of your
capital per trade, half in a weak market". At that size, 24 stocks would need about 166% of the capital (about 300% with the waiting ones), so
nobody can follow all of it, and nothing said which to leave out. The messages never state an amount of money (the scan cannot know what the reader
still has free), so this is a calculator the reader runs with their own numbers.

HOW: every trade that is bought-and-not-sold or waiting to be bought is sized with the messages' own rule - risk `risk_pct` of the capital from the buy
price to the FIRST stop (half of that when the market was weak on the pick day), so shares = money at risk / (buy price - first stop) - and listed in
the scanner's order of preference (the same order as the messages: 💪 strong uptrend, then 🔥 fresh breakouts before 👀 about-to-break stocks, then
quality). Working down that list, a stock is a "Take" while a room is free (fewer than `max_stocks` held or waiting) and its money still fits the capital
(no borrowing); stocks the reader says they already hold or have an order for come first and are counted as held.

HONESTY: the order of preference is a tidy way to choose, not a proven edge - in the 10-year replay of an account that can hold only a few stocks it was
not clearly better than a random choice (reports/evidence/portfolio_study_*.md). This file reads only the ledger columns the dashboard already reads
and imports nothing from the scanner, so the dashboard stays a plain viewer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEAK_HALF = 0.5          # the messages' rule: half size when the market's breadth was weak on the pick day
LOAD_CAPITAL = 10_000_000.0   # any capital will do for a share of the capital; a big one keeps whole-share rounding out of the percentage
HELD, TAKE, NO_ROOM, NO_MONEY = "Held", "Take", "Skip - every room is taken", "Skip - the money does not fit"


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return pd.to_numeric(df[name], errors="coerce") if name in df.columns else pd.Series(np.nan, index=df.index)


def listed(ledger: pd.DataFrame | None) -> int:
    """How many trades are bought-and-not-sold or waiting to be bought (the ones the plan is about)."""
    if ledger is None or ledger.empty or "status" not in ledger.columns:
        return 0
    return int(ledger["status"].isin(["OPEN", "PENDING"]).sum())


def sized_list(ledger: pd.DataFrame | None, capital: float, risk_pct: float) -> pd.DataFrame:
    """
    Bought-and-not-sold and waiting trades with their size, best first. Columns: symbol, scan_mode, status ('OPEN' = bought, 'PENDING' = waiting),
    setup, strong, quality, buy, stop, stop_pct (the distance to the first stop as a share of the price), risk_money, shares, money, share_of_capital,
    risk_share. A trade without the numbers needed to size it (price, ATR), or one whose single share would already risk more than the budget, is left
    out (plan() counts them).
    """
    cols = ["symbol", "scan_mode", "status", "setup", "strong", "quality", "buy", "stop", "stop_pct", "risk_money", "shares", "money",
            "share_of_capital", "risk_share"]
    if ledger is None or ledger.empty or capital <= 0 or risk_pct <= 0 or "status" not in ledger.columns:
        return pd.DataFrame(columns=cols)
    df = ledger[ledger["status"].isin(["OPEN", "PENDING"])].copy()
    if df.empty:
        return pd.DataFrame(columns=cols)
    is_open = df["status"] == "OPEN"
    buy = _col(df, "fill_px").where(is_open, _col(df, "trigger"))
    atr, mult = _col(df, "atr_pct"), _col(df, "stop_mult").fillna(2.5)
    stop_pct = mult * atr
    weak = (df["regime"] == "weak") if "regime" in df.columns else pd.Series(False, index=df.index)
    risk_share = (risk_pct / 100.0) * np.where(weak, WEAK_HALF, 1.0)
    out = pd.DataFrame({
        "symbol": df["symbol"], "scan_mode": df["scan_mode"] if "scan_mode" in df.columns else "", "status": df["status"],
        "setup": df["setup"] if "setup" in df.columns else "", "strong": (_col(df, "strong_trend") == 1).to_numpy(),
        "quality": _col(df, "quality_pct"), "buy": buy, "stop": buy * (1.0 - stop_pct), "stop_pct": stop_pct,
        "risk_share": risk_share, "risk_money": risk_share * capital,
    })
    ok = np.isfinite(out["buy"]) & (out["buy"] > 0) & np.isfinite(out["stop_pct"]) & (out["stop_pct"] > 0)
    out = out[ok].copy()
    # shares = money at risk / (buy price - first stop); never more than the capital can pay for (no borrowing)
    out["shares"] = np.floor(np.minimum(out["risk_money"] / (out["buy"] * out["stop_pct"]), capital / out["buy"])).astype(int)
    out["money"] = out["shares"] * out["buy"]
    out["share_of_capital"] = out["money"] / capital
    out = out[out["shares"] > 0]
    fresh = (out["setup"] == "fresh").to_numpy()
    order = np.lexsort((out["symbol"].to_numpy(), -out["quality"].fillna(-1).to_numpy(), ~fresh, ~out["strong"].to_numpy(dtype=bool)))
    return out.iloc[order].reset_index(drop=True)[cols]


def list_load(ledger: pd.DataFrame | None, risk_pct: float) -> float:
    """
    What the WHOLE list (bought and waiting) would need, as a fraction of the capital, if every stock were sized by the messages' rule - the dashboard's
    "The whole list would need". Used for the one sentence the Telegram footer adds when it is more than 1.0 (the list cannot all be held). 0.0 when
    nothing on the list can be sized.
    """
    rows = sized_list(ledger, LOAD_CAPITAL, risk_pct)
    return float(rows["share_of_capital"].sum()) if len(rows) else 0.0


def plan(ledger: pd.DataFrame | None, capital: float, risk_pct: float, max_stocks: int, held=()) -> tuple[pd.DataFrame, dict]:
    """
    The sized list with a verdict on every row, and the totals. Stocks in `held` (already bought, or with a buy order placed) come first, in the same
    order of preference, and count against the rooms and the money; the rest are worked through in order: "Take" while a room is free and the money
    fits, otherwise a "Skip" that says which limit stopped it.
    """
    rows = sized_list(ledger, capital, risk_pct)
    held = set(held)
    if rows.empty:
        return rows.assign(verdict=pd.Series(dtype=object), running=pd.Series(dtype=float)), {
            "n": 0, "all_money": 0.0, "all_risk": 0.0, "take": 0, "rooms": 0, "money": 0.0, "held_money": 0.0, "left_out": listed(ledger)}
    is_held = rows["symbol"].isin(held).to_numpy()
    rows = pd.concat([rows[is_held], rows[~is_held]], ignore_index=True)       # held first; each part keeps the order of preference
    verdict, running = [], []
    rooms, money = 0, 0.0
    for r in rows.itertuples(index=False):
        if r.symbol in held:
            verdict.append(HELD)
            rooms += 1
            money += r.share_of_capital
        elif rooms >= max_stocks:
            verdict.append(NO_ROOM)
        elif money + r.share_of_capital > 1.0 + 1e-9:
            verdict.append(NO_MONEY)
        else:
            verdict.append(TAKE)
            rooms += 1
            money += r.share_of_capital
        running.append(money)
    rows = rows.assign(verdict=verdict, running=running)
    held_rows = rows[rows["verdict"] == HELD]
    totals = {
        "n": int(len(rows)), "all_money": float(rows["share_of_capital"].sum()), "all_risk": float(rows["risk_share"].sum()),
        "take": int((rows["verdict"] == TAKE).sum()), "rooms": int(rooms), "money": float(money),
        "held_money": float(held_rows["share_of_capital"].sum()), "left_out": listed(ledger) - int(len(rows)),
    }
    return rows, totals

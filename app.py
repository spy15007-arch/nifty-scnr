"""
Streamlit dashboard for the NIFTY scanner. It only reads the files GitHub Actions already commits - no separate
data pipeline and no import of the scanner package, so it stays a plain, safe viewer:

    scan_results_btst.csv / scan_results_swing.csv   the latest picks, with the whole trade plan
    summary_btst.md / summary_swing.md                the latest scan write-up (market, breadth, reasons)
    reports/output/building_up_swing.csv              strong stocks building up near a breakout (a watch list, no order)
    tracking/trades.csv                               the scoreboard ledger (every pick, followed by the plan)

REWRITTEN 2026-10-20 for the evidence-based selection: picks are ranked by a QUALITY percentile (a rank, not a
probability) and every pick carries entry, stop, trailing stop and size. The scoreboard counts only trades that
have been SOLD (stopped out / time limit); buy orders that never triggered are shown separately and are neither wins
nor losses.

REWORDED 2026-11-01 in plain words, at the user's request (the Telegram "Your list" headings were confusing, then the same was asked of the
rest): "bought / sold / waiting to buy / stop-loss / buy order" instead of "open / closed / pending / buy-stop", readable column names instead
of the raw file names (symbol, entry_mode, plus_1R ...), and a few one-line explanations (R, ATR, profit factor). The files and the ledger
columns are unchanged: only what is shown on this page changed.
"""
import os
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="NIFTY Scanner Dashboard", layout="wide")
st.title("📊 NIFTY Scanner Dashboard")

LEDGER = "tracking/trades.csv"
SETUP_LABEL = {"fresh": "🔥 Fresh breakout", "primed": "👀 About to break out"}
HOW_TO_BUY = {"close": "Buy now", "stop": "Place buy order"}
# the ledger's status words, in the words used in the Telegram message
STATUS_LABEL = {"CLOSED": "Sold", "OPEN": "Bought, not sold yet", "PENDING": "Waiting to buy", "UNFILLED": "Never bought", "VOID": "Dropped (split?)"}
STATUS_COLORS = {"Sold": "#555555", "Bought, not sold yet": "#2e6da4", "Waiting to buy": "#e8a33d", "Never bought": "#999999", "Dropped (split?)": "#999999"}
WHY_ENDED = {"stop": "Stop-loss hit", "trail": "Trailing stop-loss hit", "gap_stop": "Opened below the stop-loss",
             "same_day_stop": "Bought and hit the stop-loss the same day", "time": "60-session limit reached",
             "expired": "Buy order expired", "gapped_past_trigger": "Skipped: opened too far above the buy price"}
R_HELP = ("R = the amount you risked on a trade (buy price minus the first stop-loss). +1R means the trade is up as much as you risked; "
          "-1R means it has fallen to its first stop-loss.")

# the names of the picks-table columns as the files have them -> what the page shows
PICK_COLUMNS = {
    "symbol": "Stock", "setup": "Setup", "quality_pct": "Quality (0-100)", "trend": "200-day trend", "spiked": "Recent run-up",
    "entry_mode": "How to buy", "entry_price": "Buy at", "max_entry": "Skip if it opens above", "stop_loss": "Stop-loss",
    "risk_pct": "Risk to stop-loss", "trail_pct": "Trailing stop-loss (% below highest close)",
    "plus_1R": "Profit mark 1", "plus_2R": "Profit mark 2", "plus_3R": "Profit mark 3",
    "delivery_20d_pct": "Delivery % (20-day avg)", "vol_ratio": "Volume vs normal", "gain_pct": "Day's gain",
    "ext_pct": "Vs breakout level (50-day high)", "dist_52w_high_pct": "Vs 52-week high", "ret_12m_pct": "12-month return",
    "sector": "Sector", "stage": "Setup details",
}
BUILDING_COLUMNS = {"symbol": "Stock", "trend": "200-day trend", "quality_pct": "Quality (0-100)", "gap_pct": "Below its 50-day high",
                    "high_50d": "50-day high (the level to clear)", "price": "Last price", "ret_12m_pct": "12-month return"}
BUILDING_FORMAT = {"quality_pct": "{:.0f}", "gap_pct": "{:.1%}", "high_50d": "{:,.2f}", "price": "{:,.2f}", "ret_12m_pct": "{:+.0%}"}
PICK_FORMAT = {"quality_pct": "{:.0f}", "entry_price": "{:,.2f}", "max_entry": "{:,.2f}", "stop_loss": "{:,.2f}",
               "risk_pct": "{:.1%}", "trail_pct": "{:.1%}", "plus_1R": "{:,.2f}", "plus_2R": "{:,.2f}", "plus_3R": "{:,.2f}",
               "delivery_20d_pct": "{:.0f}%", "vol_ratio": "{:.1f}x", "gain_pct": "{:+.1%}",
               "ext_pct": "{:+.1%}", "dist_52w_high_pct": "{:+.1%}", "ret_12m_pct": "{:+.0%}"}


def _df(data, **kw):
    """st.dataframe at full width on any Streamlit version (the width argument changed in 2025)."""
    try:
        st.dataframe(data, width="stretch", **kw)
    except Exception:  # noqa: BLE001 - older Streamlit versions only know use_container_width
        st.dataframe(data, use_container_width=True, **kw)


def _read_csv(path: str):
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except (pd.errors.EmptyDataError, UnicodeDecodeError):
        return None
    return None if df.empty else df


def _is_yes(v) -> bool:
    """A flag read from a CSV (True / 1 / 1.0 / 'True'); blank and NaN are not yes."""
    return str(v).strip().lower() in ("true", "1", "1.0")


def _mtime(path: str) -> str:
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")


# --------------------------------------------------------------------------- #
def render_building_up(mode: str):
    """The swing scan's watch list: strong stocks a few % below their 50-day high, not close enough to be picks yet. No order, nothing is tracked."""
    if mode != "swing":
        return
    df = _read_csv(f"reports/output/building_up_{mode}.csv")
    if df is None:
        return
    st.subheader("🔭 Building up near a breakout (watch only - no order yet)")
    show = df.copy()
    show["trend"] = show["strong_trend"].map(lambda v: "💪 strong" if _is_yes(v) else "rising") if "strong_trend" in show.columns else ""
    cols = [c_ for c_ in BUILDING_COLUMNS if c_ in show.columns]
    show = show[cols].rename(columns=BUILDING_COLUMNS)
    fmt = {BUILDING_COLUMNS[k]: v for k, v in BUILDING_FORMAT.items() if k in cols}
    _df(show.style.format(fmt, na_rep="–"), height=min(620, 80 + 36 * len(show)))
    st.caption("Strong stocks (top half by quality, 200-day average rising) that are a few % below their 50-day high - not close enough to be a pick yet. "
               "A stock joins the picks when it gets within 3% of that high, or breaks above it. This list is a heads-up only: no order is needed, "
               "nothing here is tracked, and it is not part of the rule that was back-tested. 💪 = the 200-day average is up 5%+ in a month (listed first).")


def render_scan_tab(mode: str, label: str):
    path = f"scan_results_{mode}.csv"
    df = _read_csv(path)
    summary_path = f"summary_{mode}.md"
    if os.path.exists(summary_path):
        with open(summary_path, encoding="utf-8") as f:
            text = f.read()
        head = text.split("\n| # |")[0]            # title, basis, market, breadth, funnel
        st.markdown(head)
    if df is None:
        st.info(f"No new {label} pick in the latest run - every pick must be in a breakout setup AND in the top half of stocks "
                f"by quality, and stocks already on your list are not repeated.")
        render_building_up(mode)
        return
    st.caption(f"Files last updated: {_mtime(path)}")

    c = st.columns(5 if "strong_trend" in df.columns else 4)
    c[0].metric("Picks", len(df))
    c[1].metric("🔥 Fresh breakouts", int((df["setup"] == "fresh").sum()))
    c[2].metric("👀 About to break out", int((df["setup"] == "primed").sum()))
    c[3].metric("Buy now / buy orders", f"{int((df['entry_mode'] == 'close').sum())} / {int((df['entry_mode'] == 'stop').sum())}",
                help="Buy now = buy near the close. Buy orders = GTT / stop-limit orders that buy only if the price rises to the 'Buy at' price.")
    if "strong_trend" in df.columns:
        c[4].metric("💪 Strong uptrend", int(df["strong_trend"].map(_is_yes).sum()))

    show = df.copy()
    show["setup"] = show["setup"].map(lambda s: SETUP_LABEL.get(s, s))
    show["entry_mode"] = show["entry_mode"].map(HOW_TO_BUY).fillna(show["entry_mode"])
    if "strong_trend" in show.columns:
        show["trend"] = show["strong_trend"].map(lambda v: "💪 strong" if _is_yes(v) else "")
    if "spiked" in show.columns:
        show["spiked"] = show["spiked"].map(lambda v: "⚠️ already spiked" if _is_yes(v) else "")
    cols = [c_ for c_ in PICK_COLUMNS if c_ in show.columns]
    show = show[cols].rename(columns=PICK_COLUMNS)
    fmt = {PICK_COLUMNS[k]: v for k, v in PICK_FORMAT.items() if k in cols}
    _df(show.style.format(fmt, na_rep="–"), height=min(620, 80 + 36 * len(show)))
    st.caption("Quality = rank among today's liquid stocks (100 = best): a filter (top half only), not a probability and not a ladder. "
               "The stop-loss moves up behind the highest close (the trailing stop-loss column); there is no fixed target. "
               "Profit marks 1, 2 and 3 are the prices where you would be up 1x, 2x or 3x the amount you risk per share (buy price minus stop-loss) - "
               "reference points only, not targets. The size is yours to set: risk at most 1% of your capital per trade (half in a weak market), "
               "i.e. shares = the money you are willing to lose / (buy price - stop-loss).")
    if "strong_trend" in df.columns or "spiked" in df.columns:
        st.caption("💪 strong = the 200-day average is up 5%+ in a month (listed first; it did better in a 10-year replay, but the live scoreboard has to confirm it). "
                   "⚠️ already spiked = up sharply in the last 5 days: buy in continuation (at the 'Buy at' price), not on a dip.")
    st.download_button(f"Download {label} CSV", df.to_csv(index=False), file_name=f"scan_results_{mode}.csv")
    render_building_up(mode)


# --------------------------------------------------------------------------- #
def load_ledger():
    df = _read_csv(LEDGER)
    if df is None:
        return None
    for col in ("quality_pct", "net", "R", "peak_R", "trigger", "trail", "fill_px", "last_close", "atr_pct", "fill_n", "exit_n",
                "sessions", "exit_px", "init_stop", "trend200", "run_up", "z_run_up", "strong_trend", "spiked"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _stats(g: pd.DataFrame) -> dict:
    net, r = g["net"].astype(float), g["R"].astype(float)
    wins, losses = net[net > 0], net[net <= 0]
    return {
        "Trades": len(g), "Win rate": len(wins) / len(g), "Avg after costs": net.mean(), "Avg R": r.mean(),
        "Median": net.median(),
        "Profit factor": (wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else np.inf,
        "Best R": r.max(), "Worst R": r.min(),
    }


STATS_LABEL = {"Avg after costs": "Average gain/loss (after costs)", "Avg R": "Average R", "Median": "Typical trade (median)"}


def _stats_table(df: pd.DataFrame, by: str, labels: dict | None = None) -> pd.DataFrame:
    rows = {}
    for k, g in df.groupby(by):
        if str(k) == "" or str(k) == "nan":
            continue
        rows[labels.get(k, k) if labels else k] = _stats(g)
    return pd.DataFrame(rows).T.rename(columns=STATS_LABEL) if rows else pd.DataFrame()


STATS_FMT = {"Win rate": "{:.0%}", "Average gain/loss (after costs)": "{:+.2%}", "Average R": "{:+.2f}", "Typical trade (median)": "{:+.2%}",
             "Profit factor": "{:.2f}", "Best R": "{:+.1f}", "Worst R": "{:+.1f}"}


def render_open_tab(df: pd.DataFrame | None):
    """'Your list' - the same two lists as the Telegram message: bought and not sold yet, and buy orders still waiting."""
    if df is None:
        st.info("No tracked trades yet - this fills in once a scan has logged its first picks.")
        return
    openp = df[df["status"] == "OPEN"].sort_values("signal_date", ascending=False)
    pend = df[df["status"] == "PENDING"].sort_values("signal_date", ascending=False)
    c = st.columns(3)
    c[0].metric("Bought, not sold yet", len(openp))
    c[1].metric("Waiting to buy", len(pend))
    c[2].metric("Average so far (in R)", f"{openp['R'].mean():+.2f}R" if len(openp) and openp["R"].notna().any() else "–", help=R_HELP)

    st.subheader("Bought, not sold yet - keep your stop-loss updated")
    if len(openp):
        t = openp[["symbol", "scan_mode", "setup", "signal_date", "fill_date", "fill_px", "trail", "last_close", "net", "R", "peak_R"]].copy()
        t["setup"] = t["setup"].map(lambda s: SETUP_LABEL.get(s, s))
        t.columns = ["Stock", "Scan", "Setup", "Picked on", "Bought on", "Bought at", "Sell if price falls to", "Last close",
                     "Gain / loss so far", "R so far", "Best R so far"]
        _df(t.style.format({"Bought at": "{:,.2f}", "Sell if price falls to": "{:,.2f}", "Last close": "{:,.2f}", "Gain / loss so far": "{:+.1%}",
                            "R so far": "{:+.2f}", "Best R so far": "{:+.1f}"}, na_rep="–"), hide_index=True)
        st.caption("'Sell if price falls to' is your current stop-loss: the higher of the first stop-loss and the trailing stop-loss "
                   "(5 x the stock's average daily range, ATR, below its highest close). Move your stop-loss up to it when it rises; never lower it. "
                   "'Gain / loss so far' is marked at the last completed close, after the 0.35% round-trip cost. "
                   "R = the amount you risked on the trade: +1R means it is up as much as you risked.")
    else:
        st.info("Nothing bought and still held.")

    st.subheader("Waiting to buy - buy orders that have not triggered yet")
    if len(pend):
        t = pend[["symbol", "scan_mode", "setup", "signal_date", "trigger", "valid_sessions", "sessions"]].copy()
        t["setup"] = t["setup"].map(lambda s: SETUP_LABEL.get(s, s))
        t["left"] = (t["valid_sessions"] - t["sessions"]).clip(lower=0)
        t = t[["symbol", "scan_mode", "setup", "signal_date", "trigger", "left"]]
        t.columns = ["Stock", "Scan", "Setup", "Picked on", "Buy at", "Order valid for (more sessions)"]
        _df(t.style.format({"Buy at": "{:,.2f}"}, na_rep="–"), hide_index=True)
        st.caption("Each order buys only if the price rises to its 'Buy at' price within the sessions shown; after that it expires and is not a trade.")
    else:
        st.info("No buy orders waiting.")


def render_scoreboard_tab(df: pd.DataFrame | None):
    if df is None:
        st.info("No tracked trades yet.")
        return
    st.caption("Every pick, followed exactly by the plan: a buy order (or buy now), a stop-loss 2.5 x ATR below the buy price that then follows the price "
               "up at 5 x ATR below the highest close, and a sale after 60 sessions at the latest - all after 0.35% round-trip costs "
               "(ATR = the stock's average daily range). Only sold trades count: a buy order that never triggered is neither a win nor a loss.")
    closed = df[df["status"] == "CLOSED"]
    c = st.columns(6)
    c[0].metric("Picks logged", len(df))
    c[1].metric("Sold (finished)", len(closed))
    c[2].metric("Bought, not sold yet", int((df["status"] == "OPEN").sum()))
    c[3].metric("Waiting to buy", int((df["status"] == "PENDING").sum()))
    c[4].metric("Never bought", int((df["status"] == "UNFILLED").sum()), help="The buy order expired, or the stock opened too far above the buy price.")
    c[5].metric("Dropped (split?)", int((df["status"] == "VOID").sum()), help="The price jumped as if from a split / bonus / demerger, so the trade can no longer be scored.")

    if closed.empty:
        st.info("No trade has finished yet. Results appear when a trade is sold: its stop-loss is hit or it reaches the 60-session limit.")
    else:
        s = _stats(closed)
        c = st.columns(5)
        c[0].metric("Win rate", f"{s['Win rate']:.0%}")
        c[1].metric("Avg per trade (after costs)", f"{s['Avg after costs']:+.2%}")
        c[2].metric("Average R", f"{s['Avg R']:+.2f}", help=R_HELP)
        c[3].metric("Profit factor", "∞" if np.isinf(s["Profit factor"]) else f"{s['Profit factor']:.2f}",
                    help="Total made by the winning trades divided by the total lost by the losing ones. Above 1 means the winners outweigh the losers.")
        c[4].metric("Best / worst R", f"{s['Best R']:+.1f} / {s['Worst R']:+.1f}", help=R_HELP)
        if len(closed) < 30:
            st.warning(f"Only {len(closed)} finished trades - far too few to judge. In the 2019-2026 back-test about 4 trades in 10 won, "
                       f"a few big winners paid for the many small stop-loss exits, and 2024-26 was about break-even after costs; treat every "
                       f"number here as noise until there are 30+ finished trades.")

        cur = closed.dropna(subset=["R"]).sort_values("exit_date")
        if len(cur) >= 2:
            st.subheader("Running total of R (all finished trades, in the order they were sold)")
            st.line_chart(pd.DataFrame({"Running total of R": cur["R"].cumsum().to_numpy()}, index=pd.to_datetime(cur["exit_date"], errors="coerce")))

        bucket = pd.cut(closed["quality_pct"], [0, 65, 80, 90, 100.01], labels=["50-65", "65-80", "80-90", "90-100"], right=False)
        cl = closed.assign(bucket=bucket.astype(str))
        groupings = [("By quality score (does a higher score do better?)", "bucket", None),
                     ("By setup", "setup", SETUP_LABEL), ("By scan", "scan_mode", {"btst": "🌙 BTST", "swing": "📈 Swing"}),
                     ("By market condition on the pick day", "regime", None)]
        # picks logged since 2026-10-31 record the 200-day trend and the run-up; older ones are blank and left out of these two tables
        for col, name, yes, no, title in (
                ("strong_trend", "trend_group", "💪 Strong uptrend", "Other picks", "By 200-day trend when picked (💪 = 200-day average up 5%+ in a month)"),
                ("spiked", "spike_group", "⚠️ Already spiked", "Not spiked", "By run-up before the pick")):
            if col in cl.columns and cl[col].notna().any():
                cl[name] = cl[col].map(lambda v, yes=yes, no=no: yes if v == 1 else no if v == 0 else None)
                groupings.append((title, name, None))
        for title, by, labels in groupings:
            t = _stats_table(cl, by, labels)
            if not t.empty:
                st.subheader(title)
                _df(t.style.format(STATS_FMT))
        if "trend_group" in cl.columns or "spike_group" in cl.columns:
            st.caption("The two trend / run-up tables only include picks logged since 31 Oct 2026. They started as hints from a 10-year replay "
                       "(strong uptrends did better; spiked picks did not do measurably worse), so a group with fewer than 30 finished trades is noise.")

    st.subheader("All tracked trades")
    show = df.sort_values("signal_date", ascending=False).copy()
    show["setup"] = show["setup"].map(lambda s: SETUP_LABEL.get(s, s))
    show["status"] = show["status"].map(STATUS_LABEL).fillna(show["status"])
    if "entry_mode" in show.columns:
        show["entry_mode"] = show["entry_mode"].map(HOW_TO_BUY).fillna(show["entry_mode"])
    if "exit_reason" in show.columns:
        show["exit_reason"] = show["exit_reason"].map(WHY_ENDED).fillna(show["exit_reason"])
    trade_columns = {"symbol": "Stock", "scan_mode": "Scan", "setup": "Setup", "signal_date": "Picked on", "status": "Status",
                     "entry_mode": "How to buy", "trigger": "Buy at", "fill_date": "Bought on", "fill_px": "Bought at",
                     "exit_date": "Sold on", "exit_px": "Sold at", "exit_reason": "Why it ended", "net": "Gain / loss (after costs)",
                     "R": "R", "peak_R": "Best R reached", "quality_pct": "Quality", "regime": "Market on pick day"}
    cols = [c_ for c_ in trade_columns if c_ in show.columns]
    show = show[cols].rename(columns=trade_columns)

    def status_style(val):
        return f"background-color: {STATUS_COLORS.get(val, '#333333')}; color: white; font-weight: bold; text-align: center;"

    fmt = {"trigger": "{:,.2f}", "fill_px": "{:,.2f}", "exit_px": "{:,.2f}", "net": "{:+.1%}", "R": "{:+.2f}", "peak_R": "{:+.1f}",
           "quality_pct": "{:.0f}"}
    fmt = {trade_columns[k]: v for k, v in fmt.items() if k in cols}
    _df(show.style.map(status_style, subset=["Status"]).format(fmt, na_rep="–"), height=420)
    st.download_button("Download the ledger", df.to_csv(index=False), file_name="trades.csv")


# --------------------------------------------------------------------------- #
ledger = load_ledger()
tab1, tab2, tab3, tab4 = st.tabs(["🌙 BTST", "📈 Swing", "📌 Your list", "📒 Scoreboard"])
with tab1:
    render_scan_tab("btst", "BTST")
with tab2:
    render_scan_tab("swing", "Swing")
with tab3:
    render_open_tab(ledger)
with tab4:
    render_scoreboard_tab(ledger)

st.markdown("---")
st.caption("Refreshes whenever the GitHub Actions scans commit new results - reload this page to see the latest. "
           "A ranked shortlist, not a guarantee: no rule predicts every move.")

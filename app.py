"""
Streamlit dashboard for the NIFTY scanner. It only reads the files GitHub Actions already commits - no separate
data pipeline and no import of the scanner package, so it stays a plain, safe viewer:

    scan_results_btst.csv / scan_results_swing.csv   the latest picks, with the whole trade plan
    summary_btst.md / summary_swing.md                the latest scan write-up (market, breadth, reasons)
    tracking/trades.csv                               the scoreboard ledger (every pick, followed by the plan)

REWRITTEN 2026-10-20 for the evidence-based selection: picks are ranked by a QUALITY percentile (a rank, not a
probability) and every pick carries entry, stop, trailing stop and size. The scoreboard counts only trades that
have CLOSED (stopped out / time limit); buy-stops that never triggered are shown separately and are neither wins
nor losses.
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
STATUS_COLORS = {"CLOSED": "#555555", "OPEN": "#2e6da4", "PENDING": "#e8a33d", "UNFILLED": "#999999", "VOID": "#999999"}


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
        return
    st.caption(f"Files last updated: {_mtime(path)}")

    c = st.columns(5 if "strong_trend" in df.columns else 4)
    c[0].metric("Picks", len(df))
    c[1].metric("🔥 Fresh breakouts", int((df["setup"] == "fresh").sum()))
    c[2].metric("👀 About to break out", int((df["setup"] == "primed").sum()))
    c[3].metric("Buy now / buy-stop", f"{int((df['entry_mode'] == 'close').sum())} / {int((df['entry_mode'] == 'stop').sum())}")
    if "strong_trend" in df.columns:
        c[4].metric("💪 Strong uptrend", int(df["strong_trend"].map(_is_yes).sum()))

    show = df.copy()
    show["setup"] = show["setup"].map(lambda s: SETUP_LABEL.get(s, s))
    show["entry_mode"] = show["entry_mode"].map({"close": "Buy now", "stop": "Buy-stop"}).fillna(show["entry_mode"])
    if "strong_trend" in show.columns:
        show["trend"] = show["strong_trend"].map(lambda v: "💪 strong" if _is_yes(v) else "")
    if "spiked" in show.columns:
        show["spiked"] = show["spiked"].map(lambda v: "⚠️ already spiked" if _is_yes(v) else "")
    cols = ["symbol", "setup", "quality_pct", "trend", "spiked", "entry_mode", "entry_price", "max_entry", "stop_loss", "risk_pct",
            "trail_pct", "plus_1R", "plus_2R", "plus_3R", "delivery_20d_pct", "vol_ratio",
            "gain_pct", "ext_pct", "dist_52w_high_pct", "ret_12m_pct", "sector", "stage"]
    show = show[[c_ for c_ in cols if c_ in show.columns]]

    fmt = {"quality_pct": "{:.0f}", "entry_price": "{:,.2f}", "max_entry": "{:,.2f}", "stop_loss": "{:,.2f}",
           "risk_pct": "{:.1%}", "trail_pct": "{:.1%}", "plus_1R": "{:,.2f}", "plus_2R": "{:,.2f}", "plus_3R": "{:,.2f}",
           "delivery_20d_pct": "{:.0f}%", "vol_ratio": "{:.1f}x", "gain_pct": "{:+.1%}",
           "ext_pct": "{:+.1%}", "dist_52w_high_pct": "{:+.1%}", "ret_12m_pct": "{:+.0%}"}
    styled = show.style.format({k: v for k, v in fmt.items() if k in show.columns}, na_rep="–")
    _df(styled, height=min(620, 80 + 36 * len(show)))
    st.caption("Quality = rank among today's liquid stocks (100 = best): a filter (top half only), not a probability and not a ladder. Stop trails up behind the highest close "
               "(trail_pct); there is no fixed target - +1R/+2R/+3R are reference levels. Position size is yours to set: risk at most 1% of your "
               "capital per trade (half in a weak market), i.e. shares = the money you are willing to lose / (entry - stop).")
    if "strong_trend" in df.columns or "spiked" in df.columns:
        st.caption("💪 strong = the 200-day average is up 5%+ in a month (listed first; it did better in a 10-year replay, but the live scoreboard has to confirm it). "
                   "⚠️ already spiked = up sharply in the last 5 days: buy in continuation (at the Buy-at price), not on a dip.")
    st.download_button(f"Download {label} CSV", df.to_csv(index=False), file_name=f"scan_results_{mode}.csv")


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


def _stats_table(df: pd.DataFrame, by: str, labels: dict | None = None) -> pd.DataFrame:
    rows = {}
    for k, g in df.groupby(by):
        if str(k) == "" or str(k) == "nan":
            continue
        rows[labels.get(k, k) if labels else k] = _stats(g)
    return pd.DataFrame(rows).T if rows else pd.DataFrame()


STATS_FMT = {"Win rate": "{:.0%}", "Avg after costs": "{:+.2%}", "Avg R": "{:+.2f}", "Median": "{:+.2%}",
             "Profit factor": "{:.2f}", "Best R": "{:+.1f}", "Worst R": "{:+.1f}"}


def render_open_tab(df: pd.DataFrame | None):
    if df is None:
        st.info("No tracked trades yet - this fills in once a scan has logged its first picks.")
        return
    openp = df[df["status"] == "OPEN"].sort_values("signal_date", ascending=False)
    pend = df[df["status"] == "PENDING"].sort_values("signal_date", ascending=False)
    c = st.columns(3)
    c[0].metric("Open positions", len(openp))
    c[1].metric("Waiting for their buy-stop", len(pend))
    c[2].metric("Average mark-to-market", f"{openp['R'].mean():+.2f}R" if len(openp) and openp["R"].notna().any() else "–")

    st.subheader("Open - keep the stop updated")
    if len(openp):
        t = openp[["symbol", "scan_mode", "setup", "signal_date", "fill_date", "fill_px", "trail", "last_close", "net", "R", "peak_R"]].copy()
        t["setup"] = t["setup"].map(lambda s: SETUP_LABEL.get(s, s))
        t.columns = ["Stock", "Scan", "Setup", "Signal", "Filled", "Fill price", "Current stop", "Last close", "Mark-to-market", "R", "Peak R"]
        _df(t.style.format({"Fill price": "{:,.2f}", "Current stop": "{:,.2f}", "Last close": "{:,.2f}", "Mark-to-market": "{:+.1%}",
                                     "R": "{:+.2f}", "Peak R": "{:+.1f}"}, na_rep="–"), hide_index=True)
        st.caption("Current stop = the higher of the initial stop and the trailing stop (5 ATR below the highest close). "
                   "Raise your stop to it; never lower it. Marked at the last completed close, after the 0.35% round-trip cost.")
    else:
        st.info("No open positions.")

    st.subheader("Waiting for their buy-stop")
    if len(pend):
        t = pend[["symbol", "scan_mode", "setup", "signal_date", "trigger", "valid_sessions", "sessions"]].copy()
        t["setup"] = t["setup"].map(lambda s: SETUP_LABEL.get(s, s))
        t["left"] = (t["valid_sessions"] - t["sessions"]).clip(lower=0)
        t = t[["symbol", "scan_mode", "setup", "signal_date", "trigger", "left"]]
        t.columns = ["Stock", "Scan", "Setup", "Signal", "Buy-stop price", "Sessions left"]
        _df(t.style.format({"Buy-stop price": "{:,.2f}"}, na_rep="–"), hide_index=True)
    else:
        st.info("No buy-stops waiting.")


def render_scoreboard_tab(df: pd.DataFrame | None):
    if df is None:
        st.info("No tracked trades yet.")
        return
    st.caption("Every pick, followed exactly by the plan (buy-stop or buy-now, 2.5 ATR stop, 5 ATR trailing stop, 60-session limit), "
               "after 0.35% round-trip costs. Only CLOSED trades count: a buy-stop that never triggered is neither a win nor a loss.")
    closed = df[df["status"] == "CLOSED"]
    c = st.columns(6)
    c[0].metric("Picks logged", len(df))
    c[1].metric("Closed", len(closed))
    c[2].metric("Open", int((df["status"] == "OPEN").sum()))
    c[3].metric("Waiting", int((df["status"] == "PENDING").sum()))
    c[4].metric("Never triggered", int((df["status"] == "UNFILLED").sum()))
    c[5].metric("Dropped (split?)", int((df["status"] == "VOID").sum()))

    if closed.empty:
        st.info("No trade has closed yet. Results appear as tracked trades are stopped out or reach the time limit.")
    else:
        s = _stats(closed)
        c = st.columns(5)
        c[0].metric("Win rate", f"{s['Win rate']:.0%}")
        c[1].metric("Avg per trade (after costs)", f"{s['Avg after costs']:+.2%}")
        c[2].metric("Avg R", f"{s['Avg R']:+.2f}")
        c[3].metric("Profit factor", "∞" if np.isinf(s["Profit factor"]) else f"{s['Profit factor']:.2f}")
        c[4].metric("Best / worst R", f"{s['Best R']:+.1f} / {s['Worst R']:+.1f}")
        if len(closed) < 30:
            st.warning(f"Only {len(closed)} closed trades - far too few to judge. In the 2019-2026 back-test about 4 trades in 10 won, "
                       f"a few big winners paid for the many small stop-outs, and 2024-26 was about break-even after costs; treat every "
                       f"number here as noise until there are 30+ closed trades.")

        cur = closed.dropna(subset=["R"]).sort_values("exit_date")
        if len(cur) >= 2:
            st.subheader("Cumulative R (sum of all closed trades, in order of exit)")
            st.line_chart(pd.DataFrame({"Cumulative R": cur["R"].cumsum().to_numpy()}, index=pd.to_datetime(cur["exit_date"], errors="coerce")))

        bucket = pd.cut(closed["quality_pct"], [0, 65, 80, 90, 100.01], labels=["50-65", "65-80", "80-90", "90-100"], right=False)
        cl = closed.assign(bucket=bucket.astype(str))
        groupings = [("By quality percentile (does a higher rank do better?)", "bucket", None),
                     ("By setup", "setup", SETUP_LABEL), ("By scan", "scan_mode", {"btst": "🌙 BTST", "swing": "📈 Swing"}),
                     ("By market regime on the signal day", "regime", None)]
        # picks logged since 2026-10-31 record the 200-day trend and the run-up; older ones are blank and left out of these two tables
        for col, name, yes, no, title in (
                ("strong_trend", "trend_group", "💪 Strong uptrend", "Other picks", "By 200-day trend when picked (💪 = average up 5%+ in 20 sessions)"),
                ("spiked", "spike_group", "⚠️ Already spiked", "Not spiked", "By run-up before the signal")):
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
                       "(strong uptrends did better; spiked picks did not do measurably worse), so a group with fewer than 30 closed trades is noise.")

    st.subheader("All tracked trades")
    show = df.sort_values("signal_date", ascending=False).copy()
    show["setup"] = show["setup"].map(lambda s: SETUP_LABEL.get(s, s))
    cols = ["symbol", "scan_mode", "setup", "signal_date", "status", "entry_mode", "trigger", "fill_date", "fill_px",
            "exit_date", "exit_px", "exit_reason", "net", "R", "peak_R", "quality_pct", "regime"]
    show = show[[c_ for c_ in cols if c_ in show.columns]]

    def status_style(val):
        return f"background-color: {STATUS_COLORS.get(val, '#333333')}; color: white; font-weight: bold; text-align: center;"

    fmt = {"trigger": "{:,.2f}", "fill_px": "{:,.2f}", "exit_px": "{:,.2f}", "net": "{:+.1%}", "R": "{:+.2f}", "peak_R": "{:+.1f}",
           "quality_pct": "{:.0f}"}
    _df(show.style.map(status_style, subset=["status"]).format({k: v for k, v in fmt.items() if k in show.columns}, na_rep="–"), height=420)
    st.download_button("Download the ledger", df.to_csv(index=False), file_name="trades.csv")


# --------------------------------------------------------------------------- #
ledger = load_ledger()
tab1, tab2, tab3, tab4 = st.tabs(["🌙 BTST", "📈 Swing", "📌 Open trades", "📒 Scoreboard"])
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

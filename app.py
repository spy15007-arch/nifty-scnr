"""
Streamlit dashboard for the NIFTY scanner's latest results. Reads the
CSV files GitHub Actions already commits automatically - no separate
data pipeline, this just displays what's already there.

RENAMED (2026-09-19): morning/intraday removed entirely. afternoon->
btst, eod->swing, matching main.py's rename.

SAFE EMPTY-CSV HANDLING (2026-09-23): if a scan found zero qualifying
candidates, the CSV may exist but be empty/header-only - handled
gracefully here as a safety net (main.py now also always writes valid
headers even when empty, but this protects against any already-broken
file from before that fix).

SETUP STAGE + TRACK RECORD (2026-10-13): scan_results_*.csv now carries
a setup_stage column (fresh_breakout / pre_breakout / neither, see
scanner/breakout.py's classify_setup_stage). A third tab reads the
tracking/trade_log_*.csv ledgers (tracking/outcomes.py) and shows how
past picks actually resolved - kept as a plain CSV read here too, same
"no separate data pipeline" principle as the scan tabs, rather than
importing the scanner/tracking package into the dashboard process.

DAYS SINCE BREAK (2026-10-14): both CSVs now also carry a
days_since_break column for fresh_breakout picks (1 = the pick itself
was the breakout candle). The Track Record tab adds a win-rate-by-day
breakdown alongside the existing by-stage one - live evidence on
whether a day-1 entry actually performs differently from day-2/3.
"""
import streamlit as st
import pandas as pd
import os
from datetime import datetime

st.set_page_config(page_title="NIFTY Scanner Dashboard", layout="wide")
st.title("📊 NIFTY Scanner Dashboard")

GRADE_COLORS = {
    "A+": "#0d7d3e",
    "A": "#2e9e5b",
    "B+": "#e8a33d",
    "B": "#e8a33d",
    "C": "#999999",
}

STAGE_LABELS = {
    "fresh_breakout": "🔥 Fresh Breakout",
    "pre_breakout": "👀 Pre-Breakout",
    "neither": "–",
}

STATUS_COLORS = {
    "WON": "#0d7d3e",
    "LOST": "#b03a2e",
    "OPEN": "#2e6da4",
    "EXPIRED": "#999999",
}


def load_scan(mode: str):
    path = f"scan_results_{mode}.csv"
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None
    if df.empty:
        return None
    return df.sort_values("probability", ascending=False)


def render_scan_tab(mode: str, label: str):
    df = load_scan(mode)
    if df is None or df.empty:
        st.info(f"No {label} results yet - either the workflow hasn't run recently, or nothing qualified this session.")
        return

    mtime = datetime.fromtimestamp(os.path.getmtime(f"scan_results_{mode}.csv"))
    st.caption(f"Last updated: {mtime.strftime('%Y-%m-%d %H:%M')}")

    if "setup_stage" in df.columns:
        stage_counts = df["setup_stage"].map(lambda s: STAGE_LABELS.get(s, s)).value_counts()
        cols = st.columns(len(stage_counts) if len(stage_counts) else 1)
        for col, (stage, count) in zip(cols, stage_counts.items()):
            col.metric(stage, int(count))
        df = df.copy()
        df["setup_stage"] = df["setup_stage"].map(lambda s: STAGE_LABELS.get(s, s))

    def grade_style(val):
        color = GRADE_COLORS.get(val, "#333333")
        return f"background-color: {color}; color: white; font-weight: bold; text-align: center;"

    fmt = {
        "probability": "{:.1%}",
        "entry_distance_pct": "{:+.1%}",
        "entry_trigger": "{:.2f}",
        "stop_loss": "{:.2f}",
        "target_1": "{:.2f}",
        "target_2": "{:.2f}",
        "target_3": "{:.2f}",
        "target_4": "{:.2f}",
    }
    styled = df.style.map(grade_style, subset=["grade"]).format(fmt)

    st.dataframe(styled, use_container_width=True, height=600)
    st.download_button(f"Download {label} CSV", df.to_csv(index=False), file_name=f"scan_results_{mode}.csv")


def load_ledger(mode: str):
    path = f"tracking/trade_log_{mode}.csv"
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None
    if df.empty:
        return None
    return df.sort_values("signal_date", ascending=False)


def render_track_record_tab():
    st.caption("Every pick ever recommended, and what actually happened to it - checked automatically each "
               "scan run against target/stop, same resolution rule the backtest reports use.")

    any_data = False
    for mode, label in [("btst", "BTST"), ("swing", "Swing")]:
        df = load_ledger(mode)
        st.subheader(f"{'🌙' if mode == 'btst' else '📈'} {label}")
        if df is None or df.empty:
            st.info(f"No {label} trades tracked yet - this fills in once a scan with qualifying picks has run.")
            continue
        any_data = True

        resolved = df[df["status"].isin(["WON", "LOST"])]
        wins = int((resolved["status"] == "WON").sum())
        n_resolved = len(resolved)
        win_rate = (wins / n_resolved) if n_resolved else None
        avg_pnl = resolved["pnl_pct"].astype(float).mean() if n_resolved else None

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Open", int((df["status"] == "OPEN").sum()))
        c2.metric("Won", wins)
        c3.metric("Lost", n_resolved - wins)
        c4.metric("Win rate", f"{win_rate:.0%}" if win_rate is not None else "–")
        c5.metric("Avg P&L / resolved trade", f"{avg_pnl:+.1%}" if avg_pnl is not None else "–")

        if n_resolved > 0 and "setup_stage" in resolved.columns:
            st.caption("Win rate by setup stage (this is the evidence on whether fresh-breakout or "
                       "pre-breakout picks actually perform better for this mode):")
            by_stage = resolved.groupby("setup_stage").apply(
                lambda g: pd.Series({"count": len(g), "win_rate": (g["status"] == "WON").mean()}),
                include_groups=False,
            )
            by_stage.index = by_stage.index.map(lambda s: STAGE_LABELS.get(s, s))
            st.dataframe(
                by_stage.style.format({"win_rate": "{:.0%}"}),
                use_container_width=True,
            )

        if n_resolved > 0 and "days_since_break" in resolved.columns:
            fresh_only = resolved[resolved["days_since_break"].notna()].copy()
            if len(fresh_only) > 0:
                st.caption("Win rate by days since the breakout candle, fresh-breakout picks only - "
                           "day 1 means the pick itself WAS the breakout candle (entered the next session):")
                fresh_only["days_since_break"] = fresh_only["days_since_break"].astype(float).astype(int)
                by_days = fresh_only.groupby("days_since_break").apply(
                    lambda g: pd.Series({"count": len(g), "win_rate": (g["status"] == "WON").mean()}),
                    include_groups=False,
                )
                by_days.index = by_days.index.map(lambda d: f"Day {d}")
                st.dataframe(
                    by_days.style.format({"win_rate": "{:.0%}"}),
                    use_container_width=True,
                )

        def status_style(val):
            color = STATUS_COLORS.get(val, "#333333")
            return f"background-color: {color}; color: white; font-weight: bold; text-align: center;"

        display_df = df.copy()
        if "setup_stage" in display_df.columns:
            display_df["setup_stage"] = display_df["setup_stage"].map(lambda s: STAGE_LABELS.get(s, s))
        fmt = {
            "probability": "{:.1%}",
            "entry_trigger": "{:.2f}",
            "stop_loss": "{:.2f}",
            "target_1": "{:.2f}",
            "exit_price": "{:.2f}",
            "pnl_pct": "{:+.1%}",
        }
        styled = display_df.style.map(status_style, subset=["status"]).format(fmt, na_rep="–")
        st.dataframe(styled, use_container_width=True, height=400)
        st.download_button(f"Download {label} trade log", df.to_csv(index=False), file_name=f"trade_log_{mode}.csv")

    if not any_data:
        st.info("No tracked trades yet for either mode.")


tab1, tab2, tab3 = st.tabs(["🌙 BTST", "📈 Swing", "📒 Track Record"])
with tab1:
    render_scan_tab("btst", "BTST")
with tab2:
    render_scan_tab("swing", "Swing")
with tab3:
    render_track_record_tab()

st.markdown("---")
st.caption("Refreshes automatically whenever the GitHub Actions scan workflows commit new results - reload this page to see the latest.")

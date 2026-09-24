"""
Streamlit dashboard for the NIFTY scanner's latest results. Reads the
CSV files GitHub Actions already commits automatically - no separate
data pipeline, this just displays what's already there.

RENAMED (2026-09-19): morning/intraday removed entirely. afternoon ->
btst, eod -> swing, matching main.py's rename.
"""
import os
from datetime import datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="NIFTY Scanner Dashboard", layout="wide")
st.title("📊 NIFTY Scanner Dashboard")

GRADE_COLORS = {
    "A+": "#0d7d3e",
    "A": "#2e9e5b",
    "B+": "#e8a33d",
    "B": "#e8a33d",
    "C": "#999999",
}


def load_scan(mode: str):
    path = f"scan_results_{mode}.csv"
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        # A scan with no rows may produce an empty CSV. Treat it as no results.
        return None

    if df.empty:
        return None

    # Show the strongest candidates first within the selected scan category.
    if "probability" in df.columns:
        df["probability"] = pd.to_numeric(df["probability"], errors="coerce")
        df = df.sort_values("probability", ascending=False, na_position="last")
    return df.reset_index(drop=True)


def render_scan_tab(mode: str, label: str):
    df = load_scan(mode)
    if df is None or df.empty:
        st.info(
            f"No {label} results yet - the workflow may not have run recently, "
            "or nothing was flagged this session."
        )
        return

    mtime = datetime.fromtimestamp(os.path.getmtime(f"scan_results_{mode}.csv"))
    st.caption(f"Latest {label} scan updated: {mtime.strftime('%Y-%m-%d %H:%M')}")

    def grade_style(val):
        color = GRADE_COLORS.get(val, "#333333")
        return (
            f"background-color: {color}; color: white; "
            "font-weight: bold; text-align: center;"
        )

    styled = df.style
    if "grade" in df.columns:
        styled = styled.map(grade_style, subset=["grade"])

    styled = styled.format({
        "probability": "{:.1%}",
        "entry_distance_pct": "{:+.1%}",
        "entry_trigger": "{:.2f}",
        "stop_loss": "{:.2f}",
        "target_1": "{:.2f}",
        "target_2": "{:.2f}",
        "target_3": "{:.2f}",
        "target_4": "{:.2f}",
    })

    st.dataframe(styled, use_container_width=True, height=600)
    st.download_button(
        f"Download latest {label} CSV",
        df.to_csv(index=False),
        file_name=f"scan_results_{mode}.csv",
    )


# Each tab displays only the CSV belonging to its strategy category.
tab1, tab2 = st.tabs(["🌙 BTST", "📈 Swing"])
with tab1:
    render_scan_tab("btst", "BTST")
with tab2:
    render_scan_tab("swing", "Swing")

st.markdown("---")
st.caption(
    "Select BTST or Swing to view its latest stock list. Reload this page "
    "after GitHub Actions commits new results."
)

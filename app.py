"""
Streamlit dashboard for the NIFTY scanner's latest results. Reads the
latest dated CSV committed under reports/btst or reports/swing.
"""
import glob
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
    report_dir = f"reports/{mode}"
    paths = glob.glob(f"{report_dir}/scan_results_{mode}_*.csv")
    if not paths:
        return None, None

    # Select the newest dated report, rather than relying on a root CSV.
    path = max(paths, key=os.path.getmtime)
    if os.path.getsize(path) == 0:
        return None, path

    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return None, path

    if df.empty:
        return None, path

    if "probability" in df.columns:
        df["probability"] = pd.to_numeric(df["probability"], errors="coerce")
        # Score is the scanner probability, shown explicitly for clarity.
        df["score"] = df["probability"]
        df = df.sort_values("score", ascending=False, na_position="last")
        df = df.reset_index(drop=True)
        df.insert(0, "rank", range(1, len(df) + 1))

    return df, path


def render_scan_tab(mode: str, label: str):
    df, path = load_scan(mode)
    if df is None or df.empty:
        st.info(
            f"No {label} results yet - the workflow may not have run recently, "
            "or nothing was flagged this session."
        )
        return

    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    st.caption(f"Latest {label} scan: {mtime.strftime('%Y-%m-%d %H:%M')}")

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
        "score": "{:.1%}",
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
        file_name=os.path.basename(path),
    )


tab1, tab2 = st.tabs(["🌙 BTST", "📈 Swing"])
with tab1:
    render_scan_tab("btst", "BTST")
with tab2:
    render_scan_tab("swing", "Swing")

st.markdown("---")
st.caption(
    "Select BTST or Swing to view its latest stock list. Results are ordered "
    "from highest score to lowest score."
)

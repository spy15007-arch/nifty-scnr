"""
BTST Breakout-Readiness Scanner
================================
No scanner gives a genuine 99% breakout probability. This scores stocks 0-10
on 5 measurable signals. Even a 9-10/10 score historically follows through
well under 100% of the time. Not financial advice.
"""
import io
import time
import numpy as np
import pandas as pd
import requests
import yfinance as yf

CONFIG = {
    "min_price": 20,
    "min_avg_volume": 100000,
    "base_lookback_days": 25,
    "tight_range_pct": 25,
    "resistance_lookback_days": 60,
    "resistance_buffer_pct": 3,
    "benchmark": "^NSEI",
    "min_score_to_list": 6,
}


def load_nifty500():
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=30)
    df = pd.read_csv(io.StringIO(resp.text))
    return [s.strip() + ".NS" for s in df["Symbol"]]


def get_daily_history(ticker, period="1y"):
    try:
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df.empty or len(df) < 70:
            return None
        return df
    except Exception:
        return None


def score_strong_close(df):
    today = df.iloc[-1]
    day_range = today["High"] - today["Low"]
    if day_range <= 0:
        return 0, 0
    position = (today["Close"] - today["Low"]) / day_range
    if position >= 0.85:
        return 2, position
    elif position >= 0.65:
        return 1, position
    return 0, position


def score_volume_surge(df):
    avg_vol_50 = df["Volume"].iloc[-51:-1].mean()
    today_vol = df["Volume"].iloc[-1]
    if avg_vol_50 <= 0:
        return 0, 0
    ratio = today_vol / avg_vol_50
    if ratio >= 2.0:
        return 2, ratio
    elif ratio >= 1.3:
        return 1, ratio
    return 0, ratio


def score_base_breakout(df):
    window = df["Close"].iloc[-(CONFIG["base_lookback_days"] + 1):-1]
    if len(window) < CONFIG["base_lookback_days"]:
        return 0, None
    base_high = window.max()
    base_low = window.min()
    range_pct = (base_high - base_low) / base_low * 100
    today_close = df["Close"].iloc[-1]
    breaking_out = today_close > base_high
    tight = range_pct <= CONFIG["tight_range_pct"]
    if breaking_out and tight:
        return 2, range_pct
    elif breaking_out:
        return 1, range_pct
    return 0, range_pct


def score_relative_strength(df, bench_close):
    if len(df) < 64 or len(bench_close) < 64:
        return 0, None
    stock_ret = df["Close"].iloc[-1] / df["Close"].iloc[-64] - 1
    bench_ret = bench_close.iloc[-1] / bench_close.iloc[-64] - 1
    rel = stock_ret - bench_ret
    if rel >= 0.10:
        return 2, rel
    elif rel >= 0.03:
        return 1, rel
    return 0, rel


def score_room_to_run(df):
    lookback = df["Close"].iloc[-(CONFIG["resistance_lookback_days"] + 1):-1]
    if len(lookback) < 20:
        return 0, None
    prior_high = lookback.max()
    today_close = df["Close"].iloc[-1]
    if today_close >= prior_high:
        return 2, 0.0
    clearance_needed = (prior_high - today_close) / today_close * 100
    if clearance_needed <= CONFIG["resistance_buffer_pct"]:
        return 1, clearance_needed
    return 0, clearance_needed


def main():
    print("Loading Nifty 500 list...")
    tickers = load_nifty500()
    print(len(tickers), "tickers loaded")

    print("Downloading benchmark:", CONFIG["benchmark"])
    bench = get_daily_history(CONFIG["benchmark"], period="1y")
    bench_close = bench["Close"]

    results = []
    for i, ticker in enumerate(tickers):
        print(i + 1, "/", len(tickers), ticker, end=" ")
        df = get_daily_history(ticker)
        if df is None:
            print("skip: no data")
            continue
        if df["Close"].iloc[-1] < CONFIG["min_price"]:
            print("skip: low price")
            continue
        if df["Volume"].iloc[-51:-1].mean() < CONFIG["min_avg_volume"]:
            print("skip: illiquid")
            continue

        s1, v1 = score_strong_close(df)
        s2, v2 = score_volume_surge(df)
        s3, v3 = score_base_breakout(df)
        s4, v4 = score_relative_strength(df, bench_close)
        s5, v5 = score_room_to_run(df)
        total = s1 + s2 + s3 + s4 + s5

        results.append({
            "ticker": ticker,
            "price": round(df["Close"].iloc[-1], 2),
            "score": total,
            "strong_close_pts": s1,
            "close_position_pct": round(v1 * 100, 1) if v1 is not None else None,
            "volume_pts": s2,
            "volume_vs_avg": round(v2, 2) if v2 is not None else None,
            "base_breakout_pts": s3,
            "base_range_pct": round(v3, 1) if v3 is not None else None,
            "rs_pts": s4,
            "rs_edge_pct": round(v4 * 100, 1) if v4 is not None else None,
            "room_pts": s5,
            "resistance_clearance_pct": round(v5, 1) if v5 is not None else None,
        })
        print("score=" + str(total))
        time.sleep(0.15)

    df_res = pd.DataFrame(results)
    if not df_res.empty:
        df_res = df_res.sort_values("score", ascending=False)

    df_res.to_csv("breakout_scan_full.csv", index=False)
    shortlist = df_res[df_res["score"] >= CONFIG["min_score_to_list"]] if not df_res.empty else df_res
    shortlist.to_csv("breakout_shortlist.csv", index=False)

    write_summary(shortlist, len(tickers))
    print("Scanned", len(tickers), "- shortlisted (score >=", CONFIG["min_score_to_list"], "):", len(shortlist))


def write_summary(shortlist, total_scanned):
    from datetime import datetime

    lines = []
    lines.append("# BTST Breakout-Readiness Scan")
    lines.append("")
    lines.append("Last run: " + datetime.now().strftime("%Y-%m-%d %H:%M") + " IST")
    lines.append("")
    lines.append(
        "**No scanner gives a genuine 99% breakout probability. This is a 0-10 "
        "readiness SCORE based on 5 measurable signals, not a prediction. Even a "
        "9-10/10 score historically follows through well under 100% of the time. "
        "This is not financial advice - verify news/fundamentals before acting.**"
    )
    lines.append("")
    lines.append("Scanned " + str(total_scanned) + " stocks - " + str(len(shortlist)) + " scored 6+/10")
    lines.append("")

    if shortlist.empty:
        lines.append("No stocks scored 6 or higher today.")
    else:
        lines.append("| Stock | Price | Score /10 | Close Position % | Vol vs 50d Avg | Base Range % | RS Edge % | Resistance Clearance % |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for _, row in shortlist.iterrows():
            name = str(row["ticker"]).replace(".NS", "")
            lines.append(
                "| " + name
                + " | " + str(row["price"])
                + " | " + str(row["score"])
                + " | " + str(row["close_position_pct"])
                + " | " + str(row["volume_vs_avg"])
                + " | " + str(row["base_range_pct"])
                + " | " + str(row["rs_edge_pct"])
                + " | " + str(row["resistance_clearance_pct"])
                + " |"
            )
        lines.append("")
        lines.append(
            "Close Position %: higher = closed nearer today's high (100 = closed at day's high). "
            "Vol vs 50d Avg: above 1.3-2x signals real interest. "
            "Base Range %: lower = tighter prior consolidation (breakout more meaningful). "
            "RS Edge %: stock's 3-month return minus Nifty's 3-month return. "
            "Resistance Clearance %: 0 means today made a new high (max room); higher numbers mean "
            "an old high is close overhead."
        )

    with open("breakout_summary.md", "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()

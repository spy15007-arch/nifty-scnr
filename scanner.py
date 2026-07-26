"""
Headless Nifty 500 momentum scanner for GitHub Actions.
Runs daily after market close, writes results to scan_results.csv
and a human-readable results.md summary.
"""

import io
import time
import numpy as np
import pandas as pd
import requests
import yfinance as yf

CONFIG = {
    "min_price": 20,
    "rs_min_percentile": 70,
    "base_lookback_days": 25,
    "tight_range_pct": 20,
    "tight_vol_contraction": False,
    "pct_above_52w_low": 20,
    "pct_below_52w_high": 30,
    "min_eps_growth": 15,
    "min_sales_growth": 10,
    "benchmark": "^NSEI",
}


def load_nifty500_tickers():
    url = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=20)
    df = pd.read_csv(io.StringIO(resp.text))
    return [s.strip() + ".NS" for s in df["Symbol"]]


def get_price_history(ticker, period="2y"):
    try:
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df.empty or len(df) < 260:
            return None
        return df
    except Exception:
        return None


def trend_template_pass(df):
    close = df["Close"]
    sma50 = close.rolling(50).mean()
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()
    if len(df) < 260 or pd.isna(sma200.iloc[-1]):
        return False, {}
    price = close.iloc[-1]
    low_52w = close[-252:].min()
    high_52w = close[-252:].max()
    sma200_trending_up = sma200.iloc[-1] > sma200.iloc[-22]
    checks = {
        "price_above_150_200": price > sma150.iloc[-1] and price > sma200.iloc[-1],
        "150_above_200": sma150.iloc[-1] > sma200.iloc[-1],
        "200_trending_up": sma200_trending_up,
        "50_above_150_200": sma50.iloc[-1] > sma150.iloc[-1] > sma200.iloc[-1],
        "price_above_50": price > sma50.iloc[-1],
        "above_52w_low": price >= low_52w * (1 + CONFIG["pct_above_52w_low"] / 100),
        "near_52w_high": price >= high_52w * (1 - CONFIG["pct_below_52w_high"] / 100),
    }
    passed = all(checks.values())
    return passed, {"price": price, "low_52w": low_52w, "high_52w": high_52w, **checks}


def relative_strength_raw(df, bench_close):
    close = df["Close"]
    if len(close) < 252 or len(bench_close) < 252:
        return None

    def perf(series, days):
        if len(series) <= days:
            return None
        return series.iloc[-1] / series.iloc[-days] - 1

    stock_perf = [perf(close, 63), perf(close, 126), perf(close, 189), perf(close, 252)]
    bench_perf = [perf(bench_close, 63), perf(bench_close, 126), perf(bench_close, 189), perf(bench_close, 252)]
    if any(p is None for p in stock_perf + bench_perf):
        return None
    weights = [0.4, 0.2, 0.2, 0.2]
    rel = [(1 + s) / (1 + b) for s, b in zip(stock_perf, bench_perf)]
    return sum(w * r for w, r in zip(weights, rel))


def tight_base_pass(df):
    window = df["Close"].tail(CONFIG["base_lookback_days"])
    if len(window) < CONFIG["base_lookback_days"]:
        return False, {}
    high, low = window.max(), window.min()
    range_pct = (high - low) / low * 100
    first_half = window.iloc[: len(window) // 2]
    second_half = window.iloc[len(window) // 2:]
    contracting = second_half.std() < first_half.std()
    tight = range_pct <= CONFIG["tight_range_pct"]
    if CONFIG["tight_vol_contraction"]:
        tight = tight and contracting
    return tight, {"base_range_pct": round(range_pct, 1), "contracting": contracting}


def accumulation_score(df):
    recent = df.tail(50).copy()
    recent["chg"] = recent["Close"].pct_change()
    up_vol = recent.loc[recent["chg"] > 0, "Volume"].sum()
    down_vol = recent.loc[recent["chg"] < 0, "Volume"].sum()
    if down_vol == 0:
        return np.inf
    return round(up_vol / down_vol, 2)


def fundamentals_pass(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info
        eps_growth = info.get("earningsQuarterlyGrowth")
        sales_growth = info.get("revenueGrowth")
        if eps_growth is not None:
            eps_growth *= 100
        if sales_growth is not None:
            sales_growth *= 100
        passed = (
            eps_growth is not None and eps_growth >= CONFIG["min_eps_growth"]
            and sales_growth is not None and sales_growth >= CONFIG["min_sales_growth"]
        )
        return passed, {"eps_growth_pct": eps_growth, "sales_growth_pct": sales_growth}
    except Exception:
        return False, {"eps_growth_pct": None, "sales_growth_pct": None}


def main():
    print("Loading Nifty 500 list...")
    tickers = load_nifty500_tickers()
    print(len(tickers), "tickers loaded")

    print("Downloading benchmark:", CONFIG["benchmark"])
    bench = get_price_history(CONFIG["benchmark"], period="2y")
    bench_close = bench["Close"]

    results = []
    for i, ticker in enumerate(tickers):
        print(i + 1, "/", len(tickers), ticker, end=" ")
        df = get_price_history(ticker)
        if df is None or df["Close"].iloc[-1] < CONFIG["min_price"]:
            print("skip")
            continue
        trend_ok, trend_info = trend_template_pass(df)
        if not trend_ok:
            print("fail: trend")
            continue
        rs_raw = relative_strength_raw(df, bench_close)
        if rs_raw is None:
            print("fail: RS")
            continue
        base_ok, base_info = tight_base_pass(df)
        accum = accumulation_score(df)
        fund_ok, fund_info = fundamentals_pass(ticker)
        results.append({
            "ticker": ticker,
            "price": round(trend_info["price"], 2),
            "pct_below_52w_high": round(
                (trend_info["high_52w"] - trend_info["price"]) / trend_info["high_52w"] * 100, 1
            ),
            "rs_raw_score": round(rs_raw, 3),
            "tight_base": base_ok,
            "base_range_pct": base_info.get("base_range_pct"),
            "up_down_vol_ratio": accum,
            "eps_growth_pct": fund_info["eps_growth_pct"],
            "sales_growth_pct": fund_info["sales_growth_pct"],
            "fundamentals_pass": fund_ok,
        })
        print("OK")
        time.sleep(0.15)

    df_res = pd.DataFrame(results)
    if not df_res.empty:
        df_res["rs_rating"] = (df_res["rs_raw_score"].rank(pct=True) * 98 + 1).round(0).astype(int)
        df_res["final_candidate"] = (
            (df_res["rs_rating"] >= CONFIG["rs_min_percentile"])
            & df_res["tight_base"]
            & df_res["fundamentals_pass"]
        )
        df_res = df_res.sort_values(["final_candidate", "rs_rating"], ascending=[False, False])

    df_res.to_csv("scan_results.csv", index=False)

    candidates = df_res[df_res["final_candidate"]] if not df_res.empty else df_res
    today = pd.Timestamp.now().strftime("%Y-%m-%d")
    with open("results.md", "w") as f:
        f.write("# Scan results — " + today + "\n\n")
        f.write("Scanned " + str(len(tickers)) + " stocks, " + str(len(df_res))
                + " passed trend template, " + str(len(candidates)) + " full candidates.\n\n")
        if not candidates.empty:
            f.write(candidates[[
                "ticker", "price", "rs_rating", "pct_below_52w_high", "base_range_pct",
                "up_down_vol_ratio", "eps_growth_pct", "sales_growth_pct"
            ]].to_markdown(index=False))
        else:
            f.write("No full candidates today.\n")

    print("Done. candidates:", len(candidates))


if __name__ == "__main__":
    main()

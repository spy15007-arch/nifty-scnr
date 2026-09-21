import streamlit as st
import pandas as pd
import os
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from streamlit_lightweight_charts import renderLightweightCharts

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="Apex Quant Engine", layout="wide", page_icon="📈")

# --- DATA LOADING FUNCTIONS ---
@st.cache_data(ttl=30)
def load_data(file_path):
    if os.path.exists(file_path):
        try: return pd.read_csv(file_path)
        except: return pd.DataFrame()
    return pd.DataFrame()

# --- PORTFOLIO ACTION FUNCTIONS ---
def add_to_portfolio(raw_stock, df_source):
    if raw_stock is None or df_source.empty: return
    stock_row = df_source[df_source['RawStock'] == raw_stock].iloc[0]
    file_path = "portfolio.csv"
    pf = pd.read_csv(file_path) if os.path.exists(file_path) else pd.DataFrame(columns=['Stock', 'RawStock', 'Entry', 'Qty', 'Current_SL', 'T1', 'T2', 'T3', 'Status', 'Sector'])
    if not pf.empty and (pf['RawStock'] == stock_row['RawStock']).any():
        if 'Active' in pf.loc[pf['RawStock'] == stock_row['RawStock'], 'Status'].values:
            st.toast(f"⚠️ {stock_row['RawStock']} is already in your Active Portfolio!", icon="⚠️")
            return
    new_trade = pd.DataFrame([{'Stock': stock_row['Stock'], 'RawStock': stock_row['RawStock'], 'Entry': stock_row['Entry'], 'Qty': stock_row['Qty'], 'Current_SL': stock_row['EqSL'], 'T1': stock_row['EqT1'], 'T2': stock_row['EqT2'], 'T3': stock_row['EqT3'], 'Status': 'Active', 'Sector': 'Unknown'}])
    pf = pd.concat([pf, new_trade], ignore_index=True)
    pf.to_csv(file_path, index=False)
    st.toast(f"✅ Successfully added {stock_row['RawStock']} to your Portfolio Tracking Engine!", icon="✅")

def remove_from_portfolio(raw_stock):
    file_path = "portfolio.csv"
    if os.path.exists(file_path):
        pf = pd.read_csv(file_path)
        pf = pf[pf['RawStock'] != raw_stock]
        pf.to_csv(file_path, index=False)
        st.toast(f"🗑️ Removed {raw_stock} from Portfolio!", icon="✅")

def display_interactive_table(df, tab_name):
    if df.empty:
        st.info(f"No qualifying setups matched the criteria for {tab_name} today.")
        return
    
    df = df.sort_values(by=['Score', 'Vol vs 50d'], ascending=[False, False])
    df['Chart'] = "https://in.tradingview.com/chart/?symbol=NSE:" + df['RawStock'].astype(str)
    
    display_cols = ['Stock', 'Tag', 'Entry', 'EqSL', 'EqT1', 'EqT2', 'Score', 'RS_Rating', 'Vol vs 50d', 'RSI', 'Chart', 'RawStock']
    display_df = df[[col for col in display_cols if col in df.columns]]
    column_config = {
        "Chart": st.column_config.LinkColumn("📊 Chart", display_text="📈 View", help="Open directly in TradingView"),
        "Score": st.column_config.NumberColumn("Score /100", help="Institutional Composite Score", format="%d / 100"),
        "RS_Rating": st.column_config.TextColumn("RS vs Nifty", help="Relative Strength Percentile Outperformance"),
        "Entry": st.column_config.NumberColumn("CMP (₹)", format="₹%.2f"),
        "EqSL": st.column_config.NumberColumn("Stop Loss", format="₹%.2f"),
        "Vol vs 50d": st.column_config.NumberColumn("Vol Spike", format="%.1fx"),
        "RawStock": None 
    }
    st.dataframe(display_df, use_container_width=True, hide_index=True, column_config=column_config)
    st.write("")
    col1, col2 = st.columns([3, 1])
    with col1: selected_stock = st.selectbox(f"Select stock to track from {tab_name}:", df['RawStock'].unique(), key=f"sel_{tab_name}")
    with col2:
        st.write(""); st.write("") 
        if st.button(f"➕ Add to Portfolio", key=f"btn_{tab_name}", use_container_width=True): add_to_portfolio(selected_stock, df)

# --- MAIN UI DASHBOARD LAYOUT ---
st.title("📈 Apex Quant Engine")
st.markdown("Automated Stage 2 Trend & Volatility Contraction Scanner")

tabs = st.tabs([
    "💥 Pre-Breakout (VCP)", 
    "🌊 MACD Zero-Cross", 
    "📈 Swing Structural", 
    "🌙 High-Tight BTST", 
    "💼 Active Portfolio", 
    "📊 Advanced Charting"
])

df_all = load_data("all_setups.csv")

# --- POPULATE TABLES USING ROBUST TAG MATCHING ---
if not df_all.empty:
    # Match by Tag to ensure setups never get dropped due to Horizon naming mismatches
    df_macd = df_all[df_all['Tag'].str.contains('MACD', case=False, na=False)]
    df_pre = df_all[df_all['Tag'].str.contains('VCP|Pre-Breakout|Coil', case=False, na=False) | (df_all['Horizon'] == 'Pre-Breakout')]
    df_btst = df_all[df_all['Tag'].str.contains('BTST', case=False, na=False) | (df_all['Horizon'] == 'BTST')]
    df_swing = df_all[(df_all['Horizon'] == 'Swing') & (~df_all['Tag'].str.contains('MACD', case=False, na=False))]

    with tabs[0]: st.header("💥 Pre-Breakout & VCP Contraction"); display_interactive_table(df_pre, "Pre-Breakout")
    with tabs[1]: st.header("🌊 MACD Bullish Zero-Cross"); display_interactive_table(df_macd, "MACD Zero-Cross")
    with tabs[2]: st.header("📈 Stage 2 Swing Retests"); display_interactive_table(df_swing, "Swing Trades")
    with tabs[3]: st.header("🌙 High-Tight Institutional BTST"); display_interactive_table(df_btst, "BTST")
else:
    for i in range(4):
        with tabs[i]: st.info("Zero stocks passed the institutional quality filters today. Capital protected.")

# --- TAB 5: ACTIVE PORTFOLIO ---
with tabs[4]:
    st.header("Active Trailing Portfolio")
    df_portfolio = load_data("portfolio.csv")
    if not df_portfolio.empty:
        active_pf = df_portfolio[df_portfolio['Status'] == 'Active']
        st.subheader(f"🟢 Active Trades ({len(active_pf)})")
        if not active_pf.empty:
            for index, row in active_pf.iterrows():
                with st.container(border=True):
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"**{row['Stock']}** (Sector: {row.get('Sector', 'Unknown')}) | **Current SL: ₹{row['Current_SL']}**")
                        st.markdown(f"Entry: ₹{row['Entry']} | T1:₹{row['T1']} // T2:₹{row['T2']} // T3:₹{row['T3']}")
                    with col2:
                        if st.button("❌ Remove", key=f"remove_{row['RawStock']}_{index}"):
                            remove_from_portfolio(row['RawStock'])
                            st.rerun() 
        else: st.info("No active trades currently.")
    else: st.info("Your portfolio is currently empty.")

# --- TAB 6: NATIVE QUANTITATIVE CHARTING ENGINE ---
with tabs[5]:
    st.header("📊 Interactive Native Charting")
    df_charts = load_data("chart_data.csv")
    
    if df_charts.empty or df_all.empty:
        st.info("Chart data is currently building. Please wait for the next automated market scan.")
    else:
        chart_tickers = df_charts['Ticker'].unique().tolist()
        col1, col2 = st.columns([1, 2])
        with col1: selected_ticker = st.selectbox("Select Active Setup to Analyze:", chart_tickers)
        with col2: engine_choice = st.radio("Select Rendering Engine:", ["Lightweight Charts (Execution)", "Plotly (Deep Dive)"], horizontal=True)
        
        stock_df = df_charts[df_charts['Ticker'] == selected_ticker].copy()
        stock_df['Date'] = pd.to_datetime(stock_df['Date'])
        
        stock_df['EMA20'] = stock_df['Close'].ewm(span=20, adjust=False).mean()
        stock_df['EMA50'] = stock_df['Close'].ewm(span=50, adjust=False).mean()
        stock_df['EMA200'] = stock_df['Close'].ewm(span=200, adjust=False).mean()
        
        if engine_choice == "Lightweight Charts (Execution)":
            st.markdown(f"### {selected_ticker} (Execution View)")
            lw_candles = json.loads(stock_df[['Date', 'Open', 'High', 'Low', 'Close']].rename(columns={'Date':'time'}).to_json(orient='records'))
            lw_vol = json.loads(stock_df[['Date', 'Volume']].rename(columns={'Date':'time', 'Volume':'value'}).to_json(orient='records'))
            lw_ema20 = json.loads(stock_df[['Date', 'EMA20']].rename(columns={'Date':'time', 'EMA20':'value'}).to_json(orient='records'))
            lw_ema50 = json.loads(stock_df[['Date', 'EMA50']].rename(columns={'Date':'time', 'EMA50':'value'}).to_json(orient='records'))
            lw_ema200 = json.loads(stock_df[['Date', 'EMA200']].rename(columns={'Date':'time', 'EMA200':'value'}).to_json(orient='records'))
            
            for i, v in enumerate(lw_vol): v['color'] = 'rgba(38, 166, 154, 0.5)' if stock_df['Close'].iloc[i] >= stock_df['Open'].iloc[i] else 'rgba(239, 83, 80, 0.5)'
            
            chartOptions = {
                "height": 500,
                "layout": {"background": {"type": "solid", "color": "#131722"}, "textColor": "#d1d4dc"},
                "grid": {"vertLines": {"color": "#1f2933"}, "horzLines": {"color": "#1f2933"}},
                "crosshair": {"mode": 0},
                "timeScale": {"timeVisible": False, "borderColor": "#2b2b43"},
            }
            
            series = [
                {"type": "Candlestick", "data": lw_candles, "options": {"upColor": "#26a69a", "downColor": "#ef5350", "borderVisible": False, "wickUpColor": "#26a69a", "wickDownColor": "#ef5350"}},
                {"type": "Line", "data": lw_ema20, "options": {"color": "#00E5FF", "lineWidth": 2, "title": "20 EMA"}}, 
                {"type": "Line", "data": lw_ema50, "options": {"color": "#FFD700", "lineWidth": 2, "title": "50 EMA"}}, 
                {"type": "Line", "data": lw_ema200, "options": {"color": "#FF00FF", "lineWidth": 2, "title": "200 EMA"}}, 
                {"type": "Histogram", "data": lw_vol, "options": {"priceFormat": {"type": "volume"}, "priceScaleId": "", "scaleMargins": {"top": 0.85, "bottom": 0}}}
            ]
            renderLightweightCharts([{"chartOptions": chartOptions, "series": series}], 'chart')
            
        elif engine_choice == "Plotly (Deep Dive)":
            st.markdown(f"### {selected_ticker} (Statistical View)")
            
            delta = stock_df['Close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            stock_df['RSI'] = 100 - (100 / (1 + gain/loss))
            
            stock_df['EMA12'] = stock_df['Close'].ewm(span=12, adjust=False).mean()
            stock_df['EMA26'] = stock_df['Close'].ewm(span=26, adjust=False).mean()
            stock_df['MACD'] = stock_df['EMA12'] - stock_df['EMA26']
            stock_df['Signal'] = stock_df['MACD'].ewm(span=9, adjust=False).mean()
            stock_df['MACD_Hist'] = stock_df['MACD'] - stock_df['Signal']
            
            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.04, 
                row_heights=[0.6, 0.2, 0.2], 
                specs=[[{"secondary_y": True}], [{}], [{}]]
            )
            
            vol_colors = ['rgba(38, 166, 154, 0.4)' if c >= o else 'rgba(239, 83, 80, 0.4)' for c, o in zip(stock_df['Close'], stock_df['Open'])]
            fig.add_trace(go.Bar(x=stock_df['Date'], y=stock_df['Volume'], marker_color=vol_colors, name='Volume', showlegend=False), row=1, col=1, secondary_y=True)
            
            fig.add_trace(go.Candlestick(x=stock_df['Date'], open=stock_df['Open'], high=stock_df['High'], low=stock_df['Low'], close=stock_df['Close'], name='Price'), row=1, col=1, secondary_y=False)
            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['EMA20'], line=dict(color='#00E5FF', width=1.5), name='20 EMA'), row=1, col=1, secondary_y=False)
            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['EMA50'], line=dict(color='#FFD700', width=1.5), name='50 EMA'), row=1, col=1, secondary_y=False)
            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['EMA200'], line=dict(color='#FF00FF', width=1.5), name='200 EMA'), row=1, col=1, secondary_y=False)
            
            fig.update_yaxes(range=[0, stock_df['Volume'].max() * 4], showticklabels=False, showgrid=False, secondary_y=True, row=1, col=1)

            setup_info = df_all[df_all['RawStock'] == selected_ticker]
            if not setup_info.empty:
                sl_p = float(setup_info.iloc[0]['EqSL'])
                t1_p = float(setup_info.iloc[0]['EqT1'])
                tag_name = str(setup_info.iloc[0].get('Tag', ''))
                tl_d1 = setup_info.iloc[0].get('TL_D1')
                tl_v1 = setup_info.iloc[0].get('TL_V1')
                tl_v2 = setup_info.iloc[0].get('TL_V2')
                
                if pd.notna(tl_d1) and pd.notna(tl_v1) and pd.notna(tl_v2) and "Support" in tag_name:
                    last_date = stock_df['Date'].iloc[-1]
                    fig.add_trace(go.Scatter(x=[tl_d1, last_date], y=[float(tl_v1), float(tl_v2)], mode='lines', line=dict(color='#ffeb3b', width=2.5, dash='dashdot'), name='Trendline Support'), row=1, col=1, secondary_y=False)
                
                fig.add_hline(y=sl_p, line_dash="dash", row=1, col=1, secondary_y=False, line_color="rgba(255, 82, 82, 0.8)", annotation_text=f"SL: ₹{sl_p}", annotation_position="bottom right")
                fig.add_hline(y=t1_p, line_dash="dash", row=1, col=1, secondary_y=False, line_color="rgba(38, 166, 154, 0.8)", annotation_text=f"T1: ₹{t1_p}", annotation_position="top right")

            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['RSI'], line=dict(color='#00d1ff', width=1.5), name='RSI 14'), row=2, col=1)
            fig.add_hline(y=70, line_dash="dot", row=2, col=1, line_color="rgba(255, 82, 82, 0.5)")
            fig.add_hline(y=30, line_dash="dot", row=2, col=1, line_color="rgba(38, 166, 154, 0.5)")
            
            macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in stock_df['MACD_Hist']]
            fig.add_trace(go.Bar(x=stock_df['Date'], y=stock_df['MACD_Hist'], marker_color=macd_colors, name='MACD Histogram', showlegend=False), row=3, col=1)
            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['MACD'], line=dict(color='#2962FF', width=1.5), name='MACD Line'), row=3, col=1)
            fig.add_trace(go.Scatter(x=stock_df['Date'], y=stock_df['Signal'], line=dict(color='#FF6D00', width=1.5), name='Signal Line'), row=3, col=1)

            fig.update_layout(xaxis_rangeslider_visible=False, template="plotly_dark", height=850, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, use_container_width=True)

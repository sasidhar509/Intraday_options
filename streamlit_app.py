import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import pandas as pd
import asyncio
import requests
import streamlit as st
from dotenv import load_dotenv

from agents.classic_strategies import (
    add_indicators,
    build_candles,
    latest_levels,
    news_bias_from_headlines,
    now_label,
    strategy_recommendations,
    trendlines,
)
from agents.smartapi_live import SMARTAPI_AVAILABLE, SmartApiLiveAgent
from news_api_integration import render_news_radar_widget # Import the new news radar widget

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    go = None
    make_subplots = None
    PLOTLY_AVAILABLE = False

load_dotenv()

st.set_page_config(page_title="Angel One SmartAPI WebSocket Trading Dashboard", layout="wide")
st.title("Angel One SmartAPI WebSocket Trading Desk")


IST = timezone(timedelta(hours=5, minutes=30))


def market_status():
    now = datetime.now(IST)
    open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() >= 5:
        return "CLOSED", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Weekend"
    if open_time <= now <= close_time:
        return "OPEN", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Live exchange ticks should arrive."
    return "CLOSED", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Outside NSE/BSE regular market hours."


# Deprecating existing Google RSS news fetch
# @st.cache_data(ttl=60)
# def fetch_market_news(query):
#     rss_url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-IN&gl=IN&ceid=IN:en"
#     try:
#         response = requests.get(rss_url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
#         response.raise_for_status()
#         root = ET.fromstring(response.content)
#         items = []
#         for item in root.findall(".//item")[:8]:
#             title = item.findtext("title", default="").strip()
#             link = item.findtext("link", default="").strip()
#             published = item.findtext("pubDate", default="").strip()
#             if title:
#                 items.append({"title": title, "link": link, "published": published})
#         return {"status": "SUCCESS", "items": items, "source": rss_url, "timestamp": now_label()}
#     except Exception as exc:
#         return {"status": "ERROR", "items": [], "error": str(exc), "source": rss_url, "timestamp": now_label()}



@st.cache_data(ttl=60)
def fetch_all_market_news(query):
    import asyncio
    news = asyncio.run(get_latest_news_async())
    if not news:
        return {"status": "ERROR", "items": [], "error": "Failed to fetch news", "timestamp": now_label()}
    combined = []
    for article in news:
        combined.append({
            "title": article["title"],
            "description": article.get("description", ""),
            "link": article["link"],
            "published": article["publishedAt"],
            "sentiment_score": article.get("sentiment_score", 0),
            "source": article.get("source", "Unknown")
        })
    return {
        "status": "SUCCESS",
        "items": combined,
        "errors": [],
        "timestamp": now_label(),
    }
# Removed extra blank lines and duplicate function definitions to fix syntax error


def score_news_impact(title):
    text = title.lower()
    positive_terms = [
        "rally", "gain", "surge", "record", "beat", "strong", "growth", "upgrade", "inflow",
        "rate cut", "cuts rates", "lower inflation", "deal", "order win", "profit rises",
    ]
    negative_terms = [
        "fall", "drop", "selloff", "crash", "miss", "weak", "downgrade", "outflow", "war",
        "conflict", "sanction", "tariff", "inflation", "rate hike", "crude rises", "oil rises",
        "rupee falls", "probe", "ban", "loss widens",
    ]
    high_impact_terms = [
        "rbi", "fed", "inflation", "gdp", "crude", "oil", "war", "conflict", "rupee",
        "bond yield", "election", "budget", "sebi", "tariff",
    ]
    bank_terms = ["hdfc", "icici", "sbi", "axis", "kotak", "bank", "nbfc", "rbi"]
    it_terms = ["tcs", "infosys", "wipro", "hcl", "tech mahindra", "dollar", "nasdaq"]
    heavyweights = ["reliance", "hdfc", "icici", "tcs", "infosys", "larsen", "lt", "itc", "airtel"]

    score = 0
    drivers = []
    for term in positive_terms:
        if term in text:
            score += 1
            drivers.append(term)
    for term in negative_terms:
        if term in text:
            score -= 1
            drivers.append(term)

    impact = 1
    if any(term in text for term in high_impact_terms):
        impact += 1
    if any(term in text for term in bank_terms):
        impact += 1
    if any(term in text for term in heavyweights):
        impact += 1

    nifty_impact = max(-5, min(5, score * impact))
    sensex_impact = nifty_impact
    if any(term in text for term in bank_terms + it_terms + heavyweights):
        sensex_impact = max(-5, min(5, sensex_impact + (1 if score > 0 else -1 if score < 0 else 0)))

    if nifty_impact > 0:
        bias = "Bullish"
    elif nifty_impact < 0:
        bias = "Bearish"
    else:
        bias = "Neutral"

    if not drivers:
        drivers = ["watch"]
    return {
        "Bias": bias,
        "NIFTY Impact": nifty_impact,
        "SENSEX Impact": sensex_impact,
        "Drivers": ", ".join(drivers[:4]),
    }


def build_news_impact_table(news_items):
    rows = []
    for item in news_items:
        impact = score_news_impact(item["title"])
        rows.append(
            {
                "Headline": item["title"],
                "Brief Summary": item.get("description", "—"), # Include brief paragraph summary of the news
                "Bias": impact["Bias"],
                "NIFTY Impact": impact["NIFTY Impact"],
                "SENSEX Impact": impact["SENSEX Impact"],
                "Drivers": impact["Drivers"],
                "Published": item.get("published", ""),
                "Source Query": item.get("query", "Market news"),
                "Link": item.get("link", ""),
            }
        )
    return pd.DataFrame(rows)


def render_news_dashboard(news_query):
    st.subheader("Latest News & NIFTY/SENSEX Impact")
    news_result = fetch_all_market_news(news_query)
    if news_result["status"] == "ERROR" or not news_result["items"]:
        st.warning("News fetch failed or returned no items. Check internet access and try Refresh.")
        if news_result.get("errors"):
            with st.expander("News Fetch Errors", expanded=False):
                st.json(news_result["errors"])
        return news_result, "NEUTRAL"

    impact_df = build_news_impact_table(news_result["items"])
    news_bias = news_bias_from_headlines(impact_df["Headline"].tolist())
    nifty_total = int(impact_df["NIFTY Impact"].sum())
    sensex_total = int(impact_df["SENSEX Impact"].sum())

    cols = st.columns(4)
    cols[0].metric("News Bias", news_bias)
    cols[1].metric("NIFTY News Score", nifty_total)
    cols[2].metric("SENSEX News Score", sensex_total)
    cols[3].metric("News Refreshed", news_result["timestamp"].split(" ")[1])

    # Display links directly inside the main dataframe using column configuration
    st.data_editor(
        impact_df,
        column_config={
            "Link": st.column_config.LinkColumn("News Link", help="Click to open the news article source", display_text="Open Article")
        },
        disabled=True,
        use_container_width=True,
        hide_index=True
    )

    with st.expander("Open News Links", expanded=False):
        for _, row in impact_df.iterrows():
            st.markdown(f"- [{row['Headline']}]({row['Link']})")

    return news_result, news_bias


def build_trading_chart(candles, symbol):
    lines = trendlines(candles)
    chart_df = candles.copy()
    x_values = chart_df["timestamp"]
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.05,
        row_heights=[0.72, 0.28],
    )
    fig.add_trace(
        go.Candlestick(
            x=x_values,
            open=chart_df["open"],
            high=chart_df["high"],
            low=chart_df["low"],
            close=chart_df["close"],
            name=symbol,
        ),
        row=1,
        col=1,
    )
    for column, color in [("ema_9", "#2563eb"), ("ema_20", "#f59e0b"), ("vwap", "#7c3aed")]:
        if column in chart_df:
            fig.add_trace(go.Scatter(x=x_values, y=chart_df[column], mode="lines", name=column.upper(), line={"width": 1.4, "color": color}), row=1, col=1)

    for column, color, dash in [
        ("support", "#16a34a", "dot"),
        ("resistance", "#dc2626", "dot"),
        ("s1", "#22c55e", "dash"),
        ("r1", "#ef4444", "dash"),
    ]:
        value = chart_df[column].dropna().iloc[-1] if column in chart_df and not chart_df[column].dropna().empty else None
        if value is not None:
            fig.add_hline(y=float(value), line_dash=dash, line_color=color, annotation_text=column.upper(), row=1, col=1)

    if lines["support_line"] is not None and lines["resistance_line"] is not None:
        fig.add_hline(y=lines["support_line"], line_dash="longdash", line_color="#059669", annotation_text="Trend Support", row=1, col=1)
        fig.add_hline(y=lines["resistance_line"], line_dash="longdash", line_color="#b91c1c", annotation_text="Trend Resistance", row=1, col=1)

    fig.add_trace(go.Scatter(x=x_values, y=chart_df["rsi_14"], mode="lines", name="RSI 14", line={"color": "#334155"}), row=2, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#dc2626", row=2, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#16a34a", row=2, col=1)
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=2, col=1)
    fig.update_layout(
        height=680,
        margin={"l": 10, "r": 10, "t": 30, "b": 10},
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
    )
    return fig

with st.expander("How to use this dashboard", expanded=True):
    st.markdown(
        """
        1. Enter your Angel One credentials in the sidebar (Client ID, PIN, TOTP Secret).
        2. Click 'Login & Start WebSocket' to authenticate and subscribe through SmartAPI WebSocket.
        3. Track indices with named SmartAPI tokens, e.g. `NIFTY 50=1:99926000,SENSEX=3:99919000`.
        4. Choose a candle interval and keep live auto refresh enabled for fast redraws.
        5. The dashboard shows candlesticks, RSI, trend/support/resistance, strategy rows, and CE/PE setups.
        """
    )

with st.sidebar:
    st.header("Angel One SmartAPI WebSocket")
    api_key = st.text_input("API Developer Key", value=os.getenv("ANGEL_API_KEY", os.getenv("SMARTAPI_API_KEY", "")), type="password")
    client_code = st.text_input("Client ID (Mobile/Email)", value=os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE", "")))
    pin = st.text_input("Angel One PIN", value=os.getenv("ANGEL_PIN", os.getenv("SMARTAPI_PIN", "")), type="password", help="Your 4-digit Angel One PIN")
    totp_secret = st.text_input("16-Digit TOTP Secret Key", value=os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET", "")), type="password")
    token_list = st.text_input(
        "Token List",
        value=os.getenv("SMARTAPI_TOKEN_LIST", "NIFTY 50=1:99926000,SENSEX=3:99919000"),
        help="Comma-separated entries like NIFTY 50=1:99926000,SENSEX=3:99919000. Token is the broker's instrument ID.",
    )
    subscription_mode = st.selectbox(
        "Subscription Mode",
        options={"LTP": 1, "Quote": 2, "Snap Quote": 3, "Depth": 4},
        index=0,
    )
    candle_interval = st.selectbox("Candle Interval", ["5s", "15s", "30s", "1min", "3min", "5min"], index=1)
    auto_refresh = st.checkbox("Live auto refresh", value=True)
    refresh_seconds = st.number_input("Refresh seconds", min_value=1, max_value=10, value=1, step=1)
    news_query = st.text_input("News Query", value=os.getenv("MARKET_NEWS_QUERY", "Nifty Sensex Indian stock market options latest news"))
    st.markdown("**Step 1:** Authenticate with your PIN + TOTP and start the WebSocket stream")
    complete_session = st.button("Login & Start WebSocket")

if "agent" not in st.session_state:
    st.session_state.agent = None
    st.session_state.last_connect_error = None
    st.session_state.connected_at = None
    st.session_state.logged_in = False

status_container = st.container()
metrics_container = st.container()
stream_container = st.container()

render_news_radar_widget()
news_result = None
global_news_bias = None


if complete_session:
    if not api_key or not client_code or not pin or not totp_secret:
        st.error("❌ Please fill in all credentials: API Key, Client ID, PIN, and TOTP Secret.")
    elif not SMARTAPI_AVAILABLE:
        st.error("SmartAPI package is not available. Install dependencies with `pip install -r requirements.txt`.")
    else:
        try:
            st.session_state.agent = SmartApiLiveAgent(
                api_key=api_key,
                client_code=client_code,
                pin=pin,
                totp_secret=totp_secret,
                token_list=token_list,
                mode=subscription_mode,
            )
            st.session_state.agent.login_with_pin(pin)
            st.session_state.agent.start()
            st.session_state.connected_at = datetime.utcnow()
            st.session_state.last_connect_error = None
            st.session_state.logged_in = True
            st.success("✅ Angel One SmartAPI WebSocket authenticated. Streaming live ticks...")
        except Exception as e:
            st.session_state.agent = None
            st.session_state.logged_in = False
            st.session_state.last_connect_error = str(e)
            st.error(f"❌ WebSocket login/start failed: {str(e)}")

if st.session_state.last_connect_error:
    st.error(f"Connection failed: {st.session_state.last_connect_error}")

if st.session_state.agent is None:
    st.warning("No active WebSocket session. Enter credentials and click 'Login & Start WebSocket' to begin.")
elif not st.session_state.logged_in:
    st.info("Click 'Login & Start WebSocket' to authenticate with Angel One SmartAPI.")
else:
    if st.session_state.agent.session_ready:
        st.success("✅ Angel One SmartAPI WebSocket session is active. Streaming live data...")
        if st.session_state.connected_at:
            st.info(f"Connected at: {st.session_state.connected_at.strftime('%H:%M:%S UTC')}")

        st_autorefresh = st.empty()
        st_autorefresh.button("Refresh Now")

        market_state, market_time, market_note = market_status()
        if market_state == "OPEN":
            st.success(f"Market status: OPEN | {market_time} | {market_note}")
        else:
            st.warning(f"Market status: CLOSED | {market_time} | {market_note}")

        state = st.session_state.agent.get_state()
        connected = state.pop("connected", False)
        last_error = state.pop("last_error", None)
        last_message = state.pop("last_message", None)
        last_subscribe_payload = state.pop("last_subscribe_payload", None)
        raw_message_count = state.pop("raw_message_count", 0)

        if last_error:
            st.warning(f"Last live feed error: {last_error}")

        if not connected:
            st.error("Live feed is not connected yet. Waiting for market data...")
        elif raw_message_count == 0:
            st.warning("WebSocket is connected, but no market-data messages have arrived yet.")

        rows = []
        for token, data in state.items():
            rows.append(
        {
            "Token": token,
            "Label": data.get("label"),
            "Last Price": data.get("last_price", data.get("ltp")),
            "Ticks": data.get("tick_count", 0),
            "EMA 9": data.get("ema_9"),
            "VWAP": data.get("vwap"),
            "Buyers Ratio": f"{data.get('buyers_ratio')}%" if data.get("buyers_ratio") is not None else "—",
            "Signal": data.get("signal"),
            "Probability": f"{data.get('probability')}%" if data.get("probability") is not None else "—",
            "Updated": data.get("timestamp").strftime("%H:%M:%S") if data.get("timestamp") else "—",
        }
    )

        df = pd.DataFrame(rows)

        with metrics_container:
            st.subheader("Live Feed Summary")
            st.dataframe(df, use_container_width=True)

        st.subheader("Candlestick Strategy Dashboard")
        if not PLOTLY_AVAILABLE:
            st.error("Plotly is not installed. Run `pip install -r requirements.txt`, then restart Streamlit.")
        elif state:
            token_options = list(state.keys())
            selected_token = st.selectbox("Index Chart", token_options, format_func=lambda token: f"{state[token].get('label') or state[token].get('symbol') or token}")
            history = st.session_state.agent.get_price_history(selected_token) if hasattr(st.session_state.agent, "get_price_history") else []
            candles = add_indicators(build_candles(history, candle_interval))
            selected_state = state.get(selected_token, {})

            if candles.empty:
                tick_count = selected_state.get("tick_count", 0)
                st.info(
                    f"Waiting for live ticks to build candles. Current tick count for "
                    f"{selected_state.get('label') or selected_token}: {tick_count}."
                )
                if connected and tick_count == 0:
                    st.warning(
                        "WebSocket is connected, but no ticks have arrived for this token yet. "
                        "Check that the token/exchange pair is valid and the market feed is active."
                    )
                    with st.expander("WebSocket Subscription Debug", expanded=True):
                        st.write("Raw message count:", raw_message_count)
                        st.json(last_subscribe_payload or {})
                        st.write("Last websocket message:")
                        st.json(last_message or {})
            else:
                latest = candles.iloc[-1]
                news_bias = global_news_bias
                buyers_ratio = selected_state.get("buyers_ratio")
                levels = latest_levels(candles)
                recommendations = strategy_recommendations(
                    candles,
                    buyers_ratio=buyers_ratio,
                    news_bias=news_bias,
                    underlying=selected_state.get("label") or selected_state.get("symbol") or selected_token,
                )

                metric_cols = st.columns(7)
                metric_cols[0].metric("Trend", levels.get("trend", "NEUTRAL"))
                metric_cols[1].metric("RSI 14", f"{latest.get('rsi_14', 0):.2f}" if pd.notna(latest.get("rsi_14")) else "n/a")
                metric_cols[2].metric("Support", levels.get("support", "n/a"))
                metric_cols[3].metric("Resistance", levels.get("resistance", "n/a"))
                metric_cols[4].metric("VWAP", f"{latest.get('vwap', 0):.2f}" if pd.notna(latest.get("vwap")) else "n/a")
                metric_cols[5].metric("News Bias", news_bias)
                updated = selected_state.get("timestamp")
                delay_text = f"{(datetime.utcnow() - updated).total_seconds():.1f}s" if updated else "n/a"
                metric_cols[6].metric("Feed Delay", delay_text)

                st.plotly_chart(build_trading_chart(candles, selected_token), use_container_width=True)

                left, right = st.columns([2, 1])
                with left:
                    st.subheader("Recommended Strategies")
                    visible_columns = ["Strategy", "Bias", "Option", "Action", "Entry", "Target", "Stop", "Confidence", "Reason"]
                    if recommendations.empty:
                        st.info("Waiting for more candles before strategy recommendations are available.")
                    else:
                        st.dataframe(recommendations[visible_columns], use_container_width=True, hide_index=True)
                        top = recommendations.iloc[0]
                        if top["Option"] != "WAIT":
                            st.success(
                                f"Top setup: {top['Option']} | Entry {top['Entry']} | Target {top['Target']} | SL {top['Stop']} | Confidence {top['Confidence']}"
                            )

                with right:
                    st.subheader("Levels & Pattern")
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {"Item": "Pivot", "Value": levels.get("pivot")},
                                {"Item": "Support S1", "Value": levels.get("s1")},
                                {"Item": "Support S2", "Value": levels.get("s2")},
                                {"Item": "Resistance R1", "Value": levels.get("r1")},
                                {"Item": "Resistance R2", "Value": levels.get("r2")},
                                {"Item": "Trendline Support", "Value": levels.get("support_line")},
                                {"Item": "Trendline Resistance", "Value": levels.get("resistance_line")},
                                {"Item": "Candle Formation", "Value": latest.get("candle_pattern")},
                            ]
                        ),
                        use_container_width=True,
                        hide_index=True,
                    )
        else:
            st.info("No symbols available yet. Connect and wait for live data.")

        with stream_container.expander("Raw Stream State & Depth Data", expanded=False):
            st.markdown("### WebSocket Diagnostics")
            st.json(
                {
                    "connected": connected,
                    "raw_message_count": raw_message_count,
                    "last_error": last_error,
                    "last_subscribe_payload": last_subscribe_payload,
                    "last_message": last_message,
                }
            )
            for token, data in state.items():
                st.markdown(f"### {token} — {data.get('label')}")
                st.json({
                    "last_price": data.get("last_price", data.get("ltp")),
                    "ema_9": data.get("ema_9"),
                    "vwap": data.get("vwap"),
                    "buyers_ratio": data.get("buyers_ratio"),
                    "depth": data.get("depth", {}),
                    "signal": data.get("signal"),
                    "probability": data.get("probability"),
                })

        st.caption("Use Ctrl+R in the browser or the Refresh Now button for an immediate redraw.")
        if auto_refresh:
            time.sleep(refresh_seconds)
            st.rerun()
    else:
        st.info("SMS OTP sent. Enter the code and click 'Complete Session with SMS OTP' to start streaming.")

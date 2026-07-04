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
from news_api_integration import render_news_radar_widget, fetch_ui_news_data_matrix

# ── PATCH 1/2/3: import the new strategy panel and brain instance
from patch_streamlit_strategy import render_strategy_panel
from agents.brain import StrategyBrainEngine
_brain = StrategyBrainEngine()   # single shared instance for the whole app

# ── Backtest imports (lazy — only loaded when user clicks Run Backtest)
try:
    from agents.backtest_runner import run_backtest_and_module
    BACKTEST_AVAILABLE = True
except Exception:
    BACKTEST_AVAILABLE = False

# ── GHOST strategy engine (single best strategy — SMC + liquidity sweep)
try:
    from agents.ghost_strategy import GhostStrategyEngine, render_ghost_panel, DailyRiskGuard
    GHOST_AVAILABLE = True
except Exception:
    GHOST_AVAILABLE = False

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
    open_time  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() >= 5:
        return "CLOSED", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Weekend"
    if open_time <= now <= close_time:
        return "OPEN", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Live exchange ticks should arrive."
    return "CLOSED", now.strftime("%Y-%m-%d %H:%M:%S IST"), "Outside NSE/BSE regular market hours."


@st.cache_data(ttl=60)
def fetch_all_market_news(query=None):
    import asyncio
    from news_api_integration import AsyncGuruNewsEngine
    engine = AsyncGuruNewsEngine()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        data = loop.run_until_complete(engine.execute_async_gather())
        if not data:
            return {"status": "ERROR", "items": [], "error": "Failed to fetch news", "timestamp": now_label()}
        combined = []
        for article in data:
            combined.append({
                "title":           article["title"],
                "description":     article.get("description", ""),
                "link":            article["link"],
                "published":       article["publishedAt"],
                "sentiment_score": article.get("sentiment_score", 0),
                "source":          article.get("source", "Unknown"),
            })
        return {
            "status":    "SUCCESS",
            "items":     combined,
            "errors":    [],
            "timestamp": now_label(),
        }
    except Exception as exc:
        return {"status": "ERROR", "items": [], "error": str(exc), "timestamp": now_label()}


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
    bank_terms      = ["hdfc", "icici", "sbi", "axis", "kotak", "bank", "nbfc", "rbi"]
    it_terms        = ["tcs", "infosys", "wipro", "hcl", "tech mahindra", "dollar", "nasdaq"]
    heavyweights    = ["reliance", "hdfc", "icici", "tcs", "infosys", "larsen", "lt", "itc", "airtel"]

    score   = 0
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

    nifty_impact  = max(-5, min(5, score * impact))
    sensex_impact = nifty_impact
    if any(term in text for term in bank_terms + it_terms + heavyweights):
        sensex_impact = max(-5, min(5, sensex_impact + (1 if score > 0 else -1 if score < 0 else 0)))

    bias = "Bullish" if nifty_impact > 0 else "Bearish" if nifty_impact < 0 else "Neutral"
    if not drivers:
        drivers = ["watch"]
    return {
        "Bias":           bias,
        "NIFTY Impact":   nifty_impact,
        "SENSEX Impact":  sensex_impact,
        "Drivers":        ", ".join(drivers[:4]),
    }


def build_news_impact_table(news_items):
    rows = []
    for item in news_items:
        impact = score_news_impact(item["title"])
        rows.append({
            "Headline":      item["title"],
            "Brief Summary": item.get("description", "—"),
            "Bias":          impact["Bias"],
            "NIFTY Impact":  impact["NIFTY Impact"],
            "SENSEX Impact": impact["SENSEX Impact"],
            "Drivers":       impact["Drivers"],
            "Published":     item.get("published", ""),
            "Source Query":  item.get("query", "Market news"),
            "Link":          item.get("link", ""),
        })
    return pd.DataFrame(rows)


def render_news_dashboard(news_query):
    st.subheader("Latest News & NIFTY/SENSEX Impact")
    news_result = fetch_all_market_news(news_query)

    if news_result["status"] == "ERROR" or not news_result["items"]:
        st.warning("News fetch failed or returned no items. Check internet access and try Refresh.")
        if news_result.get("errors"):
            with st.expander("News Fetch Errors", expanded=False):
                st.json(news_result["errors"])
        # ── PATCH 2 (Edit 4): cache whatever we got so panel always has data
        st.session_state["last_news_result"] = news_result
        return news_result, "NEUTRAL"

    impact_df  = build_news_impact_table(news_result["items"])
    news_bias  = news_bias_from_headlines(impact_df["Headline"].tolist())
    nifty_total  = int(impact_df["NIFTY Impact"].sum())
    sensex_total = int(impact_df["SENSEX Impact"].sum())

    cols = st.columns(4)
    cols[0].metric("News Bias",        news_bias)
    cols[1].metric("NIFTY News Score", nifty_total)
    cols[2].metric("SENSEX News Score",sensex_total)
    cols[3].metric("News Refreshed",   news_result["timestamp"].split(" ")[1])

    st.data_editor(
        impact_df,
        column_config={
            "Link": st.column_config.LinkColumn(
                "News Link",
                help="Click to open the news article source",
                display_text="Open Article",
            )
        },
        disabled=True,
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("Open News Links", expanded=False):
        for _, row in impact_df.iterrows():
            st.markdown(f"- [{row['Headline']}]({row['Link']})")

    # ── PATCH 2 (Edit 4): persist to session_state so strategy panel
    #    always has a valid news_result even on partial refresh cycles
    st.session_state["last_news_result"] = news_result

    return news_result, news_bias


def build_trading_chart(candles, symbol, ghost_signal=None,
                        show_ema=False, show_vwap=False, show_sr=False):
    """
    Clean Kite-style candlestick chart.
    - White background, Kite teal/red candles
    - Volume bars below (replaces RSI subplot)
    - Optional EMA/VWAP/S-R toggles (off by default)
    - GHOST overlays: PDH/PDL, OB zone, Entry/SL/T1/T2/T3 lines
    """
    df = candles.copy()
    xs = df["timestamp"]

    KITE_UP   = "#26a69a"
    KITE_DOWN = "#ef5350"
    GRID_CLR  = "rgba(0,0,0,0.06)"
    FONT_CLR  = "#131722"

    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.78, 0.22],
    )

    # Candlestick
    fig.add_trace(
        go.Candlestick(
            x=xs,
            open=df["open"], high=df["high"],
            low=df["low"],   close=df["close"],
            name=symbol,
            increasing=dict(line=dict(color=KITE_UP,   width=1), fillcolor=KITE_UP),
            decreasing=dict(line=dict(color=KITE_DOWN, width=1), fillcolor=KITE_DOWN),
            whiskerwidth=0,
            showlegend=False,
        ),
        row=1, col=1,
    )

    # Volume bars
    vol_col = "volume" if "volume" in df.columns else "Volume" if "Volume" in df.columns else None
    if vol_col:
        bar_colors = [KITE_UP if c >= o else KITE_DOWN
                      for c, o in zip(df["close"], df["open"])]
        fig.add_trace(
            go.Bar(x=xs, y=df[vol_col], name="Volume",
                   marker_color=bar_colors, marker_line_width=0,
                   opacity=0.55, showlegend=False),
            row=2, col=1,
        )

    # Optional: EMA lines
    if show_ema:
        for col, color, label in [("ema_9","#2563eb","EMA 9"),("ema_20","#f59e0b","EMA 20")]:
            if col in df.columns:
                fig.add_trace(go.Scatter(x=xs, y=df[col], mode="lines", name=label,
                    line=dict(width=1.2, color=color)), row=1, col=1)

    # Optional: VWAP
    if show_vwap and "vwap" in df.columns:
        fig.add_trace(go.Scatter(x=xs, y=df["vwap"], mode="lines", name="VWAP",
            line=dict(width=1.2, color="#7c3aed", dash="dot")), row=1, col=1)

    # Optional: Support / Resistance
    if show_sr:
        for col, color, dash in [("support","#16a34a","dot"),("resistance","#dc2626","dot")]:
            val = df[col].dropna().iloc[-1] if col in df.columns and not df[col].dropna().empty else None
            if val:
                fig.add_hline(y=float(val), line_dash=dash, line_color=color, line_width=1,
                    annotation_text=col.upper(), annotation_font_size=10, row=1, col=1)

    # GHOST overlays
    if ghost_signal is not None:
        sig = ghost_signal

        # PDH / PDL — thin dashed grey
        for level, label in [(sig.pdh, "PDH"), (sig.pdl, "PDL")]:
            if level:
                fig.add_hline(y=level, line_dash="dash", line_color="#90a4ae", line_width=1,
                    annotation_text=f"{label} {level:.0f}", annotation_font_size=10,
                    annotation_font_color="#546e7a", annotation_position="right", row=1, col=1)

        # VWAP line from signal
        if sig.vwap and sig.vwap > 0:
            vwap_color = "#7c3aed"
            fig.add_hline(y=sig.vwap, line_dash="dash", line_color=vwap_color, line_width=1.2,
                annotation_text=f"VWAP {sig.vwap:.0f}",
                annotation_font_size=10, annotation_font_color=vwap_color,
                annotation_position="right", row=1, col=1)

        # OB zone — amber shading
        if sig.ob_high and sig.ob_low and sig.ob_high > 0:
            fig.add_hrect(y0=sig.ob_low, y1=sig.ob_high,
                fillcolor="rgba(255,152,0,0.10)", line_width=0.8,
                line_color="rgba(255,152,0,0.5)",
                annotation_text="OB", annotation_position="right",
                annotation_font_size=10, annotation_font_color="#e65100",
                row=1, col=1)

        # Entry / SL / Targets — only when actionable
        if sig.actionable:
            entry_idx = getattr(sig, "entry_idx", None) or sig.trap_level
            sl_idx    = getattr(sig, "sl_idx", None)
            t1_idx    = getattr(sig, "t1_idx", None)
            t2_idx    = getattr(sig, "t2_idx", None)
            t3_idx    = getattr(sig, "t3_idx", None)

            if entry_idx:
                fig.add_hline(y=entry_idx, line_dash="solid", line_color="#1565c0", line_width=1.8,
                    annotation_text=f"Entry {entry_idx:.0f}", annotation_font_size=10,
                    annotation_font_color="#1565c0", annotation_position="right", row=1, col=1)
            if sl_idx:
                fig.add_hline(y=sl_idx, line_dash="dash", line_color="#c62828", line_width=1.5,
                    annotation_text=f"SL {sl_idx:.0f}", annotation_font_size=10,
                    annotation_font_color="#c62828", annotation_position="right", row=1, col=1)
            for level, label, color in [
                (t1_idx, f"T1 {t1_idx:.0f}  ({getattr(sig,'rr_t1','1:2')})" if t1_idx else None, "#2e7d32"),
                (t2_idx, f"T2 {t2_idx:.0f}  ({getattr(sig,'rr_t2','1:3')})" if t2_idx else None, "#1b5e20"),
                (t3_idx, f"T3 {t3_idx:.0f}  ({getattr(sig,'rr_t3','1:5')})" if t3_idx else None, "#003300"),
            ]:
                if level and label:
                    fig.add_hline(y=level, line_dash="dot", line_color=color, line_width=1.2,
                        annotation_text=label, annotation_font_size=10,
                        annotation_font_color=color, annotation_position="right", row=1, col=1)

    # Layout: clean Kite style
    fig.update_layout(
        height=540,
        paper_bgcolor="#ffffff",
        plot_bgcolor="#ffffff",
        font=dict(family="Inter, -apple-system, sans-serif", size=11, color=FONT_CLR),
        margin=dict(l=0, r=95, t=28, b=0),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
                    font=dict(size=10), bgcolor="rgba(255,255,255,0)"),
    )
    fig.update_xaxes(showgrid=True, gridcolor=GRID_CLR, gridwidth=1,
                     showline=True, linecolor="#e0e0e0", zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID_CLR, gridwidth=1,
                     showline=True, linecolor="#e0e0e0",
                     side="right", zeroline=False, tickformat=",.0f")
    fig.update_yaxes(row=2, col=1, tickformat=".2s",
                     title_text="Vol", title_font_size=10, automargin=True)
    return fig



# ─────────────────────────────────────────────────────────────
# Backtest section renderer
# ─────────────────────────────────────────────────────────────
def _render_backtest_section(st_module) -> None:
    """
    Renders the ▶ Run Backtest button and results panel.
    Runs in a background thread so it never blocks the live feed.
    """
    import threading

    # Session state init
    for key, default in [
        ("bt_running", False), ("bt_status", "Idle"),
        ("bt_log", []),        ("bt_result", None),
        ("bt_error",  None),
    ]:
        if key not in st.session_state:
            st.session_state[key] = default

    st.markdown("---")
    st.subheader("📊 Strategy Backtest — NIFTY & BANKNIFTY")

    if not BACKTEST_AVAILABLE:
        st.warning(
            "backtest_engine.py not found at agents/backtest_engine.py. "
            "Copy agents/backtest_engine.py into your agents/ folder."
        )
        return

    col_cfg1, col_cfg2, col_cfg3 = st.columns(3)
    bt_interval = col_cfg1.selectbox(
        "Backtest interval", ["1min", "5min", "15min", "1day"],
        index=2, key="bt_interval"
    )
    bt_start = col_cfg2.text_input("Start date", "2024-06-01", key="bt_start")
    bt_end   = col_cfg3.text_input("End date",   "2025-06-01", key="bt_end")

    col_run, col_status = st.columns([1, 3])
    with col_run:
        run_clicked = st.button(
            "▶ Run Backtest",
            disabled=bool(st.session_state.get("bt_running")),
        )

    with col_status:
        status_icon = {
            "Idle":     "⚪",
            "Started":  "🔄",
            "Finished": "✅",
            "Failed":   "❌",
        }.get(st.session_state.get("bt_status", "Idle"), "⚪")
        st.markdown(
            "**Status:** {} {}".format(
                status_icon, st.session_state.get("bt_status", "Idle")
            )
        )

    if run_clicked and not st.session_state.get("bt_running"):
        def _worker():
            try:
                st.session_state["bt_running"] = True
                st.session_state["bt_status"]  = "Started"
                st.session_state["bt_log"]     = ["▶ Backtest started…"]
                st.session_state["bt_error"]   = None

                _, res = run_backtest_and_module(
                    instruments=["NIFTY", "BANKNIFTY"],
                    interval=st.session_state.get("bt_interval", "15min"),
                    start=st.session_state.get("bt_start", "2024-06-01"),
                    end=st.session_state.get("bt_end",   "2025-06-01"),
                )
                st.session_state["bt_result"] = res
                st.session_state["bt_status"] = "Finished"
                st.session_state["bt_log"].append("✅ Backtest complete.")
            except Exception as err:
                st.session_state["bt_error"]  = str(err)
                st.session_state["bt_status"] = "Failed"
                st.session_state["bt_log"].append("❌ Error: {}".format(err))
            finally:
                st.session_state["bt_running"] = False

        threading.Thread(target=_worker, daemon=True).start()
        st.info("Backtest running in background — refresh panel to see results.")

    # Log
    if st.session_state.get("bt_log"):
        with st.expander("Backtest log", expanded=False):
            for line in st.session_state["bt_log"][-30:]:
                st.text(line)

    # Error
    if st.session_state.get("bt_error"):
        st.error("Backtest error: {}".format(st.session_state["bt_error"]))

    # Results
    if st.session_state.get("bt_result") is not None:
        try:
            from agents.backtest_engine import render_backtest_panel
            render_backtest_panel(st.session_state["bt_result"])
        except Exception as e:
            st.warning("Could not render backtest panel: {}".format(e))
            # Fallback: show raw summary dict
            res = st.session_state["bt_result"]
            results_dict = res.get("results", {})
            for key, val in results_dict.items():
                summ = val.get("summary", {})
                if summ and summ.get("total_trades", 0) > 0:
                    st.markdown("**{}** — Win: {} | Net P&L: {} | Prob: {}".format(
                        key,
                        summ.get("win_rate", "—"),
                        summ.get("net_pnl",  "—"),
                        summ.get("probability", "—"),
                    ))


# ─────────────────────────────────────────────────────────────
# GHOST strategy section renderer
# ─────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def _fetch_prev_day_levels(underlying: str):
    """
    Fetch previous trading day's High/Low for PDH/PDL.
    Uses yfinance ^NSEI / ^NSEBANK as a reliable daily-bar source.
    Cached 5 minutes — PDH/PDL don't change intraday.
    """
    import yfinance as yf
    ticker_map = {
        "NIFTY":     "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "SENSEX":    "^BSESN",
    }
    cu = "NIFTY"
    u  = (underlying or "NIFTY").upper()
    if "BANK" in u:
        cu = "BANKNIFTY"
    elif "SENSEX" in u:
        cu = "SENSEX"

    try:
        hist = yf.Ticker(ticker_map.get(cu, "^NSEI")).history(period="5d")
        if len(hist) >= 2:
            prev = hist.iloc[-2]
            return float(prev["High"]), float(prev["Low"]), cu
    except Exception:
        pass

    # Fallback approximate levels (June 2026 range)
    fallback = {
        "NIFTY":     (24850.0, 24550.0),
        "BANKNIFTY": (54850.0, 53900.0),
        "SENSEX":    (81500.0, 80800.0),
    }
    pdh, pdl = fallback.get(cu, (24850.0, 24550.0))
    return pdh, pdl, cu


def _compute_ghost_signal(candles, selected_state, underlying, news_bias):
    """
    Pure computation — no Streamlit calls. Returns (signal, instrument, pdh, pdl).
    Called before chart render so the signal can be overlaid on the chart.
    Returns None signal if preconditions not met.
    """
    if not GHOST_AVAILABLE:
        return None, None, None, None

    agent = st.session_state.get("agent")
    if agent is None or not hasattr(agent, "get_price_history"):
        return None, None, None, None

    u = (underlying or "NIFTY").upper()
    instrument = "BANKNIFTY" if "BANK" in u else "SENSEX" if "SENSEX" in u else "NIFTY"

    token = None
    for tok, label in getattr(agent, "live_state", {}).items():
        lbl = (label.get("label") or label.get("symbol") or "").upper()
        if instrument in lbl.replace(" ", ""):
            token = tok
            break
    if token is None and agent.live_state:
        token = list(agent.live_state.keys())[0]
    if token is None:
        return None, instrument, None, None

    history = agent.get_price_history(token)
    if not history or len(history) < 30:
        return None, instrument, None, None

    df15 = build_candles(history, "15min")
    df5  = build_candles(history, "5min")
    if df15.empty or df5.empty or len(df15) < 2 or len(df5) < 2:
        return None, instrument, None, None

    def _to_ohlcv(df):
        return df.rename(columns={
            "open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"
        }).set_index("timestamp")[["Open","High","Low","Close","Volume"]]

    df15_ohlc = _to_ohlcv(df15)
    df5_ohlc  = _to_ohlcv(df5)
    pdh, pdl, _ = _fetch_prev_day_levels(underlying)
    ltp = float(
        selected_state.get("last_price") or selected_state.get("ltp")
        or df5_ohlc["Close"].iloc[-1]
    )

    buyers_ratio = selected_state.get("buyers_ratio")
    engine = GhostStrategyEngine(instrument=instrument)
    signal = engine.evaluate(
        df_15min=df15_ohlc, df_5min=df5_ohlc,
        prev_day_high=pdh, prev_day_low=pdl,
        current_ltp=ltp, news_bias=news_bias,
        buyers_ratio=float(buyers_ratio) if buyers_ratio is not None else None,
    )
    return signal, instrument, pdh, pdl


def _render_ghost_section(st_module, candles, selected_state, underlying, news_bias):
    """
    Renders the GHOST strategy panel including:
    - Diagnostic "why no trade" panel when waiting
    - Market depth confluence badge
    - DailyRiskGuard discipline enforcer
    - Signal details via render_ghost_panel()
    """
    st.markdown("---")
    st.subheader("👻 GHOST — Single Best Strategy")
    st.caption(
        "Liquidity Sweep → Order Block → FVG → 5-min Confirmation. "
        "SMC + psychology filters. PDH/PDL sweep trap reversal."
    )

    if not GHOST_AVAILABLE:
        st.info("ghost_strategy.py not found — check agents/ folder.")
        return

    signal, instrument, pdh, pdl = st.session_state.get("_ghost_signal_cache", (None,None,None,None))

    if signal is None:
        agent = st.session_state.get("agent")
        if agent is None or not hasattr(agent, "get_price_history"):
            st.info("Connect WebSocket to enable GHOST live signals.")
            return

        u = (underlying or "NIFTY").upper()
        instrument = "BANKNIFTY" if "BANK" in u else "SENSEX" if "SENSEX" in u else "NIFTY"
        token = None
        for tok, label in getattr(agent, "live_state", {}).items():
            lbl = (label.get("label") or label.get("symbol") or "").upper()
            if instrument in lbl.replace(" ", ""):
                token = tok
                break
        if token is None and agent.live_state:
            token = list(agent.live_state.keys())[0]

        history = agent.get_price_history(token) if token else []
        if not history or len(history) < 30:
            st.info(f"Waiting for ticks — need 30+, have {len(history) if history else 0}.")
            return

        df15 = build_candles(history, "15min")
        df5  = build_candles(history, "5min")
        if df15.empty or df5.empty or len(df15) < 2 or len(df5) < 2:
            st.info("Not enough candles for GHOST yet (need ≥2 of each timeframe).")
            return

        def _to_ohlcv(df):
            return df.rename(columns={
                "open":"Open","high":"High","low":"Low","close":"Close","volume":"Volume"
            }).set_index("timestamp")[["Open","High","Low","Close","Volume"]]

        df15_ohlc = _to_ohlcv(df15)
        df5_ohlc  = _to_ohlcv(df5)
        pdh, pdl, _ = _fetch_prev_day_levels(underlying)
        ltp = float(
            selected_state.get("last_price") or selected_state.get("ltp")
            or df5_ohlc["Close"].iloc[-1]
        )
        buyers_ratio = selected_state.get("buyers_ratio")
        engine = GhostStrategyEngine(instrument=instrument)
        signal = engine.evaluate(
            df_15min=df15_ohlc, df_5min=df5_ohlc,
            prev_day_high=pdh, prev_day_low=pdl,
            current_ltp=ltp, news_bias=news_bias,
            buyers_ratio=float(buyers_ratio) if buyers_ratio is not None else None,
            prev_day_close=pdl_extra if "pdl_extra" in dir() else None,
        )
        st.session_state["_ghost_signal_cache"] = (signal, instrument, pdh, pdl)

    # ── Market Depth confluence badge
    buyers_ratio = selected_state.get("buyers_ratio")
    depth_raw    = selected_state.get("depth", {})
    if buyers_ratio is not None:
        buy_pct = float(buyers_ratio)
        sell_pct = 100.0 - buy_pct
        if signal and signal.direction.value == "BEAR":
            if buy_pct <= 40:
                depth_badge = f"✅ Depth confirms BEAR — only {buy_pct:.0f}% buyers (sellers dominate)"
                depth_color = "normal"
            elif buy_pct >= 65:
                depth_badge = f"⚠️ Bid wall alert — {buy_pct:.0f}% buyers. Smart money may absorb this sell-off."
                depth_color = "off"
            else:
                depth_badge = f"◽ Depth neutral — {buy_pct:.0f}% buyers / {sell_pct:.0f}% sellers"
                depth_color = "off"
        elif signal and signal.direction.value == "BULL":
            if buy_pct >= 60:
                depth_badge = f"✅ Depth confirms BULL — {buy_pct:.0f}% buyers dominating"
                depth_color = "normal"
            elif buy_pct <= 35:
                depth_badge = f"⚠️ Ask wall alert — only {buy_pct:.0f}% buyers. Supply overhead."
                depth_color = "off"
            else:
                depth_badge = f"◽ Depth neutral — {buy_pct:.0f}% buyers / {sell_pct:.0f}% sellers"
                depth_color = "off"
        else:
            depth_badge = f"📊 Market Depth — {buy_pct:.0f}% buyers / {sell_pct:.0f}% sellers"
            depth_color = "off"
        st.caption(depth_badge)

    # ── Diagnostic "Why no trade today?" panel
    if signal is not None and not signal.actionable:
        with st.expander("🔍 Why no trade? — GHOST diagnostic", expanded=True):
            ltp_now = float(selected_state.get("last_price") or selected_state.get("ltp") or 0)
            d_col1, d_col2, d_col3, d_col4, d_col5, d_col6 = st.columns(6)
            d_col1.metric("PDH", f"{pdh:.0f}" if pdh else "—")
            d_col2.metric("PDL", f"{pdl:.0f}" if pdl else "—")
            d_col3.metric("Today H", f"{signal.today_high:.0f}" if signal.today_high else "—")
            d_col4.metric("Today L", f"{signal.today_low:.0f}" if signal.today_low else "—")
            d_col5.metric("VWAP", f"{signal.vwap:.0f}" if signal.vwap else "—",
                          signal.vwap_position if signal.vwap_position else "")
            d_col6.metric("Phase", signal.phase.value.replace("_", " ") if signal.phase else "—")
            if signal.gap_type and signal.gap_type != "FLAT":
                st.info(f"📊 **{signal.gap_type}** today ({signal.gap_pct:+.2f}%) — "
                        f"GHOST uses Today's High/Low ({signal.today_high:.0f}/{signal.today_low:.0f}) "
                        f"as sweep levels since price gapped past PDH/PDL.")
            if signal.setup_type:
                setup_labels = {
                    "COMBINED": "🏆 COMBINED — PDH/PDL sweep + VWAP (highest conviction)",
                    "PDH_PDL_SWEEP": "📍 PDH/PDL Sweep trap",
                    "TODAY_HIGH_LOW_SWEEP": "📍 Today's High/Low sweep (gap-day mode)",
                    "VWAP_REJECTION": "〰️ VWAP Rejection — reduce size vs normal setups",
                }
                st.caption("Setup type: " + setup_labels.get(signal.setup_type, signal.setup_type))

            reasons = []
            if pdh and pdl and ltp_now:
                inside = pdl < ltp_now < pdh
                if inside:
                    reasons.append(
                        f"⏳ **Price inside PDH/PDL range** — GHOST waits for a breakout "
                        f"above PDH ({pdh:.0f}) or below PDL ({pdl:.0f}) to trigger a "
                        f"retail trap. Current price {ltp_now:.0f} is {min(ltp_now-pdl, pdh-ltp_now):.0f} pts "
                        f"away from the nearest level."
                    )
                elif ltp_now >= pdh:
                    reasons.append(
                        f"📍 Price is **above PDH {pdh:.0f}** (bullish breakout zone). "
                        f"GHOST is watching for a liquidity sweep of PDH followed by a "
                        f"rejection candle on 15-min to confirm a PE (bear) setup."
                    )
                else:
                    reasons.append(
                        f"📍 Price is **below PDL {pdl:.0f}** (bearish breakdown zone). "
                        f"GHOST is watching for a liquidity sweep of PDL followed by a "
                        f"rejection candle on 15-min to confirm a CE (bull) setup."
                    )

            if signal.phase and "NO_TRADE" in str(signal.phase):
                reasons.append(
                    "🛑 **Hard stop gate fired** — low trap quality combined with news "
                    "supporting the breakout direction (not our reversal). Capital protection mode."
                )
            if signal.session_warning:
                reasons.append(signal.session_warning)
            if signal.trap_quality == "LOW":
                reasons.append("⚠️ Trap quality is LOW — sweep volume was thin (fake wick risk)")
            if signal.ob_high == 0 or not signal.ob_high:
                reasons.append("🔍 No Order Block found yet in the current 15-min structure")

            wait_msg = getattr(signal, "wait_message", "")
            if wait_msg:
                reasons.append(f"💬 Engine says: *{wait_msg}*")

            if not reasons:
                reasons.append("⏳ Conditions not fully aligned yet. Waiting for next setup.")

            for r in reasons:
                st.markdown(r)

            if signal.confluence_list:
                st.markdown("**Confluence so far:**")
                for c in signal.confluence_list:
                    st.markdown(f"  {c}")

    # ── Daily Risk Guard
    if "ghost_guard" not in st.session_state:
        st.session_state["ghost_guard"] = DailyRiskGuard()
    guard = st.session_state["ghost_guard"]

    gcol1, gcol2 = st.columns([3, 1])
    with gcol1:
        st.caption("🛡️ " + guard.status_line())
    with gcol2:
        if st.button("Reset day", key="ghost_guard_reset"):
            st.session_state["ghost_guard"] = DailyRiskGuard()
            st.session_state.pop("_ghost_signal_cache", None)
            st.rerun()

    if signal is not None and signal.actionable and not guard.can_trade(signal.confidence):
        st.error(guard.block_reason())
        with st.expander("Signal details (informational only — blocked)", expanded=False):
            render_ghost_panel(signal, instrument=instrument)
        return

    if signal is not None:
        render_ghost_panel(signal, instrument=instrument)

    # ── Record trade outcome
    if signal is not None and signal.actionable:
        with st.expander("📝 Record trade outcome (updates Daily Risk Guard)", expanded=False):
            st.caption("Log after trade closes to enforce cooldown/halt rules.")
            outcome_pnl = st.number_input(
                "P&L for this trade (₹, negative if loss)",
                value=0.0, step=50.0, key="ghost_outcome_pnl"
            )
            if st.button("Log outcome", key="ghost_log_outcome"):
                guard.record_trade(outcome_pnl)
                st.session_state.pop("_ghost_signal_cache", None)
                st.success("Logged ₹{:+,.0f}. {}".format(outcome_pnl, guard.status_line()))
                st.rerun()

# ─────────────────────────────────────────────────────────────
# How-to expander
# ─────────────────────────────────────────────────────────────
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

# ─────────────────────────────────────────────────────────────
# Sidebar — credentials
# ─────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Angel One SmartAPI WebSocket")
    api_key      = st.text_input("API Developer Key",
                                 value=os.getenv("ANGEL_API_KEY", os.getenv("SMARTAPI_API_KEY", "")),
                                 type="password")
    client_code  = st.text_input("Client ID (Mobile/Email)",
                                 value=os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE", "")))
    pin          = st.text_input("Angel One PIN",
                                 value=os.getenv("ANGEL_PIN", os.getenv("SMARTAPI_PIN", "")),
                                 type="password",
                                 help="Your 4-digit Angel One PIN")
    totp_secret  = st.text_input("16-Digit TOTP Secret Key",
                                 value=os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET", "")),
                                 type="password")
    token_list   = st.text_input(
        "Token List",
        value=os.getenv("SMARTAPI_TOKEN_LIST", "NIFTY 50=1:99926000,SENSEX=3:99919000"),
        help=(
            "Format: LABEL=exchangeType:token  "
            "Common tokens — "
            "NIFTY 50 index: 1:99926000 | "
            "SENSEX index: 3:99919000 | "
            "NIFTY Futures (current month): 1:256265 | "
            "BANKNIFTY Futures: 1:260105. "
            "Exchange types: 1=NSE, 2=BSE, 3=BSE_FO, 5=NSE_FO."
        ),
    )
    # ── Token quick-reference expander so you never have to guess
    with st.expander("📋 Common SmartAPI Token Reference", expanded=False):
        st.markdown("""
| Label | exchangeType | Token | Notes |
|---|---|---|---|
| NIFTY 50 Index | 1 | 99926000 | LTP only, no OI |
| SENSEX Index | 3 | 99919000 | LTP only, no OI |
| NIFTY Futures (near) | 1 | 256265 | Has OI & volume |
| BANKNIFTY Futures | 1 | 260105 | Has OI & volume |
| NIFTY Options (ATM CE) | 2 | Lookup via instruments API | Changes weekly |

**If tick count stays 0:** The token is valid but the exchange feed
is not sending data. Try switching to NIFTY Futures `1:256265`
which has active OI and volume — index tokens sometimes return
LTP only during low-activity periods.
        """)
    _MODE_MAP = {"LTP (1)": 1, "Quote (2)": 2, "Snap Quote (3)": 3, "Depth (4)": 4}
    _mode_label = st.selectbox(
        "Subscription Mode",
        options=list(_MODE_MAP.keys()),
        index=0,
        help="LTP is fastest. Use Snap Quote (3) if you need order book depth.",
    )
    subscription_mode = _MODE_MAP[_mode_label]   # always an int: 1/2/3/4
    candle_interval = st.selectbox(
        "Candle Interval",
        ["1min", "5min", "15min", "1day"],
        index=2,  # default 15min
    )
    auto_refresh     = st.checkbox("Live auto refresh", value=True)
    refresh_seconds  = st.number_input("Refresh seconds", min_value=1, max_value=10, value=1, step=1)
    # News fetched live from RSS feeds — no query string needed
    news_query = "live_rss"
    st.caption("📰 News: Live RSS from ET, Moneycontrol, Business Standard, LiveMint")
    st.markdown("**Step 1:** Authenticate with your PIN + TOTP and start the WebSocket stream")
    complete_session = st.button("Login & Start WebSocket")

# ─────────────────────────────────────────────────────────────
# Session state init
# ─────────────────────────────────────────────────────────────
if "agent" not in st.session_state:
    st.session_state.agent               = None
    st.session_state.last_connect_error  = None
    st.session_state.connected_at        = None
    st.session_state.logged_in           = False

# ── PATCH 2 (Edit 4): initialise news cache in session_state
if "last_news_result" not in st.session_state:
    st.session_state["last_news_result"] = None
if "raw_news_sentiment" not in st.session_state:
    st.session_state["raw_news_sentiment"] = 0
if "raw_banknifty_sentiment" not in st.session_state:
    st.session_state["raw_banknifty_sentiment"] = 0

status_container  = st.container()
metrics_container = st.container()
stream_container  = st.container()

render_news_radar_widget()

# ── PATCH 2 (Edit 4): read from session_state so panel always has data
news_result      = st.session_state.get("last_news_result", None)
global_news_bias = "NEUTRAL"

# ─────────────────────────────────────────────────────────────
# Login handler
# ─────────────────────────────────────────────────────────────
if complete_session:
    if not api_key or not client_code or not pin or not totp_secret:
        st.error("❌ Please fill in all credentials: API Key, Client ID, PIN, and TOTP Secret.")
    elif not SMARTAPI_AVAILABLE:
        st.error("SmartAPI package is not available. Install dependencies with `pip install -r requirements.txt`.")
    else:
        try:
            # Ensure mode is always int (SmartAPI rejects string modes silently)
            _safe_mode = int(subscription_mode) if subscription_mode else 1
            st.session_state.agent = SmartApiLiveAgent(
                api_key=api_key, client_code=client_code,
                pin=pin, totp_secret=totp_secret,
                token_list=token_list, mode=_safe_mode,
            )
            st.session_state.agent.login_with_pin(pin)
            st.session_state.agent.start()
            st.session_state.connected_at       = datetime.utcnow()
            st.session_state.last_connect_error = None
            st.session_state.logged_in          = True
            st.success("✅ Angel One SmartAPI WebSocket authenticated. Streaming live ticks...")
        except Exception as e:
            st.session_state.agent      = None
            st.session_state.logged_in  = False
            st.session_state.last_connect_error = str(e)
            st.error(f"❌ WebSocket login/start failed: {str(e)}")

if st.session_state.last_connect_error:
    st.error(f"Connection failed: {st.session_state.last_connect_error}")

# ─────────────────────────────────────────────────────────────
# Main dashboard (requires active session)
# ─────────────────────────────────────────────────────────────
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

        state              = st.session_state.agent.get_state()
        connected          = state.pop("connected",            False)
        last_error         = state.pop("last_error",           None)
        last_message       = state.pop("last_message",         None)
        last_subscribe_payload = state.pop("last_subscribe_payload", None)
        raw_message_count  = state.pop("raw_message_count",    0)

        if last_error:
            st.warning(f"Last live feed error: {last_error}")
        if not connected:
            st.error("Live feed is not connected yet. Waiting for market data...")
        elif raw_message_count == 0:
            st.warning("WebSocket is connected, but no market-data messages have arrived yet.")

        rows = []
        for token, data in state.items():
            rows.append({
                "Token":        token,
                "Label":        data.get("label"),
                "Last Price":   data.get("last_price", data.get("ltp")),
                "Ticks":        data.get("tick_count", 0),
                "EMA 9":        data.get("ema_9"),
                "VWAP":         data.get("vwap"),
                "Buyers Ratio": f"{data.get('buyers_ratio')}%" if data.get("buyers_ratio") is not None else "—",
                "Signal":       data.get("signal"),
                "Probability":  f"{data.get('probability')}%" if data.get("probability") is not None else "—",
                "Updated":      data.get("timestamp").strftime("%H:%M:%S") if data.get("timestamp") else "—",
            })

        df = pd.DataFrame(rows)

        with metrics_container:
            st.subheader("Live Feed Summary")
            st.dataframe(df, use_container_width=True)

        st.subheader("Candlestick Strategy Dashboard")

        if not PLOTLY_AVAILABLE:
            st.error("Plotly is not installed. Run `pip install -r requirements.txt`, then restart Streamlit.")

        elif state:
            token_options   = list(state.keys())
            selected_token  = st.selectbox(
                "Index Chart",
                token_options,
                format_func=lambda token: (
                    f"{state[token].get('label') or state[token].get('symbol') or token}"
                ),
            )
            history  = (
                st.session_state.agent.get_price_history(selected_token)
                if hasattr(st.session_state.agent, "get_price_history")
                else []
            )
            candles        = add_indicators(build_candles(history, candle_interval))
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
                latest      = candles.iloc[-1]

                # ── PATCH 2 (Edit 2): news_result comes from session_state
                #    (populated by render_news_dashboard every refresh cycle).
                #    render_news_dashboard is called below; we use the cached
                #    value here so the strategy panel never gets None.
                news_result, global_news_bias = render_news_dashboard(news_query)
                news_bias    = global_news_bias

                buyers_ratio = selected_state.get("buyers_ratio")
                levels       = latest_levels(candles)
                recommendations = strategy_recommendations(
                    candles,
                    buyers_ratio=buyers_ratio,
                    news_bias=news_bias,
                    underlying=(
                        selected_state.get("label")
                        or selected_state.get("symbol")
                        or selected_token
                    ),
                )

                metric_cols = st.columns(7)
                metric_cols[0].metric("Trend",      levels.get("trend", "NEUTRAL"))
                metric_cols[1].metric("RSI 14",     f"{latest.get('rsi_14', 0):.2f}"
                                                    if pd.notna(latest.get("rsi_14")) else "n/a")
                metric_cols[2].metric("Support",    levels.get("support",    "n/a"))
                metric_cols[3].metric("Resistance", levels.get("resistance", "n/a"))
                metric_cols[4].metric("VWAP",       f"{latest.get('vwap', 0):.2f}"
                                                    if pd.notna(latest.get("vwap")) else "n/a")
                metric_cols[5].metric("News Bias",  news_bias)
                updated    = selected_state.get("timestamp")
                delay_text = (
                    f"{(datetime.utcnow() - updated).total_seconds():.1f}s"
                    if updated else "n/a"
                )
                metric_cols[6].metric("Feed Delay", delay_text)

                # ── Chart overlay toggles (sidebar)
                with st.sidebar.expander("📈 Chart overlays", expanded=False):
                    show_ema = st.checkbox("EMA 9 / 20", value=False, key="chart_ema")
                    show_vwap = st.checkbox("VWAP", value=False, key="chart_vwap")
                    show_sr  = st.checkbox("Support / Resistance", value=False, key="chart_sr")
                    show_ghost_lines = st.checkbox("GHOST levels on chart", value=True, key="chart_ghost")

                # Fetch cached GHOST signal for chart overlay (computed later in _render_ghost_section)
                _ghost_sig_cache = st.session_state.get("_ghost_signal_cache")
                _chart_ghost_signal = _ghost_sig_cache[0] if _ghost_sig_cache and show_ghost_lines else None

                st.plotly_chart(
                    build_trading_chart(candles, selected_token,
                                        ghost_signal=_chart_ghost_signal,
                                        show_ema=show_ema, show_vwap=show_vwap, show_sr=show_sr),
                    use_container_width=True,
                )

                left, right = st.columns([2, 1])

                # ── PATCH 1 + 2 + 3: unified strategy panel with risk shield
                render_strategy_panel(
                    container       = left,
                    recommendations = recommendations,
                    selected_state  = selected_state,
                    candles         = candles,
                    news_result     = news_result,
                    brain           = _brain,
                    news_bias       = news_bias,
                )

                # ── GHOST strategy — single best setup (SMC + liquidity sweep)
                if GHOST_AVAILABLE:
                    _render_ghost_section(
                        st, candles, selected_state,
                        underlying=(
                            selected_state.get("label")
                            or selected_state.get("symbol")
                            or selected_token
                        ),
                        news_bias=news_bias,
                    )

                # ── Backtest panel
                _render_backtest_section(st)

                with right:
                    st.subheader("Levels & Pattern")
                    st.dataframe(
                        pd.DataFrame([
                            {"Item": "Pivot",               "Value": levels.get("pivot")},
                            {"Item": "Support S1",          "Value": levels.get("s1")},
                            {"Item": "Support S2",          "Value": levels.get("s2")},
                            {"Item": "Resistance R1",       "Value": levels.get("r1")},
                            {"Item": "Resistance R2",       "Value": levels.get("r2")},
                            {"Item": "Trendline Support",   "Value": levels.get("support_line")},
                            {"Item": "Trendline Resistance","Value": levels.get("resistance_line")},
                            {"Item": "Candle Formation",    "Value": latest.get("candle_pattern")},
                        ]),
                        use_container_width=True,
                        hide_index=True,
                    )

        else:
            st.info("No symbols available yet. Connect and wait for live data.")

        with stream_container.expander("Raw Stream State & Depth Data", expanded=False):
            st.markdown("### WebSocket Diagnostics")
            st.json({
                "connected":              connected,
                "raw_message_count":      raw_message_count,
                "last_error":             last_error,
                "last_subscribe_payload": last_subscribe_payload,
                "last_message":           last_message,
            })
            for token, data in state.items():
                st.markdown(f"### {token} — {data.get('label')}")
                st.json({
                    "last_price":   data.get("last_price", data.get("ltp")),
                    "ema_9":        data.get("ema_9"),
                    "vwap":         data.get("vwap"),
                    "buyers_ratio": data.get("buyers_ratio"),
                    "depth":        data.get("depth", {}),
                    "signal":       data.get("signal"),
                    "probability":  data.get("probability"),
                })

        st.caption("Use Ctrl+R in the browser or the Refresh Now button for an immediate redraw.")
        if auto_refresh:
            time.sleep(refresh_seconds)
            st.rerun()

    else:
        st.info("SMS OTP sent. Enter the code and click 'Complete Session with SMS OTP' to start streaming.")

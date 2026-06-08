"""
patch_streamlit_strategy.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DROP-IN REPLACEMENT for the "Recommended Strategies" block
inside streamlit_app.py.

HOW TO INTEGRATE:
  Replace the block starting at:
      with left:
          st.subheader("Recommended Strategies")
  ...all the way to the end of that `with left:` block...
  with a call to:
      render_strategy_panel(left, recommendations, selected_state, candles, news_result)

Import at the top of streamlit_app.py:
    from patch_streamlit_strategy import render_strategy_panel

WHAT THIS PATCH DOES (PATCH 1 + PATCH 2 + PATCH 3):
  1. Calls brain.reconcile_signals() — unified probability
  2. Calls brain.generate_position_sizing() — lot-aware risk shield
  3. Bridges AsyncGuruNewsEngine sentiment → Layer 3 news_score
  4. Adds "Lots", "Qty", "Max Loss ₹", "Unified Prob%" columns
     to the strategy recommendations table
  5. Shows a hard BLOCKED banner when 1 lot > BASE_RISK_RUPEES budget
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import os
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BASE_RISK       = float(os.getenv("BASE_RISK_RUPEES", 500))
MAX_DAILY_LOSS  = float(os.getenv("MAX_DAILY_LOSS_LIMIT", 1500))


def _derive_risk_per_point(row: pd.Series) -> float:
    """
    Derive stop-loss distance in index points from a strategy row.
    Uses Entry - Stop (for bullish) or Stop - Entry (for bearish).
    Both are always positive after abs().
    """
    try:
        return abs(float(row["Entry"]) - float(row["Stop"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _lot_size_for(underlying: str) -> int:
    """NSE lot-size lookup — deterministic, no API call."""
    LOT_SIZES = {
        "NIFTY":     75,
        "BANKNIFTY": 35,
        "SENSEX":    10,
        "FINNIFTY":  40,
    }
    key = underlying.upper().replace(" ", "").replace("50", "")
    for k, v in LOT_SIZES.items():
        if k in key:
            return v
    return 75   # safe default


def _compute_sizing_for_row(
    row: pd.Series,
    unified_prob: float,
    underlying: str,
    brain,
) -> dict:
    """
    Run generate_position_sizing() for one strategy row and return
    a flat dict with the columns we want to display.
    """
    risk_per_point = _derive_risk_per_point(row)
    result = brain.generate_position_sizing(
        probability_score=unified_prob,
        risk_per_point=risk_per_point,
        symbol=underlying,
    )
    return result


def render_strategy_panel(
    container,
    recommendations: pd.DataFrame,
    selected_state: dict,
    candles: pd.DataFrame,
    news_result: dict | None,
    brain,
    news_bias: str = "NEUTRAL",
) -> None:
    """
    Render the full strategy panel with risk-shielded lot sizing.

    Parameters
    ----------
    container       : streamlit column/container to render into
    recommendations : DataFrame from classic_strategies.strategy_recommendations()
    selected_state  : live state dict for the selected symbol
    candles         : indicator-enriched OHLCV DataFrame
    news_result     : raw result from fetch_all_market_news() — used to
                      extract integer sentiment for Layer 3 bridge
    brain           : StrategyBrainEngine instance
    news_bias       : "BULLISH" | "BEARISH" | "NEUTRAL" string bias
    """

    with container:
        st.subheader("Recommended Strategies + Risk Shield")

        if recommendations.empty or candles.empty:
            st.info("Waiting for enough candles to generate strategy rows.")
            return

        # ── Extract live price data for reconcile_signals()
        ltp           = selected_state.get("last_price") or selected_state.get("ltp") or 0.0
        ema_9         = selected_state.get("ema_9") or ltp
        vwap          = selected_state.get("vwap") or ltp
        buyers_pct    = selected_state.get("buyers_ratio") or 50.0
        buyers_ratio  = buyers_pct / 100.0          # convert % → fraction

        # ── Derive underlying name for lot-size lookup
        label      = (
            selected_state.get("label")
            or selected_state.get("symbol")
            or "NIFTY"
        )

        # ── PATCH 3: extract raw integer sentiment from news_result
        raw_news_sentiment = 0
        if news_result and news_result.get("status") == "SUCCESS":
            items = news_result.get("items", [])
            if items:
                # Sum the top-5 article sentiment scores → single integer
                raw_news_sentiment = sum(
                    int(item.get("sentiment_score", 0))
                    for item in items[:5]
                )

        # ── Nifty trend from pre-market or latest candle slope
        if len(candles) >= 2:
            nifty_trend = (
                "BULLISH"
                if candles["close"].iloc[-1] > candles["close"].iloc[-2]
                else "BEARISH"
            )
        else:
            nifty_trend = "NEUTRAL"

        # ── For each strategy row, run full reconcile + sizing
        augmented_rows = []
        for _, row in recommendations.iterrows():
            classic_score = int(row.get("Score", 0))
            classic_bias  = str(row.get("Bias", "Neutral"))
            risk_per_point = _derive_risk_per_point(row)

            # PATCH 2: unified reconciliation
            reconciled = brain.reconcile_signals(
                classic_score      = classic_score,
                classic_bias       = classic_bias,
                current_price      = ltp,
                ema_9              = ema_9,
                vwap               = vwap,
                buyers_ratio       = buyers_ratio,
                raw_news_sentiment = raw_news_sentiment,
                nifty_trend        = nifty_trend,
                risk_per_point     = risk_per_point,
                symbol             = label,
            )

            sizing        = reconciled["sizing"]
            unified_prob  = reconciled["unified_probability"]
            actionable    = reconciled["actionable"]

            augmented_rows.append({
                "Strategy"     : row.get("Strategy", ""),
                "Bias"         : row.get("Bias", ""),
                "Option"       : row.get("Option", ""),
                "Entry"        : row.get("Entry", ""),
                "Target"       : row.get("Target", ""),
                "Stop"         : row.get("Stop", ""),
                "Classic Score": f"{classic_score}/5",
                "Unified Prob" : f"{unified_prob:.1f}%",
                "Lots"         : sizing.get("lots", 0),
                "Qty"          : sizing.get("quantity", 0),
                "Max Loss ₹"   : (
                    f"₹{sizing['max_loss_rupees']:,.0f}"
                    if sizing.get("max_loss_rupees", 0) > 0
                    else "—"
                ),
                "Actionable"   : "✅ YES" if actionable else "🚫 NO",
                "Reason"       : row.get("Reason", ""),
            })

        df_aug = pd.DataFrame(augmented_rows)

        # ── Colour-code the Actionable column via background
        def _highlight(row):
            colour = (
                "background-color:#E1F5EE"
                if row["Actionable"] == "✅ YES"
                else "background-color:#FCEBEB"
            )
            return [colour] * len(row)

        styled = df_aug.style.apply(_highlight, axis=1)
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # ── Best actionable row callout
        actionable_df = df_aug[df_aug["Actionable"] == "✅ YES"]
        if not actionable_df.empty:
            top = actionable_df.iloc[0]
            st.success(
                f"🎯 **Best setup:** {top['Strategy']} | "
                f"Option: `{top['Option']}` | "
                f"Entry: `{top['Entry']}` | "
                f"Target: `{top['Target']}` | "
                f"SL: `{top['Stop']}` | "
                f"**Lots: {top['Lots']}** | "
                f"Qty: {top['Qty']} | "
                f"Max Loss: {top['Max Loss ₹']} | "
                f"Prob: {top['Unified Prob']}"
            )
        else:
            st.warning(
                f"🚫 No strategy meets the entry threshold. "
                f"All setups are below the probability gate or "
                f"1 lot would breach the ₹{BASE_RISK:,.0f} risk cap."
            )

        # ── Daily loss tracker widget
        st.markdown("---")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Risk Cap / Trade", f"₹{BASE_RISK:,.0f}")
        col_b.metric("Max Daily Loss", f"₹{MAX_DAILY_LOSS:,.0f}")
        col_c.metric(
            "Trades until shutdown",
            str(int(MAX_DAILY_LOSS // BASE_RISK)),
        )

        # ── Audit log expander (full reconciliation trace)
        with st.expander("🔍 Signal Reconciliation Audit Log", expanded=False):
            if augmented_rows:
                # Re-run reconcile for top row to show full logs
                top_row = recommendations.iloc[0]
                full_reconcile = brain.reconcile_signals(
                    classic_score      = int(top_row.get("Score", 0)),
                    classic_bias       = str(top_row.get("Bias", "Neutral")),
                    current_price      = ltp,
                    ema_9              = ema_9,
                    vwap               = vwap,
                    buyers_ratio       = buyers_ratio,
                    raw_news_sentiment = raw_news_sentiment,
                    nifty_trend        = nifty_trend,
                    risk_per_point     = _derive_risk_per_point(top_row),
                    symbol             = label,
                )
                st.markdown(f"**Direction:** `{full_reconcile['direction']}`")
                st.markdown(f"**Matrix probability:** `{full_reconcile['matrix_probability']}%`")
                st.markdown(f"**Classic score:** `{full_reconcile['classic_score']}/5`")
                st.markdown(f"**Unified probability:** `{full_reconcile['unified_probability']}%`")
                st.markdown(f"**Raw news sentiment fed to Layer 3:** `{raw_news_sentiment}`")
                st.markdown("**Full audit trail:**")
                for log_line in full_reconcile["logs"]:
                    st.markdown(f"- {log_line}")

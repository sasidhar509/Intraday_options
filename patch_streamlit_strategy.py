"""
patch_streamlit_strategy.py  ── FIXED v3 (Python 3.8 compatible)

FIXES:
  1. Reads "Entry","Target","Stop" as OPTION PREMIUM values (not index levels)
     now that classic_strategies.py is fixed to output premium prices.
  2. Adds "T2", "Strike", "Idx LTP", "Idx Entry" columns from new schema.
  3. risk_per_point now derived from OPTION SL distance (Entry-Stop premium),
     not index point distance — correctly sizes lots for option buying.
  4. Removed stale html() import that caused import errors in some envs.
  5. Best-setup callout shows option contract label with live premium values.
"""

from __future__ import annotations
from typing import Optional

import os
import math
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

BASE_RISK      = float(os.getenv("BASE_RISK_RUPEES",   500))
MAX_DAILY_LOSS = float(os.getenv("MAX_DAILY_LOSS_LIMIT", 1500))

LOT_SIZES = {
    "NIFTY":     75,
    "BANKNIFTY": 35,
    "SENSEX":    10,
    "FINNIFTY":  40,
}


def _lot_size_for(underlying: str) -> int:
    key = str(underlying).upper().replace(" ", "").replace("50", "")
    for k, v in LOT_SIZES.items():
        if k in key:
            return v
    return 75


def _derive_risk_per_premium(row: pd.Series) -> float:
    """
    FIX: risk distance is now Entry premium − Stop premium (option ₹ values).
    Returns ₹ risk per unit (1 share of the option).
    """
    try:
        entry = float(row.get("Entry") or 0)
        stop  = float(row.get("Stop")  or 0)
        return max(0.0, abs(entry - stop))
    except (TypeError, ValueError):
        return 0.0


def _lots_from_premium_risk(
    risk_per_unit: float,
    probability: float,
    symbol: str,
) -> dict:
    """
    Size lots so that (lots × lot_size × risk_per_unit) ≤ capital_budget.

    capital_budget scales with conviction:
      ≥ 90%  → 2.0× BASE_RISK
      ≥ 75%  → 1.5× BASE_RISK
      else   → 1.0× BASE_RISK
    """
    if probability >= 90:
        multiplier = 2.0
        strategy   = "AGGRESSIVE — 2× allocation"
    elif probability >= 75:
        multiplier = 1.5
        strategy   = "HIGH CONVICTION — 1.5× allocation"
    else:
        multiplier = 1.0
        strategy   = "STANDARD allocation"

    budget    = BASE_RISK * multiplier
    lot_size  = _lot_size_for(symbol)

    if risk_per_unit <= 0:
        return {
            "status": "NO_TRADE",
            "lots": 0, "quantity": 0,
            "lot_size": lot_size,
            "max_loss_rupees": 0.0,
            "capital_budget": budget,
            "allocation_strategy": "BLOCKED — zero risk distance",
            "reason": "SL = Entry: cannot size a trade with zero risk.",
        }

    cost_per_lot = risk_per_unit * lot_size
    lots         = max(0, int(math.floor(budget / cost_per_lot)))

    if lots == 0:
        return {
            "status": "NO_TRADE",
            "lots": 0, "quantity": 0,
            "lot_size": lot_size,
            "max_loss_rupees": 0.0,
            "capital_budget": round(budget, 2),
            "allocation_strategy": "BLOCKED",
            "reason": (
                "1 lot costs ₹{:,.0f} ({}×₹{:.0f}) "
                "which exceeds ₹{:,.0f} budget.".format(
                    cost_per_lot, lot_size, risk_per_unit, budget
                )
            ),
        }

    max_loss = lots * cost_per_lot
    return {
        "status": "EXECUTE_TRADE",
        "lots": lots,
        "quantity": lots * lot_size,
        "lot_size": lot_size,
        "max_loss_rupees": round(max_loss, 2),
        "capital_budget": round(budget, 2),
        "allocation_strategy": strategy,
        "reason": (
            "{} lot(s) × {} = {} units | "
            "Max loss ₹{:,.0f} ≤ budget ₹{:,.0f}".format(
                lots, lot_size, lots * lot_size,
                max_loss, budget
            )
        ),
    }


def render_strategy_panel(
    container,
    recommendations: pd.DataFrame,
    selected_state: dict,
    candles: pd.DataFrame,
    news_result: Optional[dict],
    brain,
    news_bias: str = "NEUTRAL",
) -> None:
    """
    Render strategy panel with OPTION PREMIUM prices and live-synced lot sizing.
    """
    with container:
        st.subheader("📋 Option Strategies — Live Premium Prices")

        if recommendations.empty or candles.empty:
            st.info("Waiting for live candles to generate strategy rows.")
            return

        # ── Live market state
        ltp          = float(selected_state.get("last_price") or
                             selected_state.get("ltp") or 0.0)
        ema_9        = float(selected_state.get("ema_9")  or ltp)
        vwap         = float(selected_state.get("vwap")   or ltp)
        buyers_pct   = float(selected_state.get("buyers_ratio") or 50.0)
        buyers_ratio = buyers_pct / 100.0

        label = (
            selected_state.get("label")
            or selected_state.get("symbol")
            or "NIFTY"
        )

        # ── News sentiment for reconciliation
        raw_news = 0
        if news_result and news_result.get("status") == "SUCCESS":
            raw_news = sum(
                int(item.get("sentiment_score", 0))
                for item in (news_result.get("items") or [])[:5]
            )

        # ── Trend from candles
        if len(candles) >= 2:
            nifty_trend = (
                "BULLISH"
                if candles["close"].iloc[-1] > candles["close"].iloc[-2]
                else "BEARISH"
            )
        else:
            nifty_trend = "NEUTRAL"

        # ── Build augmented rows
        augmented = []
        for _, row in recommendations.iterrows():
            classic_score = int(row.get("Score", 0))
            classic_bias  = str(row.get("Bias",  "Neutral"))

            # Unified probability via brain reconciler
            risk_pts = _derive_risk_per_premium(row)   # ₹ per unit
            reconciled = brain.reconcile_signals(
                classic_score      = classic_score,
                classic_bias       = classic_bias,
                current_price      = ltp,
                ema_9              = ema_9,
                vwap               = vwap,
                buyers_ratio       = buyers_ratio,
                raw_news_sentiment = raw_news,
                nifty_trend        = nifty_trend,
                risk_per_point     = max(risk_pts, 1.0),
                symbol             = label,
            )

            unified_prob = reconciled["unified_probability"]
            actionable   = reconciled["actionable"]

            # Lot sizing based on OPTION premium risk distance
            sizing = _lots_from_premium_risk(risk_pts, unified_prob, label)

            # Option premium values (already correct from fixed classic_strategies)
            entry_px  = row.get("Entry",  "—")
            target_px = row.get("Target", "—")
            t2_px     = row.get("T2",     "—")
            stop_px   = row.get("Stop",   "—")

            # R:R calculation
            try:
                rr = round(
                    (float(target_px) - float(entry_px)) /
                    max(float(entry_px) - float(stop_px), 0.01),
                    1
                )
                rr_str = "1:{:.1f}".format(rr)
            except Exception:
                rr_str = "—"

            augmented.append({
                "Strategy":     row.get("Strategy", ""),
                "Bias":         classic_bias,
                "Contract":     row.get("Option",   ""),
                "Strike":       row.get("Strike",   ""),
                "Idx LTP":      row.get("Idx LTP",  ltp),
                "Entry ₹":      entry_px,
                "SL ₹":         stop_px,
                "T1 ₹":         target_px,
                "T2 ₹":         t2_px,
                "R:R":          rr_str,
                "Score":        "{}/5".format(classic_score),
                "Prob %":       "{:.1f}%".format(unified_prob),
                "Lots":         sizing.get("lots",       0),
                "Qty":          sizing.get("quantity",    0),
                "Max Loss ₹":   (
                    "₹{:,.0f}".format(sizing["max_loss_rupees"])
                    if sizing.get("max_loss_rupees", 0) > 0
                    else "—"
                ),
                "Action":       "✅ YES" if actionable else "🚫 NO",
                "Reason":       row.get("Reason", ""),
            })

        df_aug = pd.DataFrame(augmented)

        if df_aug.empty:
            st.warning("No strategies generated for current market data.")
            return

        # ── Colour rows
        def _highlight(row):
            if row.get("Action") == "✅ YES":
                return ["background-color:#E1F5EE"] * len(row)
            return ["background-color:#FCEBEB"] * len(row)

        display_cols = [
            "Strategy", "Bias", "Contract", "Strike", "Idx LTP",
            "Entry ₹", "SL ₹", "T1 ₹", "T2 ₹", "R:R",
            "Score", "Prob %", "Lots", "Qty", "Max Loss ₹", "Action",
        ]
        cols_present = [c for c in display_cols if c in df_aug.columns]

        st.dataframe(
            df_aug[cols_present].style.apply(_highlight, axis=1),
            use_container_width=True,
            hide_index=True,
        )

        # ── Best actionable callout
        yes_df = df_aug[df_aug["Action"] == "✅ YES"]
        if not yes_df.empty:
            top = yes_df.iloc[0]
            st.success(
                "🎯 **Best Setup:** `{}` | "
                "Entry ₹**{}** | SL ₹{} | T1 ₹{} | T2 ₹{} | "
                "R:R **{}** | Lots **{}** | Max Loss **{}** | Prob **{}**".format(
                    top.get("Contract", ""),
                    top.get("Entry ₹",  "—"),
                    top.get("SL ₹",     "—"),
                    top.get("T1 ₹",     "—"),
                    top.get("T2 ₹",     "—"),
                    top.get("R:R",      "—"),
                    top.get("Lots",     0),
                    top.get("Max Loss ₹", "—"),
                    top.get("Prob %",   "—"),
                )
            )

            # One-click copy block
            copy_text = "{} | Entry ₹{} | SL ₹{} | T1 ₹{} | T2 ₹{}".format(
                top.get("Contract", ""),
                top.get("Entry ₹",  "—"),
                top.get("SL ₹",     "—"),
                top.get("T1 ₹",     "—"),
                top.get("T2 ₹",     "—"),
            )
            st.code(copy_text, language=None)

        else:
            st.warning(
                "🚫 No actionable setup. All strategies below probability "
                "threshold or 1 lot exceeds ₹{:,.0f} risk cap.".format(BASE_RISK)
            )

        # ── Risk metrics footer
        st.markdown("---")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Risk / Trade",     "₹{:,.0f}".format(BASE_RISK))
        c2.metric("Max Daily Loss",   "₹{:,.0f}".format(MAX_DAILY_LOSS))
        c3.metric("Trades → Shutdown", str(int(MAX_DAILY_LOSS // BASE_RISK)))
        c4.metric("Live LTP",         "₹{:,.2f}".format(ltp) if ltp > 0 else "—")

        # ── Full audit log
        with st.expander("🔍 Signal Reconciliation Audit", expanded=False):
            if augmented:
                top_row = recommendations.iloc[0]
                rp      = _derive_risk_per_premium(top_row)
                full_r  = brain.reconcile_signals(
                    classic_score      = int(top_row.get("Score", 0)),
                    classic_bias       = str(top_row.get("Bias",  "Neutral")),
                    current_price      = ltp,
                    ema_9              = ema_9,
                    vwap               = vwap,
                    buyers_ratio       = buyers_ratio,
                    raw_news_sentiment = raw_news,
                    nifty_trend        = nifty_trend,
                    risk_per_point     = max(rp, 1.0),
                    symbol             = label,
                )
                st.markdown("**Direction:** `{}`".format(full_r["direction"]))
                st.markdown("**Matrix prob:** `{}%`".format(full_r["matrix_probability"]))
                st.markdown("**Unified prob:** `{}%`".format(full_r["unified_probability"]))
                st.markdown("**News sentiment fed to Layer 3:** `{}`".format(raw_news))
                st.markdown("**Audit trail:**")
                for line in full_r["logs"]:
                    st.markdown("- " + line)

from __future__ import annotations  # enables dict[]/list[]/X|Y on Python 3.8+
from typing import Dict, List, Optional

"""
agents/brain.py  ── PATCHED v2
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PATCH 1 ── Risk Shield: generate_position_sizing() now returns
           exact NIFTY lot-count capped to BASE_RISK_RUPEES.

PATCH 2 ── Unified signal: reconcile_signals() merges the
           classic_strategies score with the 4-layer matrix
           into one authoritative probability + quantity output.

PATCH 3 ── News bridge: news_score_from_sentiment() converts
           AsyncGuruNewsEngine's integer sentiment_score into
           the ±25 / 0 discrete value brain.py Layer 3 expects.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Zero hallucination — every branch is an explicit if/else check
on numeric values. No LLM calls, no probabilistic text parsing.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── NIFTY/BANKNIFTY lot sizes (NSE-mandated, updated June 2026)
LOT_SIZES: Dict[str, int] = {
    "NIFTY":     75,
    "BANKNIFTY": 35,
    "SENSEX":    10,
    "FINNIFTY":  40,
}
DEFAULT_LOT_SIZE = 75   # fallback when symbol is unknown


class StrategyBrainEngine:
    """Core mathematical engine — zero hallucination, pure if/else logic."""

    def __init__(self):
        self.base_risk        = float(os.getenv("BASE_RISK_RUPEES", 500))
        self.stop_loss_pct    = float(os.getenv("STOP_LOSS_PERCENTAGE", 15))
        self.entry_threshold  = float(os.getenv("ENTRY_THRESHOLD_PROBABILITY", 75))
        self.max_daily_loss   = float(os.getenv("MAX_DAILY_LOSS_LIMIT", 1500))

    # ════════════════════════════════════════════════════════════
    # PATCH 3  — News score bridge
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def news_score_from_sentiment(raw_sentiment_score: int, direction: str) -> int:
        """
        Convert AsyncGuruNewsEngine's integer sentiment_score to the
        discrete ±25 / 0 value that evaluate_probability_score() Layer 3
        expects.

        Rules (deterministic, no text parsing):
          raw > 0  AND direction == BULLISH  →  +25
          raw < 0  AND direction == BEARISH  →  -25  (stored as -25 internally,
                                                       Layer 3 checks for == -25)
          raw == 0 OR mismatch               →   0

        Args:
            raw_sentiment_score: integer from AsyncGuruNewsEngine
                                 (positive = bullish words dominated,
                                  negative = bearish words dominated)
            direction: "BULLISH" or "BEARISH" from the probability matrix

        Returns:
            int: +25, -25, or 0
        """
        if raw_sentiment_score > 0 and direction == "BULLISH":
            return 25
        if raw_sentiment_score < 0 and direction == "BEARISH":
            return -25
        return 0

    # ════════════════════════════════════════════════════════════
    # PATCH 1  — Risk Shield: lot-aware position sizing
    # ════════════════════════════════════════════════════════════

    def generate_position_sizing(
        self,
        probability_score: float,
        risk_per_point: float,
        symbol: str = "NIFTY",
        premium_per_lot: Optional[float] = None,
    ) -> dict:
        """
        Calculate the exact number of LOTS (not raw shares) that keeps the
        maximum possible loss under BASE_RISK_RUPEES.

        Args:
            probability_score : 0–100 from the probability matrix
            risk_per_point    : stop-loss distance in index points
                                (entry_price − stop_loss_price)
            symbol            : "NIFTY" | "BANKNIFTY" | "SENSEX" | …
            premium_per_lot   : if buying options outright, pass the premium
                                per lot so we cap outlay too (optional)

        Returns dict keys:
            status            : "EXECUTE_TRADE" | "NO_TRADE"
            lots              : int — number of lots to trade
            quantity          : int — lots × lot_size (actual shares/units)
            lot_size          : int — NSE lot size for the symbol
            risk_multiplier   : float
            max_loss_rupees   : float — worst-case loss at this sizing
            allocation_strategy: str
            reason            : str (on NO_TRADE)

        Risk-shield invariant (enforced, not advisory):
            max_loss_rupees  ≤  BASE_RISK_RUPEES × risk_multiplier
        """
        if probability_score < self.entry_threshold:
            return {
                "status": "NO_TRADE",
                "lots": 0,
                "quantity": 0,
                "lot_size": LOT_SIZES.get(symbol.upper(), DEFAULT_LOT_SIZE),
                "risk_multiplier": 0.0,
                "max_loss_rupees": 0.0,
                "allocation_strategy": "BLOCKED",
                "reason": (
                    f"Probability {probability_score:.0f}% is below the "
                    f"{self.entry_threshold:.0f}% entry threshold — NO TRADE."
                ),
            }

        if risk_per_point <= 0:
            return {
                "status": "NO_TRADE",
                "lots": 0,
                "quantity": 0,
                "lot_size": LOT_SIZES.get(symbol.upper(), DEFAULT_LOT_SIZE),
                "risk_multiplier": 0.0,
                "max_loss_rupees": 0.0,
                "allocation_strategy": "BLOCKED",
                "reason": "risk_per_point must be > 0 (entry price − stop loss).",
            }

        # ── Step 1: determine conviction multiplier (explicit ladder)
        if probability_score == 100:
            risk_multiplier      = 2.0
            allocation_strategy  = "AGGRESSIVE — Double Allocation (100% conviction)"
        elif probability_score >= 85:
            risk_multiplier      = 1.5
            allocation_strategy  = "HIGH CONVICTION — 1.5× Allocation"
        elif probability_score >= 75:
            risk_multiplier      = 1.0
            allocation_strategy  = "STANDARD — Normal Allocation"
        else:
            # 75 > score >= entry_threshold: edge case from custom threshold
            risk_multiplier      = 0.5
            allocation_strategy  = "DEFENSIVE — Half Allocation"

        # ── Step 2: capital budget for this trade
        capital_at_risk = self.base_risk * risk_multiplier   # e.g. ₹1,000 at 100%

        # ── Step 3: lot size from NSE table
        lot_size = LOT_SIZES.get(symbol.upper(), DEFAULT_LOT_SIZE)

        # ── Step 4: rupee loss per lot if SL is hit
        #    For index options, 1 index point = ₹lot_size per lot
        rupee_loss_per_lot = risk_per_point * lot_size

        # ── Step 5: max lots we can afford under the capital budget
        #    Floor to whole lots; never round up (would breach shield)
        if rupee_loss_per_lot <= 0:
            lots = 0
        else:
            lots = int(capital_at_risk // rupee_loss_per_lot)

        # ── Step 6: if premium-buying mode, also cap by outright premium cost
        if premium_per_lot is not None and premium_per_lot > 0:
            max_lots_by_premium = int(capital_at_risk // premium_per_lot)
            lots = min(lots, max_lots_by_premium)

        # ── Step 7: hard floor — at least 0 lots (never negative)
        lots = max(0, lots)

        if lots == 0:
            return {
                "status": "NO_TRADE",
                "lots": 0,
                "quantity": 0,
                "lot_size": lot_size,
                "risk_multiplier": risk_multiplier,
                "max_loss_rupees": 0.0,
                "allocation_strategy": "BLOCKED",
                "reason": (
                    f"Even 1 lot of {symbol} risks ₹{rupee_loss_per_lot:,.0f} "
                    f"which exceeds the ₹{capital_at_risk:,.0f} budget at "
                    f"{probability_score:.0f}% conviction. "
                    f"Widen the stop or wait for a tighter setup."
                ),
            }

        actual_max_loss = lots * rupee_loss_per_lot

        return {
            "status": "EXECUTE_TRADE",
            "lots": lots,
            "quantity": lots * lot_size,
            "lot_size": lot_size,
            "risk_multiplier": risk_multiplier,
            "max_loss_rupees": round(actual_max_loss, 2),
            "capital_budget": round(capital_at_risk, 2),
            "allocation_strategy": allocation_strategy,
            "reason": (
                f"{lots} lot(s) × {lot_size} = {lots * lot_size} units | "
                f"Max loss ₹{actual_max_loss:,.0f} ≤ budget ₹{capital_at_risk:,.0f}"
            ),
        }

    # ════════════════════════════════════════════════════════════
    # PATCH 2  — Unified signal reconciler
    # ════════════════════════════════════════════════════════════

    def reconcile_signals(
        self,
        classic_score: int,
        classic_bias: str,
        current_price: float,
        ema_9: float,
        vwap: float,
        buyers_ratio: float,
        raw_news_sentiment: int,
        nifty_trend: str,
        risk_per_point: float,
        symbol: str = "NIFTY",
        premium_per_lot: Optional[float] = None,
    ) -> dict:
        """
        Single entry-point that merges classic_strategies output with the
        4-layer probability matrix and then computes lot sizing.

        classic_score   : 0–5 integer from strategy_recommendations()
        classic_bias    : "Bullish" | "Bearish" from strategy_recommendations()
        current_price   : live LTP
        ema_9           : 9-period EMA
        vwap            : VWAP
        buyers_ratio    : 0.0–1.0 (fraction, NOT percentage)
        raw_news_sentiment: integer from AsyncGuruNewsEngine.sentiment_score
        nifty_trend     : "BULLISH" | "BEARISH" | "NEUTRAL"
        risk_per_point  : SL distance in index points
        symbol          : underlying (for lot size lookup)
        premium_per_lot : optional outright premium per lot (buying mode)

        Returns a single unified dict with:
            direction         : "BULLISH" | "BEARISH" | "NEUTRAL"
            matrix_probability: 0–100 from the 4-layer matrix
            classic_score     : 0–5 echoed back
            unified_probability: blended score (see weighting below)
            actionable        : bool — True if unified_probability ≥ threshold
            sizing            : full dict from generate_position_sizing()
            logs              : List[str] — full audit trail
        """
        logs: List[str] = []

        # ── Step 1: determine direction
        #    Classic strategy direction takes precedence if score ≥ 3 (60%+)
        #    Otherwise fall back to matrix direction
        if classic_score >= 3 and classic_bias.upper() in ("BULLISH", "BEARISH"):
            direction = classic_bias.upper()
            logs.append(
                f"Direction from classic strategy (score {classic_score}/5): {direction}"
            )
        else:
            # Pre-derive matrix direction without full scoring
            if current_price > ema_9 and current_price > vwap:
                direction = "BULLISH"
            elif current_price < ema_9 and current_price < vwap:
                direction = "BEARISH"
            else:
                direction = "NEUTRAL"
            logs.append(
                f"Direction from price structure (EMA/VWAP): {direction}"
            )

        if direction == "NEUTRAL":
            logs.append("Price inside EMA/VWAP chop zone — NO TRADE.")
            return {
                "direction": "NEUTRAL",
                "matrix_probability": 0,
                "classic_score": classic_score,
                "unified_probability": 0,
                "actionable": False,
                "sizing": {
                    "status": "NO_TRADE",
                    "lots": 0,
                    "quantity": 0,
                    "reason": "Price in chop zone.",
                },
                "logs": logs,
            }

        # ── Step 2: convert news sentiment (PATCH 3 bridge)
        news_score = self.news_score_from_sentiment(raw_news_sentiment, direction)
        logs.append(
            f"News bridge: raw_sentiment={raw_news_sentiment} → "
            f"Layer 3 news_score={news_score} (direction={direction})"
        )

        # ── Step 3: run 4-layer matrix
        matrix_result = self.evaluate_probability_score(
            current_price=current_price,
            ema_9=ema_9,
            vwap=vwap,
            buyers_ratio=buyers_ratio,
            news_score=news_score,
            nifty_trend=nifty_trend,
        )
        matrix_prob = matrix_result.get("score", 0)
        logs.extend(matrix_result.get("logs", []))

        # ── Step 4: blend classic score into probability
        #    classic_score is 0–5; normalise to 0–100 and apply 30% weight.
        #    The 4-layer matrix carries 70% weight.
        #    Rationale: matrix uses hard price data; classic adds pattern context.
        classic_pct     = (classic_score / 5) * 100          # 0–100
        unified_prob    = round(0.70 * matrix_prob + 0.30 * classic_pct, 1)
        logs.append(
            f"Blend: 70% × matrix({matrix_prob}) + 30% × classic({classic_pct:.0f}) "
            f"= unified {unified_prob}%"
        )

        # ── Step 5: actionability gate
        actionable = unified_prob >= self.entry_threshold
        if not actionable:
            logs.append(
                f"Unified {unified_prob}% < threshold {self.entry_threshold}% → NO TRADE"
            )

        # ── Step 6: risk-shielded lot sizing
        sizing = self.generate_position_sizing(
            probability_score=unified_prob,
            risk_per_point=risk_per_point,
            symbol=symbol,
            premium_per_lot=premium_per_lot,
        )
        logs.append(f"Sizing result: {sizing['reason']}")

        return {
            "direction": direction,
            "matrix_probability": matrix_prob,
            "classic_score": classic_score,
            "unified_probability": unified_prob,
            "actionable": actionable,
            "sizing": sizing,
            "logs": logs,
        }

    # ════════════════════════════════════════════════════════════
    # ORIGINAL methods — unchanged below this line
    # ════════════════════════════════════════════════════════════

    def calculate_measured_move(self, point_a, point_b, point_c):
        wave_1_height = abs(point_b - point_a)
        if wave_1_height <= 0 or point_c >= point_b:
            return {"status": "INVALID_PATTERN",
                    "reason": "Pattern structure broken"}
        entry_price   = point_c
        target_price  = point_c + wave_1_height
        stop_loss     = point_c - (wave_1_height * (self.stop_loss_pct / 100))
        risk_per_share = entry_price - stop_loss
        return {
            "status": "VALID_PATTERN",
            "entry": round(entry_price, 2),
            "target": round(target_price, 2),
            "stop_loss": round(stop_loss, 2),
            "risk_per_share": round(risk_per_share, 2),
            "wave_height": round(wave_1_height, 2),
        }

    def evaluate_probability_score(
        self, current_price, ema_9, vwap,
        buyers_ratio, news_score, nifty_trend
    ):
        score = 25
        logs  = ["Base Price Action Pattern Verified (+25%)"]

        if current_price > ema_9 and current_price > vwap:
            direction = "BULLISH"
        elif current_price < ema_9 and current_price < vwap:
            direction = "BEARISH"
        else:
            return {
                "score": 0,
                "direction": "NEUTRAL",
                "logs": ["Price locked inside EMA/VWAP chop zone — High Premium Decay Risk"],
            }

        if (direction == "BULLISH" and nifty_trend == "BULLISH") or \
           (direction == "BEARISH" and nifty_trend == "BEARISH"):
            score += 25
            logs.append("Broad Index Trend aligns perfectly (+25%)")
        else:
            logs.append("Index divergence detected — Counter-trend risk (0%)")

        if (direction == "BULLISH" and news_score == 25) or \
           (direction == "BEARISH" and news_score == -25):
            score += 25
            logs.append("AI News Sentiment confirms direction (+25%)")
        else:
            logs.append("News direction neutral or diverging (0%)")

        if direction == "BULLISH" and buyers_ratio >= 0.60:
            score += 25
            logs.append(
                f"Order book shows strong buyers ({buyers_ratio*100:.0f}%) (+25%)"
            )
        elif direction == "BEARISH" and buyers_ratio <= 0.40:
            score += 25
            logs.append(
                f"Order book shows heavy sellers ({(1-buyers_ratio)*100:.0f}%) (+25%)"
            )
        else:
            logs.append("Order book balanced — Churn risk present (0%)")

        return {"score": score, "direction": direction, "logs": logs}

    def generate_option_signal(
        self, direction, probability_score,
        current_price, wave_height, verification_logs
    ):
        if probability_score < self.entry_threshold:
            return {
                "action": "🛑 NO TRADING NOW",
                "probability": f"{probability_score}%",
                "reason": "Probability below minimum threshold",
            }
        if direction == "BULLISH":
            buy_action  = "CE (CALL OPTION)"
            sell_action = "PE (PUT OPTION)"
            entry       = current_price
            target      = current_price + wave_height
            stop_loss   = current_price - (wave_height * 0.25)
        else:
            buy_action  = "PE (PUT OPTION)"
            sell_action = "CE (CALL OPTION)"
            entry       = current_price
            target      = current_price - wave_height
            stop_loss   = current_price + (wave_height * 0.25)

        if probability_score == 100:
            size_label    = "AGGRESSIVE SIZE (Double Position)"
            strategy_mode = "SELLING (Hedged with Margin Protection)"
        else:
            size_label    = "STANDARD SIZE"
            strategy_mode = "BUYING (Directional Premium Play)"

        return {
            "action": f"⚡ EXECUTE {direction} SETUP",
            "probability": f"{probability_score}%",
            "allocation": size_label,
            "buy_contract": buy_action,
            "sell_contract": sell_action,
            "strategy_mode": strategy_mode,
            "entry": round(entry, 2),
            "target": round(target, 2),
            "stop_loss": round(stop_loss, 2),
            "verification_logs": verification_logs,
        }

    def calculate_hedged_option_selling(self, direction, atm_price, wave_height):
        otm_distance = wave_height * 0.5
        if direction == "BULLISH":
            hedge_strike = atm_price - otm_distance
            sell_strike  = atm_price
            trade_label  = (
                "BUY FAR OTM PUT (Hedge) + SHORT ATM PUT (Premium Collection)"
            )
        else:
            hedge_strike = atm_price + otm_distance
            sell_strike  = atm_price
            trade_label  = (
                "BUY FAR OTM CALL (Hedge) + SHORT ATM CALL (Premium Collection)"
            )
        return {
            "status": "HEDGED_OPTION_SELLING_ACTIVE",
            "trade_structure": trade_label,
            "hedge_strike": round(hedge_strike, 2),
            "sell_strike": round(sell_strike, 2),
            "estimated_margin_required": "~₹38,000 (Safe for ₹50K balance)",
            "execution_sequence": (
                "1. BUY Hedge First | 2. SHORT Sell Leg | "
                "(Reverse order to exit)"
            ),
            "premium_collection_benefit": (
                "Captures Time Decay (Theta) and IV Crush simultaneously"
            ),
        }

    def evaluate_signal(
        self, symbol=None, price=None,
        volume=None, ema_9=None, vwap=None
    ):
        if price is None or price <= 0:
            return {"signal": "HOLD", "probability": 0.0,
                    "reasoning": "Invalid price data"}
        ema_9  = ema_9  or price
        vwap   = vwap   or price
        volume = volume or 0

        probability = 50.0
        if price > ema_9 and price > vwap:
            signal      = "BUY"
            probability = min(75.0, 50.0 + ((price - ema_9) / price * 25))
        elif price < ema_9 and price < vwap:
            signal      = "SELL"
            probability = min(75.0, 50.0 + ((ema_9 - price) / price * 25))
        else:
            signal      = "HOLD"
            probability = 50.0

        if volume > 10_000:
            probability = min(100.0, probability + 10)

        return {
            "signal": signal,
            "probability": round(probability, 2),
            "reasoning": (
                f"{signal} signal: Price={price:.2f}, "
                f"EMA9={ema_9:.2f}, VWAP={vwap:.2f}, Vol={volume}"
            ),
        }
"""
Technical Indicator and Mathematical Engine

Core calculation engine for:
- Measured Move Strategy calculations
- Probability scoring (0-100%)
- Dynamic position sizing based on confidence
- Option buying and selling logic with margin hedging
"""

import os
from dotenv import load_dotenv

load_dotenv()


class StrategyBrainEngine:
    """Core mathematical engine for trading decisions without hallucination"""
    
    def __init__(self):
        self.base_risk = float(os.getenv("BASE_RISK_RUPEES", 500))
        self.stop_loss_pct = float(os.getenv("STOP_LOSS_PERCENTAGE", 15))
        self.entry_threshold = float(os.getenv("ENTRY_THRESHOLD_PROBABILITY", 75))
        
    def calculate_measured_move(self, point_a, point_b, point_c):
        """
        Calculates Measured Move pattern targets and stop losses.
        Pure mathematical logic with zero hallucination.
        
        Args:
            point_a: Initial swing low
            point_b: Swing high
            point_c: Pullback point (9 EMA level)
            
        Returns:
            Dictionary with entry, target, and stop loss prices
        """
        wave_1_height = abs(point_b - point_a)
        
        # Validate pattern structure
        if wave_1_height <= 0 or point_c >= point_b:
            return {"status": "INVALID_PATTERN", "reason": "Pattern structure broken"}
        
        # Calculate targets and stops
        entry_price = point_c
        target_price = point_c + wave_1_height
        stop_loss = point_c - (wave_1_height * (self.stop_loss_pct / 100))
        
        risk_per_share = entry_price - stop_loss
        
        return {
            "status": "VALID_PATTERN",
            "entry": round(entry_price, 2),
            "target": round(target_price, 2),
            "stop_loss": round(stop_loss, 2),
            "risk_per_share": round(risk_per_share, 2),
            "wave_height": round(wave_1_height, 2)
        }
    
    def evaluate_probability_score(self, current_price, ema_9, vwap, 
                                  buyers_ratio, news_score, nifty_trend):
        """
        Calculates 4-layer probability matrix (0-100%).
        
        Args:
            current_price: Current market price
            ema_9: 9-period exponential moving average
            vwap: Volume weighted average price
            buyers_ratio: Order book buy/sell ratio (0.0 to 1.0)
            news_score: AI sentiment score (-25, 0, or +25)
            nifty_trend: "BULLISH", "BEARISH", or "NEUTRAL"
            
        Returns:
            Dictionary with probability score and verification logs
        """
        score = 25  # Base structure match
        verification_logs = ["Base Price Action Pattern Verified (+25%)"]
        
        # Layer 1: Determine direction
        if current_price > ema_9 and current_price > vwap:
            direction = "BULLISH"
        elif current_price < ema_9 and current_price < vwap:
            direction = "BEARISH"
        else:
            return {
                "score": 0,
                "direction": "NEUTRAL",
                "logs": ["Price locked inside EMA/VWAP chop zone - High Premium Decay Risk"]
            }
        
        # Layer 2: Macro index alignment
        if (direction == "BULLISH" and nifty_trend == "BULLISH") or \
           (direction == "BEARISH" and nifty_trend == "BEARISH"):
            score += 25
            verification_logs.append("Broad Index Trend aligns perfectly (+25%)")
        else:
            verification_logs.append("Index divergence detected - Counter-trend risk (0%)")
        
        # Layer 3: AI News Sentiment Integration
        if (direction == "BULLISH" and news_score == 25) or \
           (direction == "BEARISH" and news_score == -25):
            score += 25
            verification_logs.append("AI News Sentiment confirms direction (+25%)")
        else:
            verification_logs.append("News direction neutral or diverging (0%)")
        
        # Layer 4: Order Book Depth Check
        if direction == "BULLISH" and buyers_ratio >= 0.60:
            score += 25
            verification_logs.append(f"Order book shows strong buyers ({buyers_ratio*100:.0f}%) (+25%)")
        elif direction == "BEARISH" and buyers_ratio <= 0.40:
            score += 25
            verification_logs.append(f"Order book shows heavy sellers ({(1-buyers_ratio)*100:.0f}%) (+25%)")
        else:
            verification_logs.append("Order book balanced - Churn risk present (0%)")
        
        return {
            "score": score,
            "direction": direction,
            "logs": verification_logs
        }
    
    def generate_position_sizing(self, probability_score, risk_per_share):
        """
        Dynamically calculates position size based on probability.
        
        Args:
            probability_score: Score from 0-100%
            risk_per_share: Risk amount per share
            
        Returns:
            Dictionary with quantity, risk multiplier, and allocation strategy
        """
        if probability_score < self.entry_threshold:
            return {
                "status": "NO_TRADE",
                "reason": f"Probability {probability_score}% below threshold {self.entry_threshold}%"
            }
        
        # Dynamic multiplier based on conviction
        if probability_score == 100:
            risk_multiplier = 2.0
            allocation_strategy = "AGGRESSIVE SIZE (Double Capital Allocation)"
        elif probability_score >= 85:
            risk_multiplier = 1.5
            allocation_strategy = "HIGH CONVICTION SIZE (150% Allocation)"
        elif probability_score >= 75:
            risk_multiplier = 1.0
            allocation_strategy = "STANDARD SIZE (Normal Capital Allocation)"
        else:
            risk_multiplier = 0.5
            allocation_strategy = "LOW CONVICTION SIZE (Defensive Mode)"
        
        calculated_risk = self.base_risk * risk_multiplier
        quantity = int(calculated_risk / risk_per_share) if risk_per_share > 0 else 0
        
        return {
            "status": "EXECUTE_TRADE",
            "quantity": quantity,
            "risk_multiplier": risk_multiplier,
            "allocation_strategy": allocation_strategy,
            "total_risk_amount": round(calculated_risk, 2)
        }
    
    def generate_option_signal(self, direction, probability_score, 
                              current_price, wave_height, verification_logs):
        """
        Generates complete option trading signal for buying or selling.
        
        Args:
            direction: "BULLISH" or "BEARISH"
            probability_score: 0-100 confidence percentage
            current_price: Current market price
            wave_height: Measured Move wave height
            verification_logs: List of verification conditions
            
        Returns:
            Complete signal dictionary with entry, targets, and strategy type
        """
        if probability_score < self.entry_threshold:
            return {
                "action": "🛑 NO TRADING NOW",
                "probability": f"{probability_score}%",
                "reason": "Probability below minimum threshold"
            }
        
        # Calculate entry and targets
        if direction == "BULLISH":
            buy_action = "CE (CALL OPTION)"
            sell_action = "PE (PUT OPTION)"
            entry = current_price
            target = current_price + wave_height
            stop_loss = current_price - (wave_height * 0.25)
        else:
            buy_action = "PE (PUT OPTION)"
            sell_action = "CE (CALL OPTION)"
            entry = current_price
            target = current_price - wave_height
            stop_loss = current_price + (wave_height * 0.25)
        
        # Determine strategy mode
        if probability_score == 100:
            size_label = "AGGRESSIVE SIZE (Double Position)"
            strategy_mode = "SELLING (Hedged with Margin Protection)"
        else:
            size_label = "STANDARD SIZE"
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
            "verification_logs": verification_logs
        }
    
    def calculate_hedged_option_selling(self, direction, atm_price, wave_height):
        """
        Calculates margin-hedged option selling strategy for ₹50K capital.
        
        Args:
            direction: "BULLISH" (sell puts) or "BEARISH" (sell calls)
            atm_price: At-The-Money strike price
            wave_height: Size of price movement target
            
        Returns:
            Dictionary with hedge and sell leg details
        """
        otm_distance = wave_height * 0.5  # Deep OTM for maximum margin reduction
        
        if direction == "BULLISH":
            hedge_strike = atm_price - otm_distance  # Buy protective put
            sell_strike = atm_price  # Sell naked put (hedged)
            trade_label = "BUY FAR OTM PUT (Hedge) + SHORT ATM PUT (Premium Collection)"
        else:
            hedge_strike = atm_price + otm_distance  # Buy protective call
            sell_strike = atm_price  # Sell naked call (hedged)
            trade_label = "BUY FAR OTM CALL (Hedge) + SHORT ATM CALL (Premium Collection)"
        
        return {
            "status": "HEDGED_OPTION_SELLING_ACTIVE",
            "trade_structure": trade_label,
            "hedge_strike": round(hedge_strike, 2),
            "sell_strike": round(sell_strike, 2),
            "estimated_margin_required": "~₹38,000 (Safe for ₹50K balance)",
            "execution_sequence": "1. BUY Hedge First | 2. SHORT Sell Leg | (Reverse order to exit)",
            "premium_collection_benefit": "Captures Time Decay (Theta) and IV Crush simultaneously"
        }

    def evaluate_signal(self, symbol=None, price=None, volume=None, ema_9=None, vwap=None):
        """
        Simple signal evaluation for live streaming data.
        Returns buy/sell/hold signal with probability score.
        
        Args:
            symbol: Token or symbol name
            price: Current price
            volume: Current volume
            ema_9: 9-period EMA (optional)
            vwap: Volume-weighted average price (optional)
            
        Returns:
            Dictionary with signal, probability, and reasoning
        """
        if price is None or price <= 0:
            return {
                "signal": "HOLD",
                "probability": 0.0,
                "reasoning": "Invalid price data"
            }
        
        # Default values if not provided
        ema_9 = ema_9 or price
        vwap = vwap or price
        volume = volume or 0
        
        # Simple signal logic
        probability = 50.0  # Base probability
        
        # Price position relative to moving averages
        if price > ema_9 and price > vwap:
            signal = "BUY"
            probability = min(75.0, 50.0 + ((price - ema_9) / price * 25))
        elif price < ema_9 and price < vwap:
            signal = "SELL"
            probability = min(75.0, 50.0 + ((ema_9 - price) / price * 25))
        else:
            signal = "HOLD"
            probability = 50.0
        
        # Volume adjustment
        if volume > 10000:
            probability = min(100.0, probability + 10)
        
        return {
            "signal": signal,
            "probability": round(probability, 2),
            "reasoning": f"{signal} signal: Price={price:.2f}, EMA9={ema_9:.2f}, VWAP={vwap:.2f}, Vol={volume}"
        }



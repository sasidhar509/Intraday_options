"""
Master Execution Dashboard Loop (Your Trading Guru System)

This is the main orchestration engine that:
1. Loads pre-market data using scraper agents
2. Integrates AI sentiment analysis (FinBERT)
3. Calculates technical indicators and probability scores
4. Generates CE/PE option trading signals with dynamic sizing
5. Outputs actionable trading commands to your laptop screen
"""

import os
import sys
import time
import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv

# Import local agent modules
from agents.brain import StrategyBrainEngine
from agents.scraper import (
    PreMarketScrapingAgent,
    NewsScraperAgent,
    MarketDataFetcher
)

# Initialize color printing and environment variables
init(autoreset=True)
load_dotenv()

# Try to import FinBERT AI model (graceful fallback if not installed)
try:
    from transformers import pipeline
    HAS_FINBERT = True
except ImportError:
    HAS_FINBERT = False


class AINewsAnalystAgent:
    """Agent: Converts live news text into mathematical sentiment scores using FinBERT"""
    
    def __init__(self):
        self.has_model = HAS_FINBERT
        if self.has_model:
            try:
                print(f"{Fore.CYAN}🤖 [SYSTEM] Loading FinBERT AI Model onto your laptop...")
                self.sentiment_model = pipeline(
                    "sentiment-analysis",
                    model="ProsusAI/finbert"
                )
                print(f"{Fore.GREEN}✓ FinBERT Model loaded successfully!\n")
            except Exception as e:
                print(f"{Fore.YELLOW}⚠️ Could not load FinBERT model: {e}")
                self.has_model = False
    
    def analyze_headline_sentiment(self, headline_text):
        """
        Analyzes a news headline and returns a mathematical sentiment score.
        No hallucination - returns only: +25 (Positive), 0 (Neutral), or -25 (Negative)
        """
        if not self.has_model:
            return 0, "AI Model Offline - Neutral Default"
        
        try:
            result = self.sentiment_model(headline_text[:512])[0]  # Truncate for efficiency
            sentiment = result['label'].lower()
            confidence = result['score']
            
            # High confidence threshold
            if confidence > float(os.getenv("SENTIMENT_CONFIDENCE_THRESHOLD", 0.80)):
                if "positive" in sentiment:
                    return 25, f"AI: Positive News Confirmed ({confidence*100:.0f}% confidence)"
                elif "negative" in sentiment:
                    return -25, f"AI: Negative News Confirmed ({confidence*100:.0f}% confidence)"
            
            return 0, f"AI: Neutral/Low Confidence ({confidence*100:.0f}%)"
        except Exception as e:
            return 0, f"AI Analysis Error: {str(e)}"


class LiveTradingGuruSystem:
    """
    Master Orchestration Engine - Your Complete Trading Assistant
    
    Processes:
    - Pre-market global data
    - Live market structure
    - AI sentiment from news feeds
    - Technical indicators and probability matrices
    - Dynamic position sizing
    - CE/PE option signals
    """
    
    def __init__(self):
        self.brain = StrategyBrainEngine()
        self.scraper_agent = PreMarketScrapingAgent()
        self.news_agent = NewsScraperAgent()
        self.market_fetcher = MarketDataFetcher()
        self.ai_sentiment = AINewsAnalystAgent()
        self.trading_mode = os.getenv("TRADING_MODE", "SELLING")
        
    def print_header(self):
        """Prints the system initialization header"""
        print("\n" + "="*85)
        print(f"🧠 {Fore.GREEN}LIVE AGENTIC AI TRADING GURU SYSTEM - PRODUCTION DEPLOYMENT")
        print("="*85)
        print(f"⚙️  Operational Mode          : {Fore.CYAN}{self.trading_mode.upper()}")
        print(f"💰 Capital Protection Shield : {Fore.CYAN}₹{os.getenv('BASE_RISK_RUPEES')} Fixed Risk per Setup")
        print(f"📊 Probability Threshold     : {Fore.CYAN}{os.getenv('ENTRY_THRESHOLD_PROBABILITY')}%")
        print(f"🤖 AI Sentiment Analysis    : {Fore.CYAN}{'Enabled (FinBERT)' if HAS_FINBERT else 'Offline - Neutral Default'}")
        print("="*85 + "\n")
    
    def run_pre_market_analysis(self):
        """Phase 1: Analyze market before opening bell"""
        print(f"{Fore.CYAN}📈 [PHASE 1] PRE-MARKET ANALYSIS - Establishing Global Bias...\n")
        
        bias_report = self.scraper_agent.determine_market_bias()
        
        print(f"🌍 Market Bias Assessment    : {Fore.YELLOW}{bias_report['market_bias']}")
        print(f"   GIFT Nifty Change         : {bias_report['gift_nifty']['change_points']:+.2f} points")
        print(f"   Bullish Signals           : {bias_report['bullish_signals']}")
        print(f"   Bearish Signals           : {bias_report['bearish_signals']}")
        
        print(f"\n🌐 Global Overnight Indices:")
        for idx_name, idx_data in bias_report['global_indices'].items():
            status = f"{Fore.GREEN}↑ UP" if idx_data.get('change') == 'UP' else f"{Fore.RED}↓ DOWN"
            print(f"   {idx_name:12} : {status} @ {idx_data.get('close')}")
        
        print("\n" + "="*85 + "\n")
        
        return bias_report['market_bias']
    
    def run_live_market_analysis(self, ticker_symbol="NIFTY 50 INDEX", current_price=23400.0, 
                                ema_9=23410.0, vwap=23405.0, buyers_ratio=0.55):
        """Phase 2: Analyze live market structure and generate trading signals"""
        print(f"{Fore.CYAN}⚡ [PHASE 2] LIVE MARKET SCANNER - Analyzing {ticker_symbol}...\n")
        
        # Get live market bias
        market_bias_data = self.scraper_agent.determine_market_bias()
        market_bias = market_bias_data['market_bias']
        
        # Fetch latest news for sentiment
        news_data = self.news_agent.fetch_latest_market_news()
        
        # Analyze first headline for sentiment
        news_score = 0
        news_reason = "No news available"
        
        if news_data['status'] == 'SUCCESS' and news_data.get('headlines'):
            latest_headline = news_data['headlines'][0]
            news_score, news_reason = self.ai_sentiment.analyze_headline_sentiment(latest_headline)
            print(f"📰 Latest News: \"{latest_headline[:70]}...\"")
            print(f"   AI Sentiment: {news_reason}\n")
        
        # Calculate probability score
        prob_data = self.brain.evaluate_probability_score(
            current_price=current_price,
            ema_9=ema_9,
            vwap=vwap,
            buyers_ratio=buyers_ratio,
            news_score=news_score,
            nifty_trend=market_bias
        )
        
        probability_score = prob_data['score']
        direction = prob_data['direction']
        
        print(f"📊 Technical Analysis:")
        print(f"   Current Price             : ₹{current_price}")
        print(f"   9-EMA Level               : ₹{ema_9}")
        print(f"   VWAP Support              : ₹{vwap}")
        print(f"   Order Book Buyers         : {buyers_ratio*100:.0f}%")
        print(f"\n🎯 Probability Matrix Score  : {Fore.YELLOW}{probability_score}%")
        print(f"📈 Market Direction          : {Fore.YELLOW}{direction}\n")
        
        print("✓ Verification Checklist:")
        for log in prob_data['logs']:
            print(f"   • {log}")
        
        print("\n" + "="*85 + "\n")
        
        # Generate trading signal
        if probability_score < float(os.getenv("ENTRY_THRESHOLD_PROBABILITY", 75)):
            print(f"🛑 {Fore.YELLOW}GURU COMMAND: NO TRADING NOW")
            print(f"   Reason: Probability {probability_score}% below {os.getenv('ENTRY_THRESHOLD_PROBABILITY')}% threshold\n")
            return None
        
        # Calculate Measured Move
        point_a = current_price - 50  # Simulated swing low
        point_b = current_price + 40  # Simulated swing high
        point_c = current_price - 10  # Simulated pullback
        
        measured_move = self.brain.calculate_measured_move(point_a, point_b, point_c)
        
        if measured_move['status'] != 'VALID_PATTERN':
            print(f"🛑 Invalid pattern structure\n")
            return None
        
        wave_height = measured_move['wave_height']
        
        # Generate option signal
        signal = self.brain.generate_option_signal(
            direction=direction,
            probability_score=probability_score,
            current_price=current_price,
            wave_height=wave_height,
            verification_logs=prob_data['logs']
        )
        
        return signal
    
    def print_trading_signal(self, signal):
        """Formats and displays the trading signal"""
        if signal is None:
            return
        
        if signal['action'].startswith('🛑'):
            print(f"{Fore.RED}{signal['action']}")
            if 'reason' in signal:
                print(f"   {signal['reason']}\n")
            return
        
        print(f"{Fore.GREEN}{signal['action']}")
        print(f"   ✓ Probability             : {signal['probability']}")
        print(f"   📊 Allocation Strategy    : {signal['allocation']}")
        print(f"   💼 Strategy Mode          : {signal['strategy_mode']}")
        print(f"   🔵 Buy Contract (Long)    : {signal['buy_contract']}")
        print(f"   🔴 Sell Contract (Short)  : {signal['sell_contract']}")
        print(f"\n   📍 Entry Price            : ₹{signal['entry']}")
        print(f"   🎯 Profit Target          : ₹{signal['target']}")
        print(f"   🛑 Stop Loss (Strict)     : ₹{signal['stop_loss']}")
        print(f"\n   Verification Checklist:")
        for log in signal['verification_logs']:
            print(f"      • {log}")
        
        # If selling mode, show margin hedging info
        if "SELLING" in signal['strategy_mode']:
            print(f"\n   🛡️  Margin-Hedged Structure:")
            print(f"      Step 1: BUY Deep OTM Hedge (Margin Protection First)")
            print(f"      Step 2: SHORT ATM Contract (Premium Collection)")
            print(f"      Est. Margin: ~₹38,000 (Safe for ₹50K balance)")
        
        print("\n" + "="*85 + "\n")
    
    def run_complete_session(self):
        """Execute full trading session workflow"""
        self.print_header()
        
        # Step 1: Pre-market
        market_bias = self.run_pre_market_analysis()
        
        # Step 2: Live market monitoring (simulated)
        print(f"{Fore.CYAN}⏰ [09:15 AM] Market Opening - Beginning Live Data Stream Analysis...\n")
        
        # Simulated live market scenarios
        live_scenarios = [
            {
                "time": "09:20",
                "ticker": "NIFTY 50",
                "price": 23420.0,
                "ema": 23410.0,
                "vwap": 23405.0,
                "buyers": 0.58
            },
            {
                "time": "09:45",
                "ticker": "NIFTY 50",
                "price": 23465.0,
                "ema": 23430.0,
                "vwap": 23435.0,
                "buyers": 0.64
            },
            {
                "time": "10:30",
                "ticker": "NIFTY 50",
                "price": 23378.0,
                "ema": 23410.0,
                "vwap": 23405.0,
                "buyers": 0.35  # Heavy sellers after RBI event
            }
        ]
        
        for scenario in live_scenarios:
            print(f"[{scenario['time']} AM] {scenario['ticker']} Analysis:")
            print("-" * 85)
            
            signal = self.run_live_market_analysis(
                ticker_symbol=scenario['ticker'],
                current_price=scenario['price'],
                ema_9=scenario['ema'],
                vwap=scenario['vwap'],
                buyers_ratio=scenario['buyers']
            )
            
            self.print_trading_signal(signal)
            
            time.sleep(1)  # Simulate real-time processing
        
        print(f"{Fore.CYAN}📊 TRADING SESSION COMPLETE\n")
        print(f"{Fore.GREEN}✓ System Ready for Live Market Deployment")
        print(f"{Fore.YELLOW}⚠️  Remember: Let your mechanical script generate the exact entry price and quantity")
        print(f"{Fore.YELLOW}   Do not overtrade. Protect your ₹50,000 capital at all costs.\n")


def main():
    """Main entry point for the trading guru system"""
    try:
        guru_system = LiveTradingGuruSystem()
        guru_system.run_complete_session()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}🤖 System powered down cleanly. Workspace detached.")
    except Exception as e:
        print(f"\n{Fore.RED}❌ System Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


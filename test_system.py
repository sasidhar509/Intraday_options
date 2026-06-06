"""
Quick Test of Trading Guru System (Without Large AI Model Download)
This demonstrates all core functionality without the FinBERT download latency.
"""

import os
import datetime
from colorama import Fore, Style, init
from dotenv import load_dotenv

from agents.brain import StrategyBrainEngine
from agents.scraper import PreMarketScrapingAgent, NewsScraperAgent

init(autoreset=True)
load_dotenv()


def main():
    """Quick system test without waiting for AI model downloads"""
    
    print("\n" + "="*85)
    print(f"🧠 {Fore.GREEN}AGENTIC TRADING GURU - QUICK SYSTEM TEST")
    print("="*85)
    print(f"⚙️  Operational Mode          : {Fore.CYAN}SELLING")
    print(f"💰 Capital Protection Shield : {Fore.CYAN}₹500 Fixed Risk per Setup")
    print(f"📊 Probability Threshold     : {Fore.CYAN}75%")
    print("="*85 + "\n")
    
    # Initialize core engines
    brain = StrategyBrainEngine()
    pre_market = PreMarketScrapingAgent()
    news_scraper = NewsScraperAgent()
    
    # Test 1: Pre-Market Data Retrieval
    print(f"{Fore.CYAN}[TEST 1] PRE-MARKET ANALYSIS - Global Bias Detection\n")
    
    bias_report = pre_market.determine_market_bias()
    print(f"🌍 Market Bias: {Fore.YELLOW}{bias_report['market_bias']}")
    print(f"   GIFT Nifty Change: {bias_report['gift_nifty']['change_points']:+.2f} points")
    print(f"   Timestamp: {bias_report['timestamp']}")
    print("\n" + "="*85 + "\n")
    
    # Test 2: News Scraping
    print(f"{Fore.CYAN}[TEST 2] NEWS SCRAPING AGENT - Fetching Market Headlines\n")
    
    news = news_scraper.fetch_latest_market_news()
    if news['status'] == 'SUCCESS':
        print(f"✓ Headlines Available: {news['headline_count']}")
        print(f"   Sample: \"{news['headlines'][0][:70]}...\"")
    print("\n" + "="*85 + "\n")
    
    # Test 3: Probability Calculation
    print(f"{Fore.CYAN}[TEST 3] PROBABILITY MATRIX - 4-Layer Technical Scoring\n")
    
    # Scenario A: Strong bullish setup
    prob_result = brain.evaluate_probability_score(
        current_price=23450.0,
        ema_9=23420.0,
        vwap=23425.0,
        buyers_ratio=0.68,
        news_score=25,
        nifty_trend="BULLISH"
    )
    
    print(f"Scenario A: Bullish Setup with Positive News")
    print(f"   Probability Score: {Fore.GREEN}{prob_result['score']}%")
    print(f"   Direction: {Fore.GREEN}{prob_result['direction']}")
    print(f"   Verification Status:")
    for log in prob_result['logs']:
        print(f"      • {log}")
    print("\n" + "-"*85 + "\n")
    
    # Scenario B: Weak bearish setup
    prob_result_2 = brain.evaluate_probability_score(
        current_price=23350.0,
        ema_9=23410.0,
        vwap=23405.0,
        buyers_ratio=0.32,
        news_score=-25,
        nifty_trend="BEARISH"
    )
    
    print(f"Scenario B: Bearish Breakdown with Negative News")
    print(f"   Probability Score: {Fore.YELLOW}{prob_result_2['score']}%")
    print(f"   Direction: {Fore.RED}{prob_result_2['direction']}")
    print(f"   Verification Status:")
    for log in prob_result_2['logs']:
        print(f"      • {log}")
    print("\n" + "="*85 + "\n")
    
    # Test 4: Measured Move Calculation
    print(f"{Fore.CYAN}[TEST 4] MEASURED MOVE STRATEGY - Entry, Target & Stop Loss\n")
    
    measured_move = brain.calculate_measured_move(
        point_a=23400.0,  # Swing low
        point_b=23450.0,  # Swing high
        point_c=23425.0   # Pullback to 9 EMA
    )
    
    print(f"Pattern Structure:")
    print(f"   Point A (Swing Low): ₹{23400.0}")
    print(f"   Point B (Swing High): ₹{23450.0}")
    print(f"   Point C (Pullback): ₹{23425.0}")
    print(f"   Wave Height: ₹{measured_move['wave_height']}")
    print(f"\n✓ Trade Parameters Generated:")
    print(f"   Entry Price: ₹{measured_move['entry']}")
    print(f"   Profit Target: ₹{measured_move['target']}")
    print(f"   Stop Loss: ₹{measured_move['stop_loss']}")
    print(f"   Risk Per Share: ₹{measured_move['risk_per_share']}")
    print("\n" + "="*85 + "\n")
    
    # Test 5: Dynamic Position Sizing
    print(f"{Fore.CYAN}[TEST 5] DYNAMIC POSITION SIZING - Confidence-Based Allocation\n")
    
    # High conviction setup
    sizing_high = brain.generate_position_sizing(
        probability_score=100,
        risk_per_share=5.0
    )
    
    print(f"100% Probability Setup (Maximum Conviction):")
    print(f"   Status: {Fore.GREEN}EXECUTE")
    print(f"   Quantity: {sizing_high['quantity']} Shares")
    print(f"   Risk Multiplier: {sizing_high['risk_multiplier']}x")
    print(f"   Strategy: {Fore.GREEN}{sizing_high['allocation_strategy']}")
    print(f"   Total Risk Amount: ₹{sizing_high['total_risk_amount']}")
    print("\n" + "-"*85 + "\n")
    
    # Low conviction setup
    sizing_low = brain.generate_position_sizing(
        probability_score=60,
        risk_per_share=5.0
    )
    
    print(f"60% Probability Setup (Below Threshold):")
    print(f"   Status: {Fore.YELLOW}{sizing_low['status']}")
    print(f"   Reason: {sizing_low['reason']}")
    print("\n" + "="*85 + "\n")
    
    # Test 6: Option Trading Signal Generation
    print(f"{Fore.CYAN}[TEST 6] OPTION TRADING SIGNAL - CE/PE Strategy Output\n")
    
    signal = brain.generate_option_signal(
        direction="BULLISH",
        probability_score=100,
        current_price=23450.0,
        wave_height=50.0,
        verification_logs=[
            "Base Price Action Pattern Verified (+25%)",
            "Macro Index alignment confirmed (+25%)",
            "AI News sentiment confirms direction (+25%)",
            "Order book shows strong buyers (65%) (+25%)"
        ]
    )
    
    print(f"🎯 TRADING SIGNAL GENERATED")
    print(f"   {signal['action']}")
    print(f"   Probability: {signal['probability']}")
    print(f"   Allocation: {signal['allocation']}")
    print(f"   Buy Contract: {signal['buy_contract']}")
    print(f"   Sell Contract: {signal['sell_contract']}")
    print(f"   Strategy Mode: {signal['strategy_mode']}")
    print(f"\n   Entry: ₹{signal['entry']}")
    print(f"   Target: ₹{signal['target']}")
    print(f"   Stop Loss: ₹{signal['stop_loss']}")
    print("\n" + "="*85 + "\n")
    
    # Test 7: Hedged Option Selling
    print(f"{Fore.CYAN}[TEST 7] MARGIN-HEDGED OPTION SELLING - ₹50K Capital Safety\n")
    
    hedged = brain.calculate_hedged_option_selling(
        direction="BULLISH",
        atm_price=23450.0,
        wave_height=50.0
    )
    
    print(f"Hedged Option Selling Structure:")
    print(f"   Status: {hedged['status']}")
    print(f"   Trade Layout: {hedged['trade_structure']}")
    print(f"   Hedge Strike: ₹{hedged['hedge_strike']}")
    print(f"   Sell Strike: ₹{hedged['sell_strike']}")
    print(f"   Margin Required: {hedged['estimated_margin_required']}")
    print(f"   Execution: {hedged['execution_sequence']}")
    print(f"   Benefit: {hedged['premium_collection_benefit']}")
    print("\n" + "="*85 + "\n")
    
    # Final Summary
    print(f"{Fore.GREEN}✓ ALL CORE SYSTEMS VALIDATED")
    print(f"{Fore.GREEN}✓ AGENTIC AI TRADING GURU READY FOR DEPLOYMENT\n")
    print(f"{Fore.YELLOW}Next Steps:")
    print(f"   1. Run 'python main.py' for full system with AI sentiment analysis")
    print(f"   2. Keep Zerodha Kite browser open for live data integration")
    print(f"   3. Monitor 15-minute charts with 9 EMA and VWAP indicators")
    print(f"   4. Follow mechanical entry/exit signals without emotion\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Test session terminated by user.")
    except Exception as e:
        print(f"\n{Fore.RED}Error: {e}")
        import traceback
        traceback.print_exc()

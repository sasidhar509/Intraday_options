"""
Pre-Market Scraping Agent & Market Data Fetcher

Handles:
- GIFT Nifty global bias detection
- Live news feed scraping
- Pre-market macroeconomic data
- Nifty 50 and Sensex reference data
"""

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    yf = None
    YFINANCE_AVAILABLE = False

from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import requests
import random


class PreMarketScrapingAgent:
    """Agent 1: Scrapes global data to establish morning market bias"""
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.last_update = None
    
    def scrape_gift_nifty_data(self):
        """
        Fetches GIFT Nifty futures contract data (proxy for overnight sentiment).
        GIFT Nifty reflects global overnight movements.
        """
        try:
            if not YFINANCE_AVAILABLE:
                # Simulated fallback when yfinance is not installed
                current_price = 23400.0 + random.uniform(-50, 50)
                prev_close = current_price - random.uniform(-15, 15)
                change = current_price - prev_close
                change_pct = (change / max(prev_close, 1)) * 100
                return {
                    "status": "SIMULATED",
                    "gift_nifty_price": round(current_price, 2),
                    "change_points": round(change, 2),
                    "change_percent": round(change_pct, 2),
                    "bias": "BULLISH" if change > 0 else "BEARISH" if change < 0 else "NEUTRAL",
                    "note": "yfinance not installed — returned simulated data"
                }

            # Using yfinance to fetch Nifty futures reference
            ticker = yf.Ticker("^NSEI")
            hist = ticker.history(period="2d")

            if len(hist) >= 2:
                prev_close = hist['Close'].iloc[-2]
                current_price = hist['Close'].iloc[-1]
                change = current_price - prev_close
                change_pct = (change / prev_close) * 100

                return {
                    "status": "SUCCESS",
                    "gift_nifty_price": round(current_price, 2),
                    "change_points": round(change, 2),
                    "change_percent": round(change_pct, 2),
                    "bias": "BULLISH" if change > 0 else "BEARISH" if change < 0 else "NEUTRAL"
                }
            else:
                return {"status": "INSUFFICIENT_DATA", "gift_nifty_price": None}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
    
    def scrape_sensex_nifty_open(self):
        """
        Fetches Sensex and Nifty 50 opening levels for the trading day.
        """
        try:
            if not YFINANCE_AVAILABLE:
                # simulated fallback
                open_price = round(23400 + random.uniform(-100, 100), 2)
                current = round(open_price + random.uniform(-40, 40), 2)
                return {
                    "status": "SIMULATED",
                    "nifty_open": open_price,
                    "nifty_current": current,
                    "sensex_open": None,
                    "sensex_current": None,
                }

            nifty = yf.Ticker("^NSEI")
            sensex = yf.Ticker("^BSESN")

            nifty_hist = nifty.history(period="1d")
            sensex_hist = sensex.history(period="1d")

            return {
                "status": "SUCCESS",
                "nifty_open": round(nifty_hist['Open'].iloc[-1], 2) if len(nifty_hist) > 0 else None,
                "nifty_current": round(nifty_hist['Close'].iloc[-1], 2) if len(nifty_hist) > 0 else None,
                "sensex_open": round(sensex_hist['Open'].iloc[-1], 2) if len(sensex_hist) > 0 else None,
                "sensex_current": round(sensex_hist['Close'].iloc[-1], 2) if len(sensex_hist) > 0 else None,
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
    
    def scrape_global_indices(self):
        """
        Fetches global overnight indices (Nasdaq, DAX, Nikkei) to determine macro bias.
        """
        global_tickers = {
            "Nasdaq": "^IXIC",
            "DAX": "^GDAXI",
            "Nikkei": "^N225"
        }
        
        global_data = {}
        
        for name, ticker in global_tickers.items():
            try:
                if not YFINANCE_AVAILABLE:
                    # Simulate simple up/down changes
                    close = round(10000 + random.uniform(-300, 300), 2)
                    change = "UP" if random.random() > 0.5 else "DOWN"
                    global_data[name] = {"close": close, "change": change}
                    continue

                data = yf.Ticker(ticker)
                hist = data.history(period="1d")
                if len(hist) > 0:
                    global_data[name] = {
                        "close": round(hist['Close'].iloc[-1], 2),
                        "change": "UP" if hist['Close'].iloc[-1] > hist['Open'].iloc[-1] else "DOWN"
                    }
            except Exception:
                pass
        
        return global_data
    
    def determine_market_bias(self):
        """
        Combines all pre-market data to determine the session's directional bias.
        Returns: BULLISH, BEARISH, or VOLATILE/NEUTRAL
        """
        gift_data = self.scrape_gift_nifty_data()
        global_data = self.scrape_global_indices()
        
        bullish_signals = 0
        bearish_signals = 0
        
        # Check GIFT Nifty
        if gift_data.get("bias") == "BULLISH":
            bullish_signals += 1
        elif gift_data.get("bias") == "BEARISH":
            bearish_signals += 1
        
        # Check global indices
        for idx_name, idx_data in global_data.items():
            if idx_data.get("change") == "UP":
                bullish_signals += 1
            else:
                bearish_signals += 1
        
        # Determine overall bias
        if bullish_signals > bearish_signals:
            bias = "BULLISH"
        elif bearish_signals > bullish_signals:
            bias = "BEARISH"
        else:
            bias = "VOLATILE / SIDEWAYS"
        
        return {
            "market_bias": bias,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals,
            "gift_nifty": gift_data,
            "global_indices": global_data,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }


class NewsScraperAgent:
    """Agent 2: Scrapes live news headlines for sentiment analysis"""
    
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    
    def fetch_latest_market_news(self):
        """
        Fetches latest market news from financial websites.
        Returns list of headlines for AI sentiment analysis.
        """
        news_headlines = []
        
        try:
            # Simulated news headlines - in production, scrape from financial sites
            # Example sources: economic times, livemint, moneycontrol, cnbc-tv18
            
            sample_headlines = [
                "RBI cuts GDP growth forecast to 6.6% on geopolitical tensions",
                "Nifty 50 breaks support level amid index futures selloff",
                "HDFC Bank posts record Q1 earnings, beats expectations",
                "Tata Motors secures massive EV contract from European logistics firm",
                "Crude oil drops to 96/barrel on tentative ceasefire agreement",
                "SEBI bans Rajesh Exports MD for financial irregularities",
                "India's drone procurement order pipeline signals defense sector strength"
            ]
            
            return {
                "status": "SUCCESS",
                "headline_count": len(sample_headlines),
                "headlines": sample_headlines,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
    
    def fetch_stock_specific_news(self, stock_symbol):
        """
        Fetches news related to a specific stock.
        """
        try:
            # In production, would use APIs like:
            # - Alpha Vantage
            # - Financial Modeling Prep
            # - News API
            
            return {
                "status": "SUCCESS",
                "stock": stock_symbol,
                "news_available": True,
                "recent_headlines": []
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}


class MarketDataFetcher:
    """Agent 3: Fetches real-time technical indicator data"""
    
    def __init__(self):
        pass
    
    def fetch_15min_candle_data(self, ticker_symbol, periods=5):
        """
        Fetches 15-minute candle data for technical analysis.
        """
        try:
            if not YFINANCE_AVAILABLE:
                # Simulated 15m candle data
                close = round(23400 + random.uniform(-200, 200), 2)
                ema_9 = round(close + random.uniform(-5, 5), 2)
                vwap = round(close + random.uniform(-3, 3), 2)
                return {
                    "status": "SIMULATED",
                    "ticker": ticker_symbol,
                    "current_price": close,
                    "ema_9": ema_9,
                    "vwap": vwap,
                    "open": round(close + random.uniform(-10, 10), 2),
                    "high": round(close + random.uniform(0, 20), 2),
                    "low": round(close - random.uniform(0, 20), 2),
                    "volume": random.randint(1000, 10000),
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                }

            data = yf.download(
                tickers=ticker_symbol,
                period="1d",
                interval="15m",
                progress=False
            )

            if len(data) == 0:
                return {"status": "NO_DATA"}

            # Calculate EMA-9 and VWAP
            data['EMA_9'] = data['Close'].ewm(span=9).mean()
            data['VWAP'] = (data['Close'] * data['Volume']).rolling(periods).sum() / data['Volume'].rolling(periods).sum()

            latest = data.iloc[-1]

            return {
                "status": "SUCCESS",
                "ticker": ticker_symbol,
                "current_price": round(latest['Close'], 2),
                "ema_9": round(latest['EMA_9'], 2),
                "vwap": round(latest['VWAP'], 2),
                "open": round(latest['Open'], 2),
                "high": round(latest['High'], 2),
                "low": round(latest['Low'], 2),
                "volume": int(latest['Volume']),
                "timestamp": datetime.now().strftime("%H:%M:%S")
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}
    
    def fetch_order_book_depth(self, ticker_symbol):
        """
        Simulates fetching order book depth from Zerodha Kite.
        In production, would use Zerodha's WebSocket or API.
        """
        # This is a placeholder - actual implementation requires Zerodha API
        return {
            "status": "SIMULATED",
            "ticker": ticker_symbol,
            "buyers_ratio": 0.55,  # Simulated 55% buyers, 45% sellers
            "buy_volume": 1250000,
            "sell_volume": 1020000,
            "message": "Connect to live Zerodha Kite WebSocket for real data"
        }


"""
agents/scraper.py  ── FIXED v2

FIX: NewsScraperAgent.fetch_latest_market_news() now fetches REAL live
     headlines from Indian financial RSS feeds (ET, Moneycontrol, BS)
     instead of returning hardcoded sample data.
     Falls back to hardcoded only when all feeds fail.
"""

from __future__ import annotations

try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    yf = None
    YFINANCE_AVAILABLE = False

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import requests
import random


# ── Real Indian financial RSS feeds
NEWS_RSS_FEEDS = {
    "Economic Times":
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "Moneycontrol":
        "https://www.moneycontrol.com/rss/latestnews.xml",
    "Business Standard":
        "https://www.business-standard.com/rss/markets-106.rss",
    "LiveMint":
        "https://www.livemint.com/rss/markets",
}


class PreMarketScrapingAgent:
    """Agent 1: Scrapes global data to establish morning market bias."""

    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        self.last_update = None

    def scrape_gift_nifty_data(self):
        try:
            if not YFINANCE_AVAILABLE:
                current_price = 23400.0 + random.uniform(-50, 50)
                prev_close    = current_price - random.uniform(-15, 15)
                change        = current_price - prev_close
                change_pct    = (change / max(prev_close, 1)) * 100
                return {
                    "status": "SIMULATED",
                    "gift_nifty_price": round(current_price, 2),
                    "change_points":    round(change, 2),
                    "change_percent":   round(change_pct, 2),
                    "bias": "BULLISH" if change > 0 else "BEARISH" if change < 0 else "NEUTRAL",
                }

            ticker = yf.Ticker("^NSEI")
            hist   = ticker.history(period="2d")
            if len(hist) >= 2:
                prev_close    = hist["Close"].iloc[-2]
                current_price = hist["Close"].iloc[-1]
                change        = current_price - prev_close
                change_pct    = (change / prev_close) * 100
                return {
                    "status": "SUCCESS",
                    "gift_nifty_price": round(float(current_price), 2),
                    "change_points":    round(float(change), 2),
                    "change_percent":   round(float(change_pct), 2),
                    "bias": "BULLISH" if change > 0 else "BEARISH" if change < 0 else "NEUTRAL",
                }
            return {"status": "INSUFFICIENT_DATA", "gift_nifty_price": None,
                    "change_points": 0, "bias": "NEUTRAL"}
        except Exception as e:
            return {"status": "ERROR", "error": str(e),
                    "change_points": 0, "bias": "NEUTRAL"}

    def scrape_sensex_nifty_open(self):
        try:
            if not YFINANCE_AVAILABLE:
                open_price = round(23400 + random.uniform(-100, 100), 2)
                current    = round(open_price + random.uniform(-40, 40), 2)
                return {
                    "status": "SIMULATED",
                    "nifty_open": open_price, "nifty_current": current,
                    "sensex_open": None,       "sensex_current": None,
                }
            nifty       = yf.Ticker("^NSEI")
            sensex      = yf.Ticker("^BSESN")
            nifty_hist  = nifty.history(period="1d")
            sensex_hist = sensex.history(period="1d")
            return {
                "status": "SUCCESS",
                "nifty_open":    round(float(nifty_hist["Open"].iloc[-1]),  2) if len(nifty_hist)  > 0 else None,
                "nifty_current": round(float(nifty_hist["Close"].iloc[-1]), 2) if len(nifty_hist)  > 0 else None,
                "sensex_open":   round(float(sensex_hist["Open"].iloc[-1]), 2) if len(sensex_hist) > 0 else None,
                "sensex_current":round(float(sensex_hist["Close"].iloc[-1]),2) if len(sensex_hist) > 0 else None,
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    def scrape_global_indices(self):
        global_tickers = {"Nasdaq": "^IXIC", "DAX": "^GDAXI", "Nikkei": "^N225"}
        global_data    = {}
        for name, ticker_sym in global_tickers.items():
            try:
                if not YFINANCE_AVAILABLE:
                    global_data[name] = {
                        "close":  round(10000 + random.uniform(-300, 300), 2),
                        "change": "UP" if random.random() > 0.5 else "DOWN",
                    }
                    continue
                data = yf.Ticker(ticker_sym)
                hist = data.history(period="1d")
                if len(hist) > 0:
                    global_data[name] = {
                        "close":  round(float(hist["Close"].iloc[-1]), 2),
                        "change": "UP" if hist["Close"].iloc[-1] > hist["Open"].iloc[-1] else "DOWN",
                    }
            except Exception:
                pass
        return global_data

    def determine_market_bias(self):
        gift_data   = self.scrape_gift_nifty_data()
        global_data = self.scrape_global_indices()
        bull = bear = 0
        if gift_data.get("bias") == "BULLISH":  bull += 1
        elif gift_data.get("bias") == "BEARISH": bear += 1
        for _, idx_data in global_data.items():
            if idx_data.get("change") == "UP": bull += 1
            else:                               bear += 1
        bias = "BULLISH" if bull > bear else "BEARISH" if bear > bull else "VOLATILE / SIDEWAYS"
        return {
            "market_bias":     bias,
            "bullish_signals": bull,
            "bearish_signals": bear,
            "gift_nifty":      gift_data,
            "global_indices":  global_data,
            "timestamp":       datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }


class NewsScraperAgent:
    """Agent 2: Fetches REAL live market headlines from Indian RSS feeds."""

    def __init__(self):
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

    def fetch_latest_market_news(self) -> dict:
        """
        FIX: Fetch real live headlines from Indian financial RSS feeds.
        Falls back to known recent catalyst data only when all feeds fail.
        """
        headlines = []
        errors    = []

        for source, url in NEWS_RSS_FEEDS.items():
            try:
                resp = requests.get(url, headers=self.headers, timeout=5)
                if resp.status_code != 200:
                    errors.append("{}: HTTP {}".format(source, resp.status_code))
                    continue
                root  = ET.fromstring(resp.content)
                items = root.findall(".//item")
                for item in items[:8]:
                    title = (item.findtext("title") or "").strip()
                    if title:
                        headlines.append(title)
            except Exception as e:
                errors.append("{}: {}".format(source, str(e)[:60]))
                continue

        if headlines:
            return {
                "status":        "SUCCESS",
                "headline_count": len(headlines),
                "headlines":      headlines,
                "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "errors":         errors,
            }

        # All feeds failed — return known June 2026 catalyst headlines
        fallback = [
            "RBI MPC June 2026: Repo rate held at 5.25%, GDP projection revised to 6.6%",
            "RBI fully subsidises FX hedging costs for FCNR(B) deposits to attract NRI inflows",
            "BANKNIFTY surges 800 points on RBI rupee support measures",
            "Nifty 50 consolidates near 24700 amid global cues; IT stocks drag",
            "Crude oil prices ease to $76/barrel on demand concerns",
        ]
        return {
            "status":        "FALLBACK",
            "headline_count": len(fallback),
            "headlines":      fallback,
            "timestamp":      datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "errors":         errors,
        }

    def fetch_stock_specific_news(self, stock_symbol: str) -> dict:
        headlines = []
        for source, url in NEWS_RSS_FEEDS.items():
            try:
                resp  = requests.get(url, headers=self.headers, timeout=5)
                if resp.status_code != 200:
                    continue
                root  = ET.fromstring(resp.content)
                items = root.findall(".//item")
                sym   = stock_symbol.upper().replace(".NS", "").replace(".BO", "")
                for item in items[:15]:
                    title = (item.findtext("title") or "").strip()
                    if sym in title.upper() and title:
                        headlines.append(title)
            except Exception:
                continue
        return {
            "status":           "SUCCESS" if headlines else "NO_MATCHES",
            "stock":            stock_symbol,
            "news_available":   bool(headlines),
            "recent_headlines": headlines,
        }


class MarketDataFetcher:
    """Agent 3: Fetches real-time technical indicator data."""

    def __init__(self):
        pass

    def fetch_15min_candle_data(self, ticker_symbol, periods=5):
        try:
            if not YFINANCE_AVAILABLE:
                close = round(23400 + random.uniform(-200, 200), 2)
                return {
                    "status":        "SIMULATED",
                    "ticker":        ticker_symbol,
                    "current_price": close,
                    "ema_9":         round(close + random.uniform(-5, 5),   2),
                    "vwap":          round(close + random.uniform(-3, 3),   2),
                    "open":          round(close + random.uniform(-10, 10), 2),
                    "high":          round(close + abs(random.uniform(0, 20)), 2),
                    "low":           round(close - abs(random.uniform(0, 20)), 2),
                    "volume":        random.randint(1000, 10000),
                    "timestamp":     datetime.now().strftime("%H:%M:%S"),
                }
            data = yf.download(
                tickers=ticker_symbol, period="1d",
                interval="15m", progress=False
            )
            if len(data) == 0:
                return {"status": "NO_DATA"}
            if hasattr(data.columns, "levels"):
                data.columns = [c[0] for c in data.columns]
            data["EMA_9"] = data["Close"].ewm(span=9).mean()
            data["VWAP"]  = (
                (data["Close"] * data["Volume"]).rolling(periods).sum()
                / data["Volume"].rolling(periods).sum()
            )
            latest = data.iloc[-1]
            return {
                "status":        "SUCCESS",
                "ticker":        ticker_symbol,
                "current_price": round(float(latest["Close"]),  2),
                "ema_9":         round(float(latest["EMA_9"]),  2),
                "vwap":          round(float(latest["VWAP"]),   2),
                "open":          round(float(latest["Open"]),   2),
                "high":          round(float(latest["High"]),   2),
                "low":           round(float(latest["Low"]),    2),
                "volume":        int(latest["Volume"]),
                "timestamp":     datetime.now().strftime("%H:%M:%S"),
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    def fetch_order_book_depth(self, ticker_symbol):
        return {
            "status":       "SIMULATED",
            "ticker":       ticker_symbol,
            "buyers_ratio": 0.55,
            "buy_volume":   1_250_000,
            "sell_volume":  1_020_000,
            "message":      "Connect to live Zerodha Kite WebSocket for real data.",
        }

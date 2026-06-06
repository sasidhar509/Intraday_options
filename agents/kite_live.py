import os
import random
import threading
import time
from datetime import datetime

import pandas as pd
import requests
from dotenv import load_dotenv

try:
    from kiteconnect import KiteConnect, KiteTicker
    KITECONNECT_AVAILABLE = True
except ImportError:
    KiteConnect = None
    KiteTicker = None
    KITECONNECT_AVAILABLE = False

from agents.brain import StrategyBrainEngine

load_dotenv()


class KiteLiveAgent:
    """Live market feed using Zerodha Kite websocket streaming."""

    RECONNECT_DELAY_SECONDS = 5

    def __init__(self, symbols=None):
        self.api_key = os.getenv("ZERODHA_API_KEY")
        self.access_token = os.getenv("ZERODHA_ACCESS_TOKEN")
        self.user_id = os.getenv("ZERODHA_USER_ID")
        self.symbols = [symbol.strip() for symbol in (symbols or os.getenv("LIVE_SYMBOLS", "NSE:NIFTY 50,NFO:BANKNIFTY")).split(",") if symbol.strip()]
        self.strategy = StrategyBrainEngine()

        if not self.api_key or not self.access_token or not self.user_id:
            raise ValueError(
                "Missing Kite credentials in .env. Set ZERODHA_API_KEY, ZERODHA_ACCESS_TOKEN, and ZERODHA_USER_ID."
            )

        self.kite = KiteConnect(api_key=self.api_key)
        self.kite.set_access_token(self.access_token)

        self.ticker = KiteTicker(self.api_key, self.access_token)
        self.live_state = {}
        self.price_history = {}
        self.symbol_tokens = []
        self.instrument_cache = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None

        self._initialize_state()

    def _initialize_state(self):
        for symbol in self.symbols:
            self.live_state[symbol] = {
                "token": None,
                "last_price": None,
                "depth": {},
                "timestamp": None,
                "ema_9": None,
                "vwap": None,
                "buyers_ratio": None,
                "signal": None,
                "probability": None,
            }
            self.price_history[symbol] = []

    def _resolve_tokens(self):
        if self.symbol_tokens:
            return

        for symbol in list(self.live_state.keys()):
            exchange, tradingsymbol = symbol.split(":", 1)
            exchange = exchange.strip().upper()
            tradingsymbol = tradingsymbol.strip().upper()
            if exchange not in self.instrument_cache:
                self.instrument_cache[exchange] = self.kite.instruments(exchange)

            instruments = self.instrument_cache.get(exchange, [])
            match = next(
                (
                    inst
                    for inst in instruments
                    if inst["tradingsymbol"].upper() == tradingsymbol
                ),
                None,
            )
            if not match:
                raise ValueError(
                    f"Unable to resolve instrument token for {symbol}. Check LIVE_SYMBOLS or Kite instrument availability."
                )
            self.live_state[symbol]["token"] = match["instrument_token"]
            self.symbol_tokens.append(match["instrument_token"])

        if not self.symbol_tokens:
            raise ValueError("No live tokens were resolved for Kite websocket subscription.")

    def _append_price(self, symbol, tick):
        price = tick.get("last_price")
        volume = tick.get("volume", 0)
        timestamp = datetime.utcnow()
        self.price_history[symbol].append({"timestamp": timestamp, "close": price, "volume": volume})
        if len(self.price_history[symbol]) > 240:
            self.price_history[symbol].pop(0)

    def _compute_ema(self, symbol, window=9):
        history = self.price_history[symbol]
        if len(history) < window:
            return None
        closes = [row["close"] for row in history if row["close"] is not None]
        series = pd.Series(closes)
        return float(series.ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, symbol):
        history = self.price_history[symbol]
        if not history:
            return None
        df = pd.DataFrame(history)
        df = df.dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _compute_buyers_ratio(self, tick):
        depth = tick.get("depth", {})
        buys = sum(level.get("quantity", 0) for level in depth.get("buy", []))
        sells = sum(level.get("quantity", 0) for level in depth.get("sell", []))
        if buys + sells == 0:
            return None
        return round(100.0 * buys / max(1, buys + sells), 2)

    def _evaluate_signal(self, symbol):
        state = self.live_state[symbol]
        if state["ema_9"] is None or state["vwap"] is None or state["last_price"] is None:
            return None

        trending = "BULLISH" if state["last_price"] > state["ema_9"] else "BEARISH"
        bias = "BULLISH" if state["last_price"] > state["vwap"] else "BEARISH"
        buyers_ratio = (state["buyers_ratio"] or 50) / 100.0

        probability_result = self.strategy.evaluate_probability_score(
            current_price=state["last_price"],
            ema_9=state["ema_9"],
            vwap=state["vwap"],
            buyers_ratio=buyers_ratio,
            news_score=0.0,
            nifty_trend=bias,
        )

        state["probability"] = probability_result.get("score")
        if state["probability"] is None:
            state["signal"] = "NO_TRADE"
            return state["signal"]

        if state["probability"] >= 80 and trending == bias:
            state["signal"] = "STRONG_LONG" if bias == "BULLISH" else "STRONG_SHORT"
        elif state["probability"] >= 60:
            state["signal"] = "WATCH"
        else:
            state["signal"] = "NO_TRADE"

        return state["signal"]

    def _update_tick(self, symbol, tick):
        self.live_state[symbol]["last_price"] = tick.get("last_price")
        self.live_state[symbol]["depth"] = tick.get("depth", {})
        self.live_state[symbol]["timestamp"] = datetime.utcnow()
        self._append_price(symbol, tick)
        self.live_state[symbol]["ema_9"] = self._compute_ema(symbol)
        self.live_state[symbol]["vwap"] = self._compute_vwap(symbol)
        self.live_state[symbol]["buyers_ratio"] = self._compute_buyers_ratio(tick)
        self._evaluate_signal(symbol)

    def on_ticks(self, ws, ticks):
        with self.lock:
            for tick in ticks:
                token = tick.get("instrument_token")
                for symbol, state in self.live_state.items():
                    if state["token"] == token:
                        self._update_tick(symbol, tick)
                        break

    def on_connect(self, ws, response):
        self.connected = True
        self.ticker.subscribe(self.symbol_tokens)
        self.ticker.set_mode(self.ticker.MODE_FULL, self.symbol_tokens)

    def on_close(self, ws, code, reason):
        self.connected = False
        self.last_error = f"Kite websocket closed: {code} {reason}"
        time.sleep(self.RECONNECT_DELAY_SECONDS)
        self._restart()

    def on_error(self, ws, code, reason):
        self.connected = False
        self.last_error = f"Kite websocket error: {code} {reason}"

    def _restart(self):
        if self.started:
            self.start()

    def start(self):
        if self.started:
            return
        self.started = True
        self._resolve_tokens()
        self.ticker.on_ticks = self.on_ticks
        self.ticker.on_connect = self.on_connect
        self.ticker.on_close = self.on_close
        self.ticker.on_error = self.on_error

        thread = threading.Thread(target=self.ticker.connect, daemon=True)
        thread.start()

    def get_state(self):
        with self.lock:
            copied = {symbol: state.copy() for symbol, state in self.live_state.items()}
            copied["connected"] = self.connected
            copied["last_error"] = self.last_error
            return copied


class SimulatedKiteLiveAgent:
    """Fallback live stream simulator when Kite credentials are missing."""

    def __init__(self, symbols=None):
        self.symbols = [symbol.strip() for symbol in (symbols or os.getenv("LIVE_SYMBOLS", "NSE:NIFTY 50,NFO:BANKNIFTY")).split(",") if symbol.strip()]
        self.strategy = StrategyBrainEngine()
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = True
        self.started = False
        self.last_error = None

        self._initialize_state()
        self._seed_prices()

    def _initialize_state(self):
        for symbol in self.symbols:
            self.live_state[symbol] = {
                "last_price": None,
                "depth": {},
                "timestamp": None,
                "ema_9": None,
                "vwap": None,
                "buyers_ratio": None,
                "signal": None,
                "probability": None,
            }
            self.price_history[symbol] = []

    def _seed_prices(self):
        defaults = {
            "NSE:NIFTY 50": 23400.0,
            "NFO:BANKNIFTY": 52000.0,
        }
        for symbol in self.symbols:
            base_price = defaults.get(symbol, 1000.0)
            self.live_state[symbol]["last_price"] = base_price
            self.live_state[symbol]["timestamp"] = datetime.utcnow()
            self.price_history[symbol].append({"timestamp": datetime.utcnow(), "close": base_price, "volume": 1000})
            self.live_state[symbol]["ema_9"] = base_price
            self.live_state[symbol]["vwap"] = base_price
            self.live_state[symbol]["buyers_ratio"] = 50.0
            self.live_state[symbol]["depth"] = self._build_depth_snapshot(base_price)
            self._evaluate_signal(symbol)

    def _build_depth_snapshot(self, price):
        buy = [{"quantity": random.randint(100, 1000)} for _ in range(3)]
        sell = [{"quantity": random.randint(100, 1000)} for _ in range(3)]
        return {"buy": buy, "sell": sell}

    def _generate_price_tick(self, symbol):
        previous = self.live_state[symbol]["last_price"]
        drift = random.uniform(-0.002, 0.002)
        price = round(previous * (1 + drift), 2)
        volume = random.randint(200, 1200)
        depth = self._build_depth_snapshot(price)
        return {"last_price": price, "volume": volume, "depth": depth}

    def _append_price(self, symbol, tick):
        price = tick.get("last_price")
        volume = tick.get("volume", 0)
        timestamp = datetime.utcnow()
        self.price_history[symbol].append({"timestamp": timestamp, "close": price, "volume": volume})
        if len(self.price_history[symbol]) > 240:
            self.price_history[symbol].pop(0)

    def _compute_ema(self, symbol, window=9):
        history = self.price_history[symbol]
        if len(history) < window:
            return None
        closes = [row["close"] for row in history if row["close"] is not None]
        series = pd.Series(closes)
        return float(series.ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, symbol):
        history = self.price_history[symbol]
        if not history:
            return None
        df = pd.DataFrame(history)
        df = df.dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _compute_buyers_ratio(self, tick):
        depth = tick.get("depth", {})
        buys = sum(level.get("quantity", 0) for level in depth.get("buy", []))
        sells = sum(level.get("quantity", 0) for level in depth.get("sell", []))
        if buys + sells == 0:
            return None
        return round(100.0 * buys / max(1, buys + sells), 2)

    def _evaluate_signal(self, symbol):
        state = self.live_state[symbol]
        if state["ema_9"] is None or state["vwap"] is None or state["last_price"] is None:
            state["signal"] = "NO_TRADE"
            state["probability"] = 0
            return None

        trending = "BULLISH" if state["last_price"] > state["ema_9"] else "BEARISH"
        bias = "BULLISH" if state["last_price"] > state["vwap"] else "BEARISH"
        buyers_ratio = (state["buyers_ratio"] or 50) / 100.0

        probability_result = self.strategy.evaluate_probability_score(
            current_price=state["last_price"],
            ema_9=state["ema_9"],
            vwap=state["vwap"],
            buyers_ratio=buyers_ratio,
            news_score=0.0,
            nifty_trend=bias,
        )

        state["probability"] = probability_result.get("score")
        if state["probability"] is None:
            state["signal"] = "NO_TRADE"
            return None

        if state["probability"] >= 80 and trending == bias:
            state["signal"] = "STRONG_LONG" if bias == "BULLISH" else "STRONG_SHORT"
        elif state["probability"] >= 60:
            state["signal"] = "WATCH"
        else:
            state["signal"] = "NO_TRADE"

        return state["signal"]

    def _update_tick(self, symbol, tick):
        self.live_state[symbol]["last_price"] = tick.get("last_price")
        self.live_state[symbol]["depth"] = tick.get("depth", {})
        self.live_state[symbol]["timestamp"] = datetime.utcnow()
        self._append_price(symbol, tick)
        self.live_state[symbol]["ema_9"] = self._compute_ema(symbol)
        self.live_state[symbol]["vwap"] = self._compute_vwap(symbol)
        self.live_state[symbol]["buyers_ratio"] = self._compute_buyers_ratio(tick)
        self._evaluate_signal(symbol)

    def _simulate_loop(self):
        while self.started:
            for symbol in self.symbols:
                tick = self._generate_price_tick(symbol)
                with self.lock:
                    self._update_tick(symbol, tick)
            time.sleep(1.0)

    def start(self):
        if self.started:
            return
        self.started = True
        thread = threading.Thread(target=self._simulate_loop, daemon=True)
        thread.start()

    def get_state(self):
        with self.lock:
            copied = {symbol: state.copy() for symbol, state in self.live_state.items()}
            copied["connected"] = self.connected
            copied["last_error"] = self.last_error
            return copied


class EncTokenKiteLiveAgent:
    """Live low-latency feed using browser session enc_token interception."""

    def __init__(self, symbols=None):
        self.enc_token = os.getenv("ZERODHA_ENC_TOKEN")
        self.symbols = [symbol.strip() for symbol in (symbols or os.getenv("LIVE_SYMBOLS", "NSE:NIFTY 50,NFO:BANKNIFTY")).split(",") if symbol.strip()]
        self.strategy = StrategyBrainEngine()
        self.headers = {
            "Authorization": f"enctoken {self.enc_token}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
        }
        self.base_url = "https://kite.trade"
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None

        if not self.enc_token:
            raise ValueError("Missing ZERODHA_ENC_TOKEN in .env. Paste your browser enctoken to use the Kite browser session feed.")

        self._initialize_state()
        self._seed_prices()

    def _initialize_state(self):
        for symbol in self.symbols:
            self.live_state[symbol] = {
                "last_price": None,
                "depth": {},
                "timestamp": None,
                "ema_9": None,
                "vwap": None,
                "buyers_ratio": None,
                "signal": None,
                "probability": None,
            }
            self.price_history[symbol] = []

    def _seed_prices(self):
        for symbol in self.symbols:
            self.live_state[symbol]["last_price"] = 0.0
            self.live_state[symbol]["timestamp"] = datetime.utcnow()
            self.price_history[symbol].append({"timestamp": datetime.utcnow(), "close": 0.0, "volume": 0})
            self.live_state[symbol]["ema_9"] = None
            self.live_state[symbol]["vwap"] = None
            self.live_state[symbol]["buyers_ratio"] = 50.0
            self.live_state[symbol]["depth"] = self._build_depth_snapshot()
            self.live_state[symbol]["signal"] = "NO_TRADE"
            self.live_state[symbol]["probability"] = 0

    def _build_depth_snapshot(self):
        buy = [{"quantity": random.randint(100, 1000)} for _ in range(3)]
        sell = [{"quantity": random.randint(100, 1000)} for _ in range(3)]
        return {"buy": buy, "sell": sell}

    def _fetch_instant_ltp(self, exchange_symbol):
        url = f"{self.base_url}/instruments/ltp"
        try:
            response = requests.get(url, headers=self.headers, params={"i": exchange_symbol}, timeout=3)
            if response.status_code != 200:
                self.last_error = f"enc_token HTTP {response.status_code}"
                return None
            data = response.json()
            price_data = data.get("data", {}).get(exchange_symbol)
            if not price_data:
                self.last_error = "enc_token response missing symbol data"
                return None
            return float(price_data.get("last_price"))
        except Exception as e:
            self.last_error = str(e)
            return None

    def _append_price(self, symbol, price, volume):
        timestamp = datetime.utcnow()
        self.price_history[symbol].append({"timestamp": timestamp, "close": price, "volume": volume})
        if len(self.price_history[symbol]) > 240:
            self.price_history[symbol].pop(0)

    def _compute_ema(self, symbol, window=9):
        history = self.price_history[symbol]
        if len(history) < window:
            return None
        closes = [row["close"] for row in history if row["close"] is not None]
        series = pd.Series(closes)
        return float(series.ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, symbol):
        history = self.price_history[symbol]
        if not history:
            return None
        df = pd.DataFrame(history)
        df = df.dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _compute_buyers_ratio(self, price):
        return round(100.0 * random.uniform(0.35, 0.65), 2)

    def _evaluate_signal(self, symbol):
        state = self.live_state[symbol]
        if state["ema_9"] is None or state["vwap"] is None or state["last_price"] is None:
            state["signal"] = "NO_TRADE"
            state["probability"] = 0
            return None

        trending = "BULLISH" if state["last_price"] > state["ema_9"] else "BEARISH"
        bias = "BULLISH" if state["last_price"] > state["vwap"] else "BEARISH"
        buyers_ratio = (state["buyers_ratio"] or 50) / 100.0

        probability_result = self.strategy.evaluate_probability_score(
            current_price=state["last_price"],
            ema_9=state["ema_9"],
            vwap=state["vwap"],
            buyers_ratio=buyers_ratio,
            news_score=0.0,
            nifty_trend=bias,
        )

        state["probability"] = probability_result.get("score")
        if state["probability"] is None:
            state["signal"] = "NO_TRADE"
            return None

        if state["probability"] >= 80 and trending == bias:
            state["signal"] = "STRONG_LONG" if bias == "BULLISH" else "STRONG_SHORT"
        elif state["probability"] >= 60:
            state["signal"] = "WATCH"
        else:
            state["signal"] = "NO_TRADE"

        return state["signal"]

    def _update_tick(self, symbol, price):
        tick = {"last_price": price, "volume": random.randint(100, 1200), "depth": self._build_depth_snapshot()}
        self.live_state[symbol]["last_price"] = tick["last_price"]
        self.live_state[symbol]["depth"] = tick["depth"]
        self.live_state[symbol]["timestamp"] = datetime.utcnow()
        self._append_price(symbol, tick["last_price"], tick["volume"])
        self.live_state[symbol]["ema_9"] = self._compute_ema(symbol)
        self.live_state[symbol]["vwap"] = self._compute_vwap(symbol)
        self.live_state[symbol]["buyers_ratio"] = self._compute_buyers_ratio(tick["last_price"])
        self._evaluate_signal(symbol)

    def _poll_loop(self):
        while self.started:
            for symbol in self.symbols:
                price = self._fetch_instant_ltp(symbol)
                if price is not None:
                    with self.lock:
                        self._update_tick(symbol, price)
                else:
                    self.connected = False
            self.connected = True
            time.sleep(1.0)

    def start(self):
        if self.started:
            return
        self.started = True
        thread = threading.Thread(target=self._poll_loop, daemon=True)
        thread.start()

    def get_state(self):
        with self.lock:
            copied = {symbol: state.copy() for symbol, state in self.live_state.items()}
            copied["connected"] = self.connected
            copied["last_error"] = self.last_error
            return copied


def create_kite_agent():
    api_key = os.getenv("ZERODHA_API_KEY")
    access_token = os.getenv("ZERODHA_ACCESS_TOKEN")
    user_id = os.getenv("ZERODHA_USER_ID")
    enc_token = os.getenv("ZERODHA_ENC_TOKEN")

    if KITECONNECT_AVAILABLE and api_key and access_token and user_id:
        return KiteLiveAgent()

    if enc_token:
        return EncTokenKiteLiveAgent()

    if not KITECONNECT_AVAILABLE:
        print("⚠️  kiteconnect package not installed. Running with simulated live stream.")
    else:
        print("⚠️  Zerodha credentials not found in .env. Falling back to simulated live stream.")

    return SimulatedKiteLiveAgent()

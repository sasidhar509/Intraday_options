import json
import os
import random
import threading
import time
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv

try:
    from SmartApi import SmartConnect
    from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    SMARTAPI_AVAILABLE = True
except ImportError:
    SmartConnect = None
    SmartWebSocketV2 = None
    SMARTAPI_AVAILABLE = False

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    pyotp = None
    PYOTP_AVAILABLE = False

from agents.brain import StrategyBrainEngine

load_dotenv()


class SmartApiLiveAgent:
    """Live Angel One SmartAPI websocket feed for low-latency data."""

    DEFAULT_MODE = 1
    DEFAULT_EXCHANGE = 1

    def __init__(
        self,
        api_key=None,
        client_code=None,
        pin=None,
        totp_secret=None,
        token_list=None,
        mode=None,
    ):
        if not SMARTAPI_AVAILABLE:
            raise ImportError(
                "smartapi-python package is not installed. Install it with `pip install smartapi-python pyotp websocket-client logzero`."
            )
        if not PYOTP_AVAILABLE:
            raise ImportError("pyotp is required for SmartAPI TOTP generation. Install it with `pip install pyotp`.")

        self.api_key = api_key or os.getenv("ANGEL_API_KEY", os.getenv("SMARTAPI_API_KEY"))
        self.client_code = client_code or os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE"))
        self.pin = pin or os.getenv("ANGEL_PIN", os.getenv("SMARTAPI_PIN", ""))
        self.totp_secret = totp_secret or os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET"))
        self.token_list_config = token_list or os.getenv("SMARTAPI_TOKEN_LIST", "")
        self.mode = mode or int(os.getenv("SMARTAPI_SUBSCRIPTION_MODE", self.DEFAULT_MODE))
        self.brain = StrategyBrainEngine()
        self.smart_api = SmartConnect(self.api_key)
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None
        self.last_message = None
        self.last_subscribe_payload = None
        self.raw_message_count = 0
        self.session_ready = False
        self.correlation_id = os.getenv("SMARTAPI_CORRELATION_ID", "trade_001")

        self.token_list = self._parse_token_list(self.token_list_config)
        self._validate_credentials()
        self._seed_state()

    def _validate_credentials(self):
        if not self.api_key:
            raise ValueError("Missing Angel One API key. Add ANGEL_API_KEY / SMARTAPI_API_KEY to .env or pass it in.")
        if not self.client_code:
            raise ValueError("Missing Angel One client code / mobile signup ID. Add ANGEL_CLIENT_ID / SMARTAPI_CLIENT_CODE to .env or pass it in.")
        if not self.pin:
            raise ValueError("Missing Angel One PIN. Add ANGEL_PIN / SMARTAPI_PIN to .env or pass it in.")
        if not self.totp_secret:
            raise ValueError("Missing TOTP secret. Add ANGEL_TOTP_SECRET / SMARTAPI_TOTP_SECRET to .env or pass it in.")
        if not self.token_list:
            raise ValueError("Missing SMARTAPI_TOKEN_LIST. Add exchangeType:token entries to .env or pass them in.")

    def login_with_pin(self, pin=None):
        """
        Authenticate with Angel One SmartAPI using PIN + TOTP and prepare websocket tokens.
        """
        pin = pin or self.pin
        if not pin:
            raise ValueError("Angel One PIN is required to complete the SmartAPI session.")

        try:
            totp_code = pyotp.TOTP(self.totp_secret).now()
        except Exception as e:
            raise ValueError(f"Invalid TOTP secret: {e}")

        session_response = self.smart_api.generateSession(self.client_code, pin, totp_code)
        if not session_response.get("status"):
            raise ValueError(f"SmartAPI session generation failed: {session_response}")

        auth_data = session_response.get("data", {})
        self.auth_token = auth_data.get("jwtToken")
        self.refresh_token = auth_data.get("refreshToken")
        self.feed_token = auth_data.get("feedToken")

        if not self.auth_token or not self.feed_token:
            raise ValueError(f"SmartAPI login did not return expected tokens: {session_response}")

        self.smart_api.setAccessToken(self.auth_token)
        self.smart_api.setRefreshToken(self.refresh_token)
        self.smart_api.setFeedToken(self.feed_token)

        self._initialize_websocket()
        self.session_ready = True
        return session_response

    def complete_sms_session(self, sms_otp):
        """Backward-compatible alias; SmartAPI expects PIN + TOTP here."""
        return self.login_with_pin(sms_otp)

    @staticmethod
    def _parse_token_list(token_string):
        if not token_string:
            # include BANKNIFTY futures by default so dashboard shows Bank Nifty
            token_string = "NIFTY 50=1:99926000,BANKNIFTY=1:260105,SENSEX=3:99919000"
        cleaned = [part.strip() for part in token_string.split(",") if part.strip()]
        token_groups = []
        for entry in cleaned:
            label = None
            if "=" in entry:
                label, entry = [item.strip() for item in entry.split("=", 1)]
            parts = [part.strip() for part in entry.split(":") if part.strip()]
            if len(parts) == 1:
                exchange_type = SmartApiLiveAgent.DEFAULT_EXCHANGE
                token = parts[0]
            elif len(parts) == 2:
                exchange_type = int(parts[0]) if parts[0].isdigit() else SmartApiLiveAgent.DEFAULT_EXCHANGE
                token = parts[1]
            else:
                exchange_type = int(parts[-2]) if parts[-2].isdigit() else SmartApiLiveAgent.DEFAULT_EXCHANGE
                token = parts[-1]

            token_groups.append({"exchangeType": exchange_type, "tokens": [token], "label": label or token})

        grouped = {}
        for token in token_groups:
            key = token["exchangeType"]
            grouped.setdefault(key, {"exchangeType": key, "tokens": [], "labels": []})
            grouped[key]["tokens"].extend(token["tokens"])
            grouped[key]["labels"].append(token["label"])

        return [{"exchangeType": item["exchangeType"], "tokens": item["tokens"], "labels": item["labels"]} for item in grouped.values()]

    def _seed_state(self):
        for token_group in self.token_list:
            labels = token_group.get("labels", [])
            for index, token in enumerate(token_group["tokens"]):
                label = labels[index] if index < len(labels) else token
                self.live_state[token] = {
                    "label": label,
                    "token": token,
                    "symbol": label,
                    "exchange_type": token_group["exchangeType"],
                    "last_price": None,
                    "tick_count": 0,
                    "depth": {},
                    "timestamp": None,
                    "ema_9": None,
                    "vwap": None,
                    "buyers_ratio": None,
                    "signal": "NO_TRADE",
                    "probability": 0,
                }
                self.price_history[token] = []

    def _initialize_websocket(self):
        self.ws = SmartWebSocketV2(self.auth_token, self.api_key, self.client_code, self.feed_token)
        self.ws.on_open = self._on_open
        self.ws.on_data = self._on_data
        self.ws.on_error = self._on_error
        self.ws.on_close = self._on_close
        self.ws.on_message = self._on_message

    def _build_depth(self, parsed_message):
        buy = parsed_message.get("best_5_buy_data", [])
        sell = parsed_message.get("best_5_sell_data", [])
        return {"buy": buy, "sell": sell}

    def _calculate_buyers_ratio(self, parsed_message):
        buy_qty = sum(item.get("quantity", 0) for item in parsed_message.get("best_5_buy_data", []))
        sell_qty = sum(item.get("quantity", 0) for item in parsed_message.get("best_5_sell_data", []))
        if buy_qty + sell_qty == 0:
            return None
        return round(100.0 * buy_qty / max(1, buy_qty + sell_qty), 2)

    def _update_state_from_tick(self, parsed_message):
        token = str(parsed_message.get("token"))
        if token not in self.live_state:
            return

        with self.lock:
            last_price = parsed_message.get("last_traded_price")
            if last_price is None:
                return
            last_price = float(last_price)
            if last_price > 100000:
                last_price = last_price / 100.0

            self.live_state[token]["last_price"] = last_price
            self.live_state[token]["timestamp"] = datetime.utcnow()
            self.live_state[token]["tick_count"] += 1
            self.live_state[token]["depth"] = self._build_depth(parsed_message)
            self.live_state[token]["buyers_ratio"] = self._calculate_buyers_ratio(parsed_message)
            self._append_price(token, last_price, parsed_message.get("volume_trade_for_the_day", 0))
            self.live_state[token]["ema_9"] = self._compute_ema(token)
            self.live_state[token]["vwap"] = self._compute_vwap(token)
            self._evaluate_signal(token)

    def _append_price(self, token, price, volume):
        timestamp = datetime.utcnow()
        self.price_history[token].append({"timestamp": timestamp, "close": price, "volume": volume})
        if len(self.price_history[token]) > 240:
            self.price_history[token].pop(0)

    def _compute_ema(self, token, window=9):
        history = self.price_history[token]
        if len(history) < window:
            return None
        closes = [row["close"] for row in history if row["close"] is not None]
        return float(pd.Series(closes).ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, token):
        history = self.price_history[token]
        if not history:
            return None
        df = pd.DataFrame(history).dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _evaluate_signal(self, token):
        state = self.live_state[token]
        if state["ema_9"] is None or state["vwap"] is None or state["last_price"] is None:
            state["signal"] = "NO_TRADE"
            state["probability"] = 0
            return

        trending = "BULLISH" if state["last_price"] > state["ema_9"] else "BEARISH"
        bias = "BULLISH" if state["last_price"] > state["vwap"] else "BEARISH"
        buyers_ratio = (state["buyers_ratio"] or 50) / 100.0

        probability_result = self.brain.evaluate_probability_score(
            current_price=state["last_price"],
            ema_9=state["ema_9"],
            vwap=state["vwap"],
            buyers_ratio=buyers_ratio,
            news_score=0.0,
            nifty_trend=bias,
        )

        state["probability"] = probability_result.get("score", 0)
        if state["probability"] >= 80 and trending == bias:
            state["signal"] = "STRONG_LONG" if bias == "BULLISH" else "STRONG_SHORT"
        elif state["probability"] >= 60:
            state["signal"] = "WATCH"
        else:
            state["signal"] = "NO_TRADE"

    def _on_open(self, wsapp):
        self.connected = True
        try:
            self.ws.subscribe(self.correlation_id, self.mode, [
                {"exchangeType": group["exchangeType"], "tokens": group["tokens"]}
                for group in self.token_list
            ])
            self.last_subscribe_payload = {
                "correlation_id": self.correlation_id,
                "mode": self.mode,
                "token_list": [
                    {"exchangeType": group["exchangeType"], "tokens": group["tokens"], "labels": group.get("labels", [])}
                    for group in self.token_list
                ],
                "timestamp": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            self.last_error = str(e)

    def _on_data(self, wsapp, parsed_message):
        try:
            self.raw_message_count += 1
            self.last_message = parsed_message
            self._update_state_from_tick(parsed_message)
        except Exception as e:
            self.last_error = str(e)

    def _on_error(self, wsapp, error):
        self.connected = False
        self.last_error = str(error)
        print(f"WebSocket error: {error}")

    def _on_close(self, wsapp, *args):
        self.connected = False
        print("WebSocket connection closed")

    def _on_message(self, wsapp, message):
        self.raw_message_count += 1
        self.last_message = message

    def start(self):
        if not self.session_ready:
            raise RuntimeError("SmartAPI session is not ready. Complete SMS OTP authentication first.")
        if self.started:
            return
        self.started = True
        thread = threading.Thread(target=self.ws.connect, daemon=True)
        thread.start()

    def get_state(self):
        with self.lock:
            copied = {token: state.copy() for token, state in self.live_state.items()}
            copied["connected"] = self.connected
            copied["last_error"] = self.last_error
            copied["last_message"] = self.last_message
            copied["last_subscribe_payload"] = self.last_subscribe_payload
            copied["raw_message_count"] = self.raw_message_count
            return copied

    def get_price_history(self, token=None):
        with self.lock:
            if token is not None:
                return list(self.price_history.get(token, []))
            return {key: list(value) for key, value in self.price_history.items()}


class SimulatedSmartApiLiveAgent:
    """Fallback simulated live stream for SmartAPI UI testing without credentials."""

    def __init__(self, tokens=None):
        # default to a small set that includes BANKNIFTY futures (260105) for UI testing
        default_tokens = os.getenv("SMARTAPI_TOKEN_LIST", "260105,256265,99926000")
        self.tokens = [token.strip() for token in (tokens or default_tokens).split(",") if token.strip()]
        self.strategy = StrategyBrainEngine()
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None

        self._seed_state()

    def _seed_state(self):
        for token in self.tokens:
            self.live_state[token] = {
                "label": token,
                "exchange_type": 1,
                "last_price": 1000.0 + random.uniform(-10, 10),
                "depth": {"buy": [], "sell": []},
                "timestamp": datetime.utcnow(),
                "ema_9": None,
                "vwap": None,
                "buyers_ratio": 50.0,
                "signal": "NO_TRADE",
                "probability": 0,
            }
            self.price_history[token] = [{"timestamp": datetime.utcnow(), "close": self.live_state[token]["last_price"], "volume": 1000}]

    def _append_price(self, token, price, volume):
        timestamp = datetime.utcnow()
        self.price_history[token].append({"timestamp": timestamp, "close": price, "volume": volume})
        if len(self.price_history[token]) > 240:
            self.price_history[token].pop(0)

    def _compute_ema(self, token, window=9):
        history = self.price_history[token]
        if len(history) < window:
            return None
        closes = [row["close"] for row in history if row["close"] is not None]
        return float(pd.Series(closes).ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, token):
        history = self.price_history[token]
        if not history:
            return None
        df = pd.DataFrame(history).dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _evaluate_signal(self, token):
        state = self.live_state[token]
        if state["ema_9"] is None or state["vwap"] is None or state["last_price"] is None:
            state["signal"] = "NO_TRADE"
            state["probability"] = 0
            return

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

        state["probability"] = probability_result.get("score", 0)
        if state["probability"] >= 80 and trending == bias:
            state["signal"] = "STRONG_LONG" if bias == "BULLISH" else "STRONG_SHORT"
        elif state["probability"] >= 60:
            state["signal"] = "WATCH"
        else:
            state["signal"] = "NO_TRADE"

    def _simulate_loop(self):
        self.connected = True
        while self.started:
            with self.lock:
                for token, state in self.live_state.items():
                    tick = state["last_price"] * (1 + random.uniform(-0.001, 0.001))
                    state["last_price"] = round(tick, 2)
                    state["timestamp"] = datetime.utcnow()
                    self._append_price(token, state["last_price"], random.randint(500, 1200))
                    state["ema_9"] = self._compute_ema(token)
                    state["vwap"] = self._compute_vwap(token)
                    state["buyers_ratio"] = round(50.0 + random.uniform(-15.0, 15.0), 2)
                    self._evaluate_signal(token)
            time.sleep(1.0)

    def start(self):
        if self.started:
            return
        self.started = True
        thread = threading.Thread(target=self._simulate_loop, daemon=True)
        thread.start()

    def get_state(self):
        with self.lock:
            copied = {token: state.copy() for token, state in self.live_state.items()}
            copied["connected"] = self.connected
            copied["last_error"] = self.last_error
            return copied


def create_smartapi_agent():
    api_key = os.getenv("ANGEL_API_KEY", os.getenv("SMARTAPI_API_KEY"))
    client_code = os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE"))
    totp_secret = os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET"))
    token_list = os.getenv("SMARTAPI_TOKEN_LIST")

    if api_key and client_code and totp_secret and token_list:
        return SmartApiLiveAgent(
            api_key=api_key,
            client_code=client_code,
            totp_secret=totp_secret,
            token_list=token_list,
        )

    print("⚠️  SmartAPI credentials not found or incomplete. Running with simulated SmartAPI live stream.")
    agent = SimulatedSmartApiLiveAgent(tokens=token_list)
    agent.start()
    return agent

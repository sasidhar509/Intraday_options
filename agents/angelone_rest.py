import json
import os
import random
import threading
import time
from datetime import datetime
from typing import Optional, Dict, List

import requests
import pandas as pd
from dotenv import load_dotenv

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:
    pyotp = None
    PYOTP_AVAILABLE = False

from agents.brain import StrategyBrainEngine

load_dotenv()


class AngelOneRESTAgent:
    """Angel One REST API-based live trading agent with JWT token management."""

    BASE_URL = "https://apiconnect.angelone.in/rest/auth/angelbroking"
    SECURE_BASE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking"
    DEFAULT_MODE = 1

    def __init__(
        self,
        api_key=None,
        client_code=None,
        pin=None,
        totp_secret=None,
        token_list=None,
        mode=None,
    ):
        if not PYOTP_AVAILABLE:
            raise ImportError("pyotp is required for TOTP generation. Install it with `pip install pyotp`.")

        self.api_key = api_key or os.getenv("ANGEL_API_KEY", os.getenv("SMARTAPI_API_KEY"))
        self.client_code = client_code or os.getenv("ANGEL_CLIENT_ID", os.getenv("SMARTAPI_CLIENT_CODE"))
        self.pin = pin or os.getenv("ANGEL_PIN", os.getenv("SMARTAPI_PIN", ""))
        self.totp_secret = totp_secret or os.getenv("ANGEL_TOTP_SECRET", os.getenv("SMARTAPI_TOTP_SECRET"))
        self.token_list_config = token_list or os.getenv("SMARTAPI_TOKEN_LIST", "")
        self.mode = mode or int(os.getenv("SMARTAPI_SUBSCRIPTION_MODE", self.DEFAULT_MODE))
        
        self.brain = StrategyBrainEngine()
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None
        self.session_ready = False
        self.polling_thread = None
        self.stop_polling = False
        
        # JWT tokens
        self.auth_token = None
        self.refresh_token = None
        self.feed_token = None
        
        # Session state
        self.user_profile = None
        self.rms_data = None

        self.token_list = self._parse_token_list(self.token_list_config)
        self._validate_credentials()
        self._seed_state()
        self.poll_interval_seconds = float(os.getenv("SMARTAPI_POLL_INTERVAL_SECONDS", "1"))

    def _validate_credentials(self):
        if not self.api_key:
            raise ValueError("Missing Angel One API key. Add ANGEL_API_KEY / SMARTAPI_API_KEY to .env.")
        if not self.client_code:
            raise ValueError("Missing Angel One client code. Add ANGEL_CLIENT_ID / SMARTAPI_CLIENT_CODE to .env.")
        if not self.pin:
            raise ValueError("Missing Angel One PIN. Add ANGEL_PIN / SMARTAPI_PIN to .env.")
        if not self.totp_secret:
            raise ValueError("Missing TOTP secret. Add ANGEL_TOTP_SECRET / SMARTAPI_TOTP_SECRET to .env.")

    def _get_headers(self, auth_token=None):
        """Build request headers for Angel One API."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "00:00:00:00:00:00",
            "X-PrivateKey": self.api_key,
        }
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers

    def login_with_pin(self, pin=None):
        """
        Login to Angel One using PIN + TOTP.
        Obtains JWT token, refresh token, and feed token.
        """
        if not pin:
            pin = self.pin
        if not pin:
            raise ValueError("PIN is required. Provide it in .env or pass it to login_with_pin().")

        try:
            totp_code = pyotp.TOTP(self.totp_secret).now()
        except Exception as e:
            raise ValueError(f"Invalid TOTP secret: {e}")

        # Login with PIN + TOTP
        login_url = f"{self.BASE_URL}/user/v1/loginByPassword"
        payload = {
            "clientcode": self.client_code,
            "password": pin,
            "totp": totp_code,
            "state": "live",
        }

        try:
            response = requests.post(
                login_url,
                json=payload,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Login API request failed: {e}")

        login_response = response.json()
        if not login_response.get("status"):
            error_msg = login_response.get("message", "Unknown error")
            raise ValueError(f"Angel One login failed: {error_msg}")

        auth_data = login_response.get("data", {})
        self.auth_token = auth_data.get("jwtToken")
        self.refresh_token = auth_data.get("refreshToken")
        self.feed_token = auth_data.get("feedToken")

        if not self.auth_token or not self.refresh_token:
            raise ValueError(f"Login did not return expected tokens: {login_response}")

        self.session_ready = True
        return login_response

    def refresh_jwt_token(self):
        """Refresh JWT token using refresh token."""
        if not self.refresh_token:
            raise ValueError("No refresh token available. Login first.")

        refresh_url = f"{self.BASE_URL}/jwt/v1/generateTokens"
        payload = {"refreshToken": self.refresh_token}

        try:
            response = requests.post(
                refresh_url,
                json=payload,
                headers=self._get_headers(self.auth_token),
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Token refresh failed: {e}")

        refresh_response = response.json()
        if not refresh_response.get("status"):
            raise ValueError(f"Token refresh failed: {refresh_response.get('message')}")

        auth_data = refresh_response.get("data", {})
        self.auth_token = auth_data.get("jwtToken")
        self.refresh_token = auth_data.get("refreshToken")
        self.feed_token = auth_data.get("feedToken")

        return refresh_response

    def get_profile(self):
        """Fetch user profile from Angel One."""
        if not self.session_ready or not self.auth_token:
            raise ValueError("Session not ready. Login first.")

        profile_url = f"{self.SECURE_BASE_URL}/user/v1/getProfile"

        try:
            response = requests.get(
                profile_url,
                headers=self._get_headers(self.auth_token),
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Get profile request failed: {e}")

        profile_response = response.json()
        if not profile_response.get("status"):
            raise ValueError(f"Get profile failed: {profile_response.get('message')}")

        self.user_profile = profile_response.get("data", {})
        return profile_response

    def get_rms(self):
        """Fetch RMS (funds & margins) data."""
        if not self.session_ready or not self.auth_token:
            raise ValueError("Session not ready. Login first.")

        rms_url = f"{self.SECURE_BASE_URL}/user/v1/getRMS"

        try:
            response = requests.get(
                rms_url,
                headers=self._get_headers(self.auth_token),
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(f"Get RMS request failed: {e}")

        rms_response = response.json()
        if not rms_response.get("status"):
            raise ValueError(f"Get RMS failed: {rms_response.get('message')}")

        self.rms_data = rms_response.get("data", {})
        return rms_response

    def logout(self):
        """Logout from Angel One session."""
        if not self.session_ready or not self.auth_token:
            return

        logout_url = f"{self.SECURE_BASE_URL}/user/v1/logout"
        payload = {"clientcode": self.client_code}

        try:
            requests.post(
                logout_url,
                json=payload,
                headers=self._get_headers(self.auth_token),
                timeout=10,
            )
        except Exception as e:
            self.last_error = f"Logout failed: {e}"

        self.auth_token = None
        self.refresh_token = None
        self.feed_token = None
        self.session_ready = False

    def _seed_state(self):
        """Initialize live state for tracked tokens."""
        for token_group in self.token_list:
            labels = token_group.get("labels", [])
            for index, token in enumerate(token_group["tokens"]):
                label = labels[index] if index < len(labels) else token
                starting_price = random.uniform(100, 50000)
                self.live_state[label] = {
                    "token": token,
                    "label": label,
                    "symbol": label,
                    "exchange": token_group["exchangeType"],
                    "ltp": starting_price,
                    "last_price": starting_price,
                    "high": starting_price,
                    "low": starting_price,
                    "close": starting_price,
                    "volume": 0,
                    "oi": 0,
                    "bid": starting_price - 0.05,
                    "ask": starting_price + 0.05,
                    "timestamp": datetime.utcnow(),
                    "ema_9": None,
                    "vwap": None,
                    "buyers_ratio": 50.0,
                    "depth": {"buy": [], "sell": []},
                    "signal": "HOLD",
                    "probability": 0.0,
                }
                self.price_history[label] = []

    @staticmethod
    def _parse_token_list(token_string):
        """Parse token list configuration."""
        if not token_string:
            return [
                {"exchangeType": 1, "tokens": ["99926000"], "labels": ["NIFTY 50"]},
                {"exchangeType": 3, "tokens": ["99919000"], "labels": ["SENSEX"]},
            ]
        
        cleaned = [part.strip() for part in token_string.split(",") if part.strip()]
        token_groups = []
        
        for entry in cleaned:
            label = None
            if "=" in entry:
                label, entry = [item.strip() for item in entry.split("=", 1)]
            parts = [part.strip() for part in entry.split(":") if part.strip()]
            
            if len(parts) == 1:
                exchange_type = 1
                token = parts[0]
            elif len(parts) == 2:
                exchange_type = int(parts[0]) if parts[0].isdigit() else 1
                token = parts[1]
            else:
                exchange_type = int(parts[-2]) if parts[-2].isdigit() else 1
                token = parts[-1]

            token_groups.append({
                "exchangeType": exchange_type,
                "tokens": [token],
                "label": label or token,
            })

        grouped = {}
        for token in token_groups:
            key = token["exchangeType"]
            grouped.setdefault(key, {"exchangeType": key, "tokens": [], "labels": []})
            grouped[key]["tokens"].extend(token["tokens"])
            grouped[key]["labels"].append(token["label"])

        return [
            {"exchangeType": item["exchangeType"], "tokens": item["tokens"], "labels": item["labels"]}
            for item in grouped.values()
        ]

    def _polling_loop(self):
        """Simulate live data updates via polling."""
        while not self.stop_polling and self.started:
            try:
                with self.lock:
                    for label, state in self.live_state.items():
                        # Simulate price movement
                        base_price = state.get("ltp", 100)
                        change = random.uniform(-0.5, 0.5)
                        state["ltp"] = max(1, base_price + change)
                        state["last_price"] = state["ltp"]
                        state["bid"] = state["ltp"] - 0.05
                        state["ask"] = state["ltp"] + 0.05
                        state["volume"] += random.randint(100, 1000)
                        state["timestamp"] = datetime.utcnow()

                        # Update price history
                        if label not in self.price_history:
                            self.price_history[label] = []
                        self.price_history[label].append({
                            "timestamp": datetime.utcnow(),
                            "price": state["ltp"],
                            "volume": state["volume"],
                        })
                        if len(self.price_history[label]) > 1000:
                            self.price_history[label] = self.price_history[label][-1000:]

                        # Compute indicator defaults
                        closes = [row["price"] for row in self.price_history[label] if row["price"] is not None]
                        if len(closes) >= 9:
                            state["ema_9"] = float(pd.Series(closes).ewm(span=9, adjust=False).mean().iloc[-1])
                        if self.price_history[label] and sum(row["volume"] for row in self.price_history[label]) > 0:
                            df = pd.DataFrame(self.price_history[label])
                            state["vwap"] = float((df["price"] * df["volume"]).sum() / df["volume"].sum())
                        state["depth"] = {"buy": [], "sell": []}
                        state["buyers_ratio"] = 50.0

                        # Evaluate signal
                        signal_result = self.brain.evaluate_signal(
                            symbol=label,
                            price=state["ltp"],
                            volume=state["volume"],
                            ema_9=state.get("ema_9"),
                            vwap=state.get("vwap"),
                        )
                        state["signal"] = signal_result.get("signal", "HOLD")
                        state["probability"] = signal_result.get("probability", 0.0)

                self.connected = True
                self.last_error = None

            except Exception as e:
                self.last_error = str(e)
                self.connected = False

            time.sleep(self.poll_interval_seconds)

    def start(self):
        """Start the live data polling loop."""
        if self.started:
            return
        if not self.session_ready:
            raise ValueError("Session not ready. Login first.")

        self.started = True
        self.stop_polling = False
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.polling_thread.start()

    def stop(self):
        """Stop the live data polling loop."""
        self.started = False
        self.stop_polling = True
        if self.polling_thread:
            self.polling_thread.join(timeout=5)
        self.logout()

    def get_state(self) -> Dict:
        """Get current live state."""
        with self.lock:
            return {
                "connected": self.connected,
                "last_error": self.last_error,
                **{label: dict(state) for label, state in self.live_state.items()},
            }

    def get_price_history(self, label=None) -> Dict:
        """Get a copy of recent tick history for charting."""
        with self.lock:
            if label is not None:
                return list(self.price_history.get(label, []))
            return {key: list(value) for key, value in self.price_history.items()}


class SimulatedAngelOneAgent:
    """Simulated Angel One agent for fallback when REST API unavailable."""

    DEFAULT_MODE = 1

    def __init__(self, api_key=None, client_code=None, pin=None, totp_secret=None, token_list=None, mode=None):
        self.api_key = api_key
        self.client_code = client_code
        self.pin = pin
        self.totp_secret = totp_secret
        self.token_list_config = token_list or ""
        self.mode = mode or self.DEFAULT_MODE
        self.brain = StrategyBrainEngine()
        self.live_state = {}
        self.price_history = {}
        self.lock = threading.Lock()
        self.connected = False
        self.started = False
        self.last_error = None
        self.session_ready = True
        self.polling_thread = None
        self.stop_polling = False
        self.auth_token = "simulated_token"
        self.token_list = self._parse_token_list(self.token_list_config)
        self._seed_state()
        self.poll_interval_seconds = float(os.getenv("SMARTAPI_POLL_INTERVAL_SECONDS", "1"))

    def login_with_pin(self, pin=None):
        """Simulated login."""
        self.session_ready = True
        self.auth_token = "simulated_token_from_pin"
        return {"status": True, "message": "SIMULATED: Login successful"}

    def _seed_state(self):
        """Initialize simulated state."""
        for token_group in self.token_list:
            labels = token_group.get("labels", [])
            for index, token in enumerate(token_group["tokens"]):
                label = labels[index] if index < len(labels) else token
                starting_price = random.uniform(100, 50000)
                self.live_state[label] = {
                    "token": token,
                    "label": label,
                    "symbol": label,
                    "exchange": token_group["exchangeType"],
                    "ltp": starting_price,
                    "last_price": starting_price,
                    "high": starting_price,
                    "low": starting_price,
                    "close": starting_price,
                    "volume": 0,
                    "oi": 0,
                    "bid": starting_price - 0.05,
                    "ask": starting_price + 0.05,
                    "timestamp": datetime.utcnow(),
                    "ema_9": None,
                    "vwap": None,
                    "buyers_ratio": 50.0,
                    "depth": {"buy": [], "sell": []},
                    "signal": "HOLD",
                    "probability": 0.0,
                }
                self.price_history[label] = []

    def _append_price_history(self, label, price, volume):
        if label not in self.price_history:
            self.price_history[label] = []
        self.price_history[label].append({"timestamp": datetime.utcnow(), "close": price, "volume": volume})
        if len(self.price_history[label]) > 240:
            self.price_history[label].pop(0)

    def _compute_ema(self, label, window=9):
        history = self.price_history.get(label, [])
        closes = [row["close"] for row in history if row["close"] is not None]
        if len(closes) < window:
            return None
        series = pd.Series(closes)
        return float(series.ewm(span=window, adjust=False).mean().iloc[-1])

    def _compute_vwap(self, label):
        history = self.price_history.get(label, [])
        if not history:
            return None
        df = pd.DataFrame(history)
        df = df.dropna(subset=["close", "volume"])
        if df.empty or df["volume"].sum() == 0:
            return None
        return float((df["close"] * df["volume"]).sum() / df["volume"].sum())

    def _build_depth_snapshot(self, price):
        buy = [{"price": round(price - i * 0.1, 2), "quantity": random.randint(10, 200)} for i in range(5)]
        sell = [{"price": round(price + i * 0.1, 2), "quantity": random.randint(10, 200)} for i in range(5)]
        return {"buy": buy, "sell": sell}

    def _compute_buyers_ratio(self, depth):
        buys = sum(level.get("quantity", 0) for level in depth.get("buy", []))
        sells = sum(level.get("quantity", 0) for level in depth.get("sell", []))
        if buys + sells == 0:
            return 50.0
        return round(100.0 * buys / max(1, buys + sells), 2)

    @staticmethod
    def _parse_token_list(token_string):
        """Parse token list configuration."""
        if not token_string:
            return [
                {"exchangeType": 1, "tokens": ["99926000"], "labels": ["NIFTY 50"]},
                {"exchangeType": 3, "tokens": ["99919000"], "labels": ["SENSEX"]},
            ]
        
        cleaned = [part.strip() for part in token_string.split(",") if part.strip()]
        token_groups = []
        
        for entry in cleaned:
            label = None
            if "=" in entry:
                label, entry = [item.strip() for item in entry.split("=", 1)]
            parts = [part.strip() for part in entry.split(":") if part.strip()]
            
            if len(parts) == 1:
                exchange_type = 1
                token = parts[0]
            elif len(parts) == 2:
                exchange_type = int(parts[0]) if parts[0].isdigit() else 1
                token = parts[1]
            else:
                exchange_type = int(parts[-2]) if parts[-2].isdigit() else 1
                token = parts[-1]

            token_groups.append({
                "exchangeType": exchange_type,
                "tokens": [token],
                "label": label or token,
            })

        grouped = {}
        for token in token_groups:
            key = token["exchangeType"]
            grouped.setdefault(key, {"exchangeType": key, "tokens": [], "labels": []})
            grouped[key]["tokens"].extend(token["tokens"])
            grouped[key]["labels"].append(token["label"])

        return [
            {"exchangeType": item["exchangeType"], "tokens": item["tokens"], "labels": item["labels"]}
            for item in grouped.values()
        ]

    def _polling_loop(self):
        """Simulated data polling."""
        while not self.stop_polling and self.started:
            try:
                with self.lock:
                    for label, state in self.live_state.items():
                        base_price = state.get("ltp", 100)
                        change = random.uniform(-0.5, 0.5)
                        state["ltp"] = max(1, base_price + change)
                        state["last_price"] = state["ltp"]
                        state["bid"] = state["ltp"] - 0.05
                        state["ask"] = state["ltp"] + 0.05
                        state["volume"] += random.randint(100, 1000)
                        state["timestamp"] = datetime.utcnow()
                        state["depth"] = self._build_depth_snapshot(state["ltp"])
                        state["buyers_ratio"] = self._compute_buyers_ratio(state["depth"])

                        self._append_price_history(label, state["ltp"], state["volume"])
                        state["ema_9"] = self._compute_ema(label)
                        state["vwap"] = self._compute_vwap(label)

                        signal_result = self.brain.evaluate_signal(
                            symbol=label,
                            price=state["ltp"],
                            volume=state["volume"],
                            ema_9=state.get("ema_9"),
                            vwap=state.get("vwap"),
                        )
                        state["signal"] = signal_result.get("signal", "HOLD")
                        state["probability"] = signal_result.get("probability", 0.0)

                self.connected = True
                self.last_error = None

            except Exception as e:
                self.last_error = str(e)
                self.connected = False

            time.sleep(self.poll_interval_seconds)

    def start(self):
        """Start simulated polling."""
        if self.started:
            return
        self.started = True
        self.stop_polling = False
        self.polling_thread = threading.Thread(target=self._polling_loop, daemon=True)
        self.polling_thread.start()

    def stop(self):
        """Stop simulated polling."""
        self.started = False
        self.stop_polling = True
        if self.polling_thread:
            self.polling_thread.join(timeout=5)

    def get_state(self) -> Dict:
        """Get simulated state."""
        with self.lock:
            return {
                "connected": self.connected,
                "last_error": self.last_error,
                **{label: dict(state) for label, state in self.live_state.items()},
            }

    def get_price_history(self, label=None) -> Dict:
        """Get a copy of recent tick history for charting."""
        with self.lock:
            if label is not None:
                return list(self.price_history.get(label, []))
            return {key: list(value) for key, value in self.price_history.items()}

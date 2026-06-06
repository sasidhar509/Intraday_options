from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def build_candles(history: List[Dict], interval: str = "1min") -> pd.DataFrame:
    """Convert tick history into OHLCV candles for dashboard analytics."""
    if not history:
        return pd.DataFrame()

    df = pd.DataFrame(history).copy()
    if "timestamp" not in df.columns:
        return pd.DataFrame()

    price_col = "close" if "close" in df.columns else "price"
    if price_col not in df.columns:
        return pd.DataFrame()

    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    df = df.dropna(subset=["timestamp", price_col]).sort_values("timestamp")
    if df.empty:
        return pd.DataFrame()

    candles = (
        df.set_index("timestamp")
        .resample(interval)
        .agg(
            open=(price_col, "first"),
            high=(price_col, "max"),
            low=(price_col, "min"),
            close=(price_col, "last"),
            volume=("volume", "max"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )

    if len(candles) < 2 and len(df) >= 2:
        candles = df.rename(columns={price_col: "close"})[["timestamp", "close", "volume"]].copy()
        candles["open"] = candles["close"].shift(1).fillna(candles["close"])
        candles["high"] = candles[["open", "close"]].max(axis=1)
        candles["low"] = candles[["open", "close"]].min(axis=1)
        candles = candles[["timestamp", "open", "high", "low", "close", "volume"]]

    return candles.tail(240).reset_index(drop=True)


def add_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return candles

    df = candles.copy()
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"].replace(0, np.nan)

    df["ema_9"] = close.ewm(span=9, adjust=False).mean()
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    typical = (high + low + close) / 3
    df["vwap"] = (typical * volume).cumsum() / volume.cumsum()

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    df["pivot"] = (prev_high + prev_low + prev_close) / 3
    df["r1"] = (2 * df["pivot"]) - prev_low
    df["s1"] = (2 * df["pivot"]) - prev_high
    df["r2"] = df["pivot"] + (prev_high - prev_low)
    df["s2"] = df["pivot"] - (prev_high - prev_low)

    df["support"] = low.rolling(20, min_periods=3).min()
    df["resistance"] = high.rolling(20, min_periods=3).max()
    df["candle_pattern"] = detect_candle_patterns(df)
    return df


def detect_candle_patterns(df: pd.DataFrame) -> pd.Series:
    patterns = []
    prev = None
    for _, row in df.iterrows():
        open_price = row["open"]
        close = row["close"]
        high = row["high"]
        low = row["low"]
        body = abs(close - open_price)
        candle_range = max(high - low, 0.0001)
        upper = high - max(open_price, close)
        lower = min(open_price, close) - low

        label = "None"
        if body <= candle_range * 0.1:
            label = "Doji"
        elif lower >= body * 2 and upper <= body * 0.6:
            label = "Hammer" if close >= open_price else "Hanging Man"
        elif upper >= body * 2 and lower <= body * 0.6:
            label = "Shooting Star"

        if prev is not None:
            bullish_engulf = close > open_price and prev["close"] < prev["open"] and close > prev["open"] and open_price < prev["close"]
            bearish_engulf = close < open_price and prev["close"] > prev["open"] and close < prev["open"] and open_price > prev["close"]
            if bullish_engulf:
                label = "Bullish Engulfing"
            elif bearish_engulf:
                label = "Bearish Engulfing"

        patterns.append(label)
        prev = row
    return pd.Series(patterns, index=df.index)


def trendlines(df: pd.DataFrame, lookback: int = 40) -> Dict[str, Optional[float]]:
    if len(df) < 5:
        return {"support_line": None, "resistance_line": None, "slope": 0.0, "trend": "NEUTRAL"}

    sample = df.tail(lookback)
    x = np.arange(len(sample))
    support_line = np.polyfit(x, sample["low"], 1)
    resistance_line = np.polyfit(x, sample["high"], 1)
    support_now = float(np.polyval(support_line, len(sample) - 1))
    resistance_now = float(np.polyval(resistance_line, len(sample) - 1))
    slope = float((support_line[0] + resistance_line[0]) / 2)

    if slope > 0:
        trend = "UPTREND"
    elif slope < 0:
        trend = "DOWNTREND"
    else:
        trend = "NEUTRAL"

    return {
        "support_line": support_now,
        "resistance_line": resistance_now,
        "slope": slope,
        "trend": trend,
    }


def latest_levels(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    if df.empty:
        return {}
    row = df.iloc[-1]
    keys = ["support", "resistance", "support_line", "resistance_line", "pivot", "s1", "s2", "r1", "r2"]
    result = {}
    lines = trendlines(df)
    for key in keys:
        value = lines.get(key) if key in lines else row.get(key)
        result[key] = None if pd.isna(value) else round(float(value), 2)
    result["trend"] = lines["trend"]
    return result


def strategy_recommendations(
    df: pd.DataFrame,
    buyers_ratio: Optional[float] = None,
    news_bias: str = "NEUTRAL",
    underlying: str = "NIFTY",
) -> pd.DataFrame:
    if df.empty or len(df) < 5:
        return pd.DataFrame()

    row = df.iloc[-1]
    levels = latest_levels(df)
    price = float(row["close"])
    atr = float(row["atr_14"]) if not pd.isna(row.get("atr_14")) else max(price * 0.003, 1.0)
    rsi = float(row["rsi_14"]) if not pd.isna(row.get("rsi_14")) else 50.0
    support = levels.get("support") or price - atr
    resistance = levels.get("resistance") or price + atr
    trend = levels.get("trend", "NEUTRAL")
    pattern = row.get("candle_pattern", "None")

    news_score = 0
    if news_bias == "BULLISH":
        news_score = 1
    elif news_bias == "BEARISH":
        news_score = -1

    rows = []

    bull_score = int(price > row["ema_9"]) + int(price > row["vwap"]) + int(trend == "UPTREND") + int(rsi < 70) + int(news_score >= 0)
    bear_score = int(price < row["ema_9"]) + int(price < row["vwap"]) + int(trend == "DOWNTREND") + int(rsi > 30) + int(news_score <= 0)

    add_strategy(
        rows,
        "Livermore Pivotal Point",
        bull_score,
        "BUY CE / Sell PE spread" if bull_score >= bear_score else "BUY PE / Sell CE spread",
        price if price > resistance or price > row["ema_9"] else resistance,
        price + (2 * atr),
        price - atr,
        "Momentum through pivot/resistance with trend confirmation.",
        underlying,
    )
    add_strategy(
        rows,
        "Turtle / Donchian Breakout",
        int(price >= resistance) + int(trend == "UPTREND") + int(rsi > 50) + int(news_score >= 0),
        "BUY CE above range high",
        resistance,
        resistance + (2.5 * atr),
        resistance - atr,
        "Breakout continuation above the recent range.",
        underlying,
    )
    add_strategy(
        rows,
        "Darvas Box",
        int(support < price < resistance) + int(price > row["ema_20"]) + int(rsi >= 50),
        "BUY CE on box breakout",
        resistance,
        resistance + (resistance - support),
        support,
        "Trade the box only after price clears resistance.",
        underlying,
    )
    add_strategy(
        rows,
        "RSI Mean Reversion",
        int(rsi < 35 or rsi > 65) + int(abs(price - row["vwap"]) > atr),
        "BUY CE near support" if rsi < 35 else "BUY PE near resistance",
        support if rsi < 35 else resistance,
        row["vwap"],
        support - atr if rsi < 35 else resistance + atr,
        "Fade stretched moves back toward VWAP.",
        underlying,
    )
    add_strategy(
        rows,
        "Candle Reversal",
        int(pattern in ["Hammer", "Bullish Engulfing", "Shooting Star", "Bearish Engulfing"]) + int(price > support),
        candle_action(pattern),
        price,
        price + atr if "Bullish" in pattern or pattern == "Hammer" else price - atr,
        price - atr if "Bullish" in pattern or pattern == "Hammer" else price + atr,
        f"Latest candle: {pattern}.",
        underlying,
    )

    if buyers_ratio is not None:
        for item in rows:
            if item["Bias"].startswith("Bull") and buyers_ratio >= 55:
                item["Score"] += 1
            if item["Bias"].startswith("Bear") and buyers_ratio <= 45:
                item["Score"] += 1

    result = pd.DataFrame(rows)
    result["Score"] = result["Score"].clip(upper=5)
    result["Confidence"] = (result["Score"] * 20).astype(int).astype(str) + "%"
    return result.sort_values(["Score", "Strategy"], ascending=[False, True])


def add_strategy(rows, name, score, action, entry, target, stop, reason, underlying):
    bias = "Bullish" if target >= entry else "Bearish"
    if score < 2:
        action = "WAIT"
    option_side = "CE" if bias == "Bullish" else "PE"
    strike = option_strike(underlying, entry)
    contract = "WAIT" if action == "WAIT" else f"BUY {clean_underlying(underlying)} {strike} {option_side}"
    rows.append(
        {
            "Strategy": name,
            "Bias": bias,
            "Option": contract,
            "Action": action,
            "Entry": round(float(entry), 2),
            "Target": round(float(target), 2),
            "Stop": round(float(stop), 2),
            "Score": int(score),
            "Reason": reason,
        }
    )


def clean_underlying(value: str) -> str:
    text = str(value or "NIFTY").upper()
    if "SENSEX" in text:
        return "SENSEX"
    if "BANK" in text:
        return "BANKNIFTY"
    return "NIFTY"


def option_strike(underlying: str, price: float) -> int:
    step = 100 if clean_underlying(underlying) == "SENSEX" else 50
    return int(round(float(price) / step) * step)


def candle_action(pattern: str) -> str:
    if pattern in ["Hammer", "Bullish Engulfing"]:
        return "BUY CE after high break"
    if pattern in ["Shooting Star", "Bearish Engulfing", "Hanging Man"]:
        return "BUY PE after low break"
    return "WAIT"


def news_bias_from_headlines(headlines: List[str]) -> str:
    if not headlines:
        return "NEUTRAL"
    bullish_words = ["rally", "gain", "surge", "beat", "growth", "cut rates", "record", "strong", "bull"]
    bearish_words = ["fall", "drop", "selloff", "miss", "weak", "inflation", "war", "ban", "bear", "crash"]
    text = " ".join(headlines).lower()
    score = sum(word in text for word in bullish_words) - sum(word in text for word in bearish_words)
    if score > 0:
        return "BULLISH"
    if score < 0:
        return "BEARISH"
    return "NEUTRAL"


def now_label() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

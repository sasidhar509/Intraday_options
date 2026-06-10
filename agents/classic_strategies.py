from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import math
import numpy as np
import pandas as pd


def build_candles(history: List[Dict], interval: str = "1min") -> pd.DataFrame:
    """Convert tick history into OHLCV candles for dashboard analytics.

    Only supports the limited set of timeframes used by the system: 1min, 5min, 15min and 1d.
    If an unsupported interval is provided it will be coerced to 1min.
    """
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

    # restrict allowed intervals to known good values
    freq_map = {
        "1min": "1T",
        "5min": "5T",
        "15min": "15T",
        "1d": "1D",
        "1day": "1D",
        "day": "1D",
    }
    normalized = (str(interval or "1min")).lower()
    freq = freq_map.get(normalized, "1T")

    candles = (
        df.set_index("timestamp")
        .resample(freq)
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
    # additional context metrics
    df = add_market_structure_metrics(df)
    return df


def add_market_structure_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add simple metrics used by strategies: daily high/low, order blocks, FVG and BOS.

    These are intentionally straightforward implementations to provide signals
    for the strategy engine. They can be improved later with more sophisticated
    logic or by using option chain data.
    """
    if df.empty:
        return df

    df = df.copy()
    df["day"] = df["timestamp"].dt.date
    # compute day high/low for each row's day
    day_high = df.groupby("day")["high"].transform("max")
    day_low = df.groupby("day")["low"].transform("min")
    df["day_high"] = day_high
    df["day_low"] = day_low

    # detect simple order blocks: large body candles with volume spike
    vol_mean = df["volume"].replace(0, np.nan).rolling(50, min_periods=1).mean().fillna(0)
    body = (df["close"] - df["open"]).abs()
    large_body = body > body.rolling(50, min_periods=1).mean().fillna(0) * 1.5
    vol_spike = df["volume"] > (vol_mean * 1.5)
    df["order_block"] = np.where(large_body & vol_spike, df["open"], np.nan)

    # FVG (very simple): gap between consecutive candles
    fvg = [np.nan] * len(df)
    for i in range(len(df) - 1):
        if df["low"].iat[i] > df["high"].iat[i + 1]:
            fvg[i] = (df["high"].iat[i + 1], df["low"].iat[i])
        elif df["high"].iat[i] < df["low"].iat[i + 1]:
            fvg[i] = (df["high"].iat[i], df["low"].iat[i + 1])
    df["fvg_zone"] = fvg

    # BOS (break of structure) - detect break of recent swing high/low
    swings_high = df["high"].rolling(5, center=True, min_periods=1).max()
    swings_low = df["low"].rolling(5, center=True, min_periods=1).min()
    bos = []
    for i in range(len(df)):
        if df["close"].iat[i] > swings_high.shift(1).iat[i]:
            bos.append("BOS_UP")
        elif df["close"].iat[i] < swings_low.shift(1).iat[i]:
            bos.append("BOS_DOWN")
        else:
            bos.append(None)
    df["bos"] = bos
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
        price,
        df,
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
        resistance,
        df,
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
        resistance,
        df,
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
        support if rsi < 35 else resistance,
        df,
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
        price,
        df,
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


def _norm_sigma_from_df(df: Optional[pd.DataFrame]) -> float:
    """Estimate annualized volatility from recent closes in df.

    Fallback to a reasonable default if data is insufficient.
    """
    try:
        if df is None or df.empty or "close" not in df:
            return 0.25
        closes = df["close"].dropna()
        if len(closes) < 10:
            return 0.25
        returns = closes.pct_change().dropna()
        daily_vol = returns.std()
        annual_vol = float(daily_vol * math.sqrt(252))
        return max(0.08, min(1.5, annual_vol))
    except Exception:
        return 0.25


def _black_scholes_price(S, K, T, r, sigma, option_type: str = "call") -> float:
    """Compute Black-Scholes price for a European call/put.

    Uses an approximation for the normal CDF using math.erf.
    """
    if T <= 0 or sigma <= 0:
        # immediate expiry or zero vol -> intrinsic value
        if option_type == "call":
            return max(0.0, S - K)
        return max(0.0, K - S)

    from math import log, sqrt, exp

    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        # normal cdf
        def N(x):
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        if option_type == "call":
            return S * N(d1) - K * math.exp(-r * T) * N(d2)
        else:
            return K * math.exp(-r * T) * N(-d2) - S * N(-d1)
    except Exception:
        return max(0.0, S - K) if option_type == "call" else max(0.0, K - S)


def add_strategy(rows, name, score, action, entry, target, stop, reason, underlying, entry_price: Optional[float] = None, df: Optional[pd.DataFrame] = None):
    """Append a strategy row where Entry/Target/Stop are option-premium values.

    entry, target, stop are typically passed as underlying price levels. We estimate
    an option premium (CE/PE) using Black-Scholes with an IV estimated from df. The
    resulting Option contract uses the rounded strike and CE/PE side. If df or
    entry_price isn't provided we fall back to heuristic defaults.
    """
    # determine bias from the underlying target vs entry (as before)
    bias = "Bullish" if target >= entry else "Bearish"
    if score < 2:
        action = "WAIT"

    option_side = "CE" if bias == "Bullish" else "PE"
    strike = option_strike(underlying, entry)

    # underlying spot price to use for option pricing: prefer provided entry_price, else use entry
    S = float(entry_price) if entry_price is not None else float(entry)

    # estimate implied vol from candles df
    sigma = _norm_sigma_from_df(df)
    # time to expiry (years) - assume weekly expiry ~7 days
    T = 7.0 / 365.0
    r = 0.06
    option_type = "call" if option_side == "CE" else "put"
    fair_premium = _black_scholes_price(S, float(strike), T, r, sigma, option_type=option_type)
    # ensure a minimum tick
    fair_premium = max(0.5, fair_premium)

    # scale risk based on score/confidence: higher score -> tighter risk
    if score >= 4:
        risk_mult = 1.0
    elif score == 3:
        risk_mult = 1.5
    else:
        risk_mult = 2.0

    base_risk = max(1.0, fair_premium * 0.15) * risk_mult
    # target:SL ratio ~ 5:1
    entry_premium = fair_premium
    stop_premium = max(0.1, entry_premium - base_risk)
    target_premium = entry_premium + (base_risk * 5.0)

    contract = "WAIT" if action == "WAIT" else f"BUY {clean_underlying(underlying)} {strike} {option_side}"
    rows.append(
        {
            "Strategy": name,
            "Bias": bias,
            "Option": contract,
            "Action": action,
            # Entry/Target/Stop now reflect option premium values (approximate)
            "Entry": round(float(entry_premium), 2),
            "Target": round(float(target_premium), 2),
            "Stop": round(float(stop_premium), 2),
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
    cu = clean_underlying(underlying)
    # BANKNIFTY and SENSEX commonly use 100 point strike steps, others 50
    step = 100 if cu in ("SENSEX", "BANKNIFTY") else 50
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

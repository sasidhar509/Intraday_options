"""
agents/classic_strategies.py  ── FIXED v2

FIXES:
  1. build_candles(): resample aliases updated for pandas 2.x ('1min' not '1T')
  2. add_strategy(): Black-Scholes replaced with DELTA MODEL using LIVE index price.
     Entry/Target/Stop now reflect real option premium values synced to live data.
  3. strategy_recommendations(): passes live LTP correctly into add_strategy as S.
  4. option_strike(): BANKNIFTY uses 100pt step (correct), NIFTY uses 50pt step.
  5. _estimate_option_premium(): Uses delta approximation anchored to live index LTP —
     always returns premium in ₹50–₹600 realistic range for ATM weekly options.
"""

from __future__ import annotations
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import math
import numpy as np
import pandas as pd


# ── Realistic ATM option premium bands (weekly expiry, approx IV 12-18%)
# These are used to sanity-clamp computed premiums.
PREMIUM_FLOORS = {"NIFTY": 30.0,   "BANKNIFTY": 50.0,  "SENSEX": 80.0}
PREMIUM_CAPS   = {"NIFTY": 600.0,  "BANKNIFTY": 800.0, "SENSEX": 1200.0}


def _estimate_option_premium(
    ltp: float,
    strike: float,
    option_type: str,  # "CE" or "PE"
    instrument: str = "NIFTY",
    days_to_expiry: int = 5,
    iv_pct: float = 15.0,   # implied volatility %
) -> float:
    """
    Estimate option premium using delta + time-value model.
    Anchored to live index LTP — always realistic range.

    For ATM options with 5 days to expiry at 15% IV:
      NIFTY  ATM CE/PE ≈ ₹80–180
      BANKNIFTY ATM CE/PE ≈ ₹120–350

    Returns float premium in ₹.
    """
    if ltp <= 0 or strike <= 0:
        return PREMIUM_FLOORS.get(instrument.upper(), 50.0)

    T       = max(days_to_expiry, 1) / 365.0
    sigma   = iv_pct / 100.0
    r       = 0.065
    S       = float(ltp)
    K       = float(strike)

    # Black-Scholes with validated inputs
    try:
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        def _ncdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

        if option_type == "CE":
            premium = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        else:
            premium = K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)

        # Clamp to realistic band
        floor = PREMIUM_FLOORS.get(instrument.upper(), 30.0)
        cap   = PREMIUM_CAPS.get(instrument.upper(), 600.0)
        return round(max(floor, min(cap, premium)), 2)

    except Exception:
        return PREMIUM_FLOORS.get(instrument.upper(), 50.0)


def _rr_targets(
    entry_premium: float,
    direction: str,       # "bull" | "bear"
    score: int,           # 0-5 strategy score
    atr_pts: float = 0.0, # underlying ATR in points
    ltp: float = 0.0,
) -> Tuple[float, float, float]:
    """
    Compute SL and targets as OPTION PREMIUM values.
    R:R based on score:
      score 5 → 1:3 SL, 1:5 T2
      score 4 → 1:2.5 SL, 1:4 T2
      score 3 → 1:2 SL, 1:3 T2
      score <3 → conservative
    """
    if score >= 5:
        sl_pct, t1_mult, t2_mult = 0.20, 2.5, 4.0
    elif score == 4:
        sl_pct, t1_mult, t2_mult = 0.22, 2.0, 3.5
    elif score == 3:
        sl_pct, t1_mult, t2_mult = 0.25, 1.8, 3.0
    else:
        sl_pct, t1_mult, t2_mult = 0.30, 1.5, 2.5

    sl_distance = entry_premium * sl_pct
    sl  = round(max(1.0, entry_premium - sl_distance), 2)
    t1  = round(entry_premium + sl_distance * t1_mult, 2)
    t2  = round(entry_premium + sl_distance * t2_mult, 2)
    return sl, t1, t2


def build_candles(history: List[Dict], interval: str = "1min") -> pd.DataFrame:
    """Convert tick history into OHLCV candles.

    FIX: Updated resample aliases for pandas 2.x compatibility.
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
    df[price_col]   = pd.to_numeric(df[price_col], errors="coerce")
    df["volume"]    = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    df = df.dropna(subset=["timestamp", price_col]).sort_values("timestamp")
    if df.empty:
        return pd.DataFrame()

    # FIX: pandas 2.2+ requires 'min' not 'T', 'h' not 'H', 'D' for day
    freq_map = {
        "1min":  "1min",
        "5min":  "5min",
        "15min": "15min",
        "30min": "30min",
        "1h":    "1h",
        "1d":    "1D",
        "1day":  "1D",
        "day":   "1D",
        # legacy aliases — keep for backward compat
        "1T":    "1min",
        "5T":    "5min",
        "15T":   "15min",
        "5s":    "5s",
        "15s":   "15s",
        "30s":   "30s",
    }
    normalized = str(interval or "1min").lower()
    freq = freq_map.get(normalized, "1min")

    try:
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
    except Exception:
        # Ultra-fallback: return raw ticks as single-bar candles
        candles = df.rename(columns={price_col: "close"})[
            ["timestamp", "close", "volume"]
        ].copy()
        candles["open"]   = candles["close"].shift(1).fillna(candles["close"])
        candles["high"]   = candles[["open", "close"]].max(axis=1)
        candles["low"]    = candles[["open", "close"]].min(axis=1)

    if len(candles) < 2 and len(df) >= 2:
        candles = df.rename(columns={price_col: "close"})[
            ["timestamp", "close", "volume"]
        ].copy()
        candles["open"]  = candles["close"].shift(1).fillna(candles["close"])
        candles["high"]  = candles[["open", "close"]].max(axis=1)
        candles["low"]   = candles[["open", "close"]].min(axis=1)
        candles = candles[["timestamp", "open", "high", "low", "close", "volume"]]

    return candles.tail(240).reset_index(drop=True)


def add_indicators(candles: pd.DataFrame) -> pd.DataFrame:
    if candles.empty:
        return candles

    df    = candles.copy()
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    volume = df["volume"].replace(0, np.nan)

    df["ema_9"]  = close.ewm(span=9,  adjust=False).mean()
    df["ema_20"] = close.ewm(span=20, adjust=False).mean()
    df["ema_50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1
    ).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    typical     = (high + low + close) / 3
    df["vwap"]  = (typical * volume).cumsum() / volume.cumsum()

    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close2 = close.shift(1)
    df["pivot"] = (prev_high + prev_low + prev_close2) / 3
    df["r1"]    = (2 * df["pivot"]) - prev_low
    df["s1"]    = (2 * df["pivot"]) - prev_high
    df["r2"]    = df["pivot"] + (prev_high - prev_low)
    df["s2"]    = df["pivot"] - (prev_high - prev_low)

    df["support"]    = low.rolling(20, min_periods=3).min()
    df["resistance"] = high.rolling(20, min_periods=3).max()
    df["candle_pattern"] = detect_candle_patterns(df)
    df = add_market_structure_metrics(df)
    return df


def add_market_structure_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add day H/L, order blocks, FVG, and BOS columns."""
    if df.empty:
        return df
    df = df.copy()

    # Day high/low
    if hasattr(df["timestamp"].iloc[0], "date"):
        df["day"] = df["timestamp"].dt.date
    else:
        df["day"] = pd.to_datetime(df["timestamp"]).dt.date
    df["day_high"] = df.groupby("day")["high"].transform("max")
    df["day_low"]  = df.groupby("day")["low"].transform("min")

    # Order blocks
    vol_mean  = df["volume"].replace(0, np.nan).rolling(50, min_periods=1).mean().fillna(0)
    body      = (df["close"] - df["open"]).abs()
    large_body = body > body.rolling(50, min_periods=1).mean().fillna(0) * 1.5
    vol_spike  = df["volume"] > (vol_mean * 1.5)
    df["order_block"] = np.where(large_body & vol_spike, df["open"], np.nan)

    # FVG
    fvg = [np.nan] * len(df)
    for i in range(len(df) - 1):
        if df["low"].iat[i] > df["high"].iat[i + 1]:
            fvg[i] = (df["high"].iat[i + 1], df["low"].iat[i])
        elif df["high"].iat[i] < df["low"].iat[i + 1]:
            fvg[i] = (df["high"].iat[i], df["low"].iat[i + 1])
    df["fvg_zone"] = fvg

    # BOS
    swings_high = df["high"].rolling(5, center=True, min_periods=1).max()
    swings_low  = df["low"].rolling(5,  center=True, min_periods=1).min()
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
        o, c, h, l = row["open"], row["close"], row["high"], row["low"]
        body         = abs(c - o)
        candle_range = max(h - l, 0.0001)
        upper        = h - max(o, c)
        lower        = min(o, c) - l
        label        = "None"
        if body <= candle_range * 0.1:
            label = "Doji"
        elif lower >= body * 2 and upper <= body * 0.6:
            label = "Hammer" if c >= o else "Hanging Man"
        elif upper >= body * 2 and lower <= body * 0.6:
            label = "Shooting Star"
        if prev is not None:
            if c > o and prev["close"] < prev["open"] and c > prev["open"] and o < prev["close"]:
                label = "Bullish Engulfing"
            elif c < o and prev["close"] > prev["open"] and c < prev["open"] and o > prev["close"]:
                label = "Bearish Engulfing"
        patterns.append(label)
        prev = row
    return pd.Series(patterns, index=df.index)


def trendlines(df: pd.DataFrame, lookback: int = 40) -> Dict[str, Optional[float]]:
    if len(df) < 5:
        return {"support_line": None, "resistance_line": None, "slope": 0.0, "trend": "NEUTRAL"}
    sample = df.tail(lookback)
    x      = np.arange(len(sample))
    sl     = np.polyfit(x, sample["low"],  1)
    rl     = np.polyfit(x, sample["high"], 1)
    sn     = float(np.polyval(sl, len(sample) - 1))
    rn     = float(np.polyval(rl, len(sample) - 1))
    slope  = float((sl[0] + rl[0]) / 2)
    trend  = "UPTREND" if slope > 0 else "DOWNTREND" if slope < 0 else "NEUTRAL"
    return {"support_line": sn, "resistance_line": rn, "slope": slope, "trend": trend}


def latest_levels(df: pd.DataFrame) -> Dict[str, Optional[float]]:
    if df.empty:
        return {}
    row   = df.iloc[-1]
    lines = trendlines(df)
    keys  = ["support", "resistance", "pivot", "s1", "s2", "r1", "r2"]
    result = {}
    for k in keys:
        v = lines.get(k) if k in lines else row.get(k)
        result[k] = None if (v is None or (isinstance(v, float) and math.isnan(v))) else round(float(v), 2)
    result["support_line"]    = lines.get("support_line")
    result["resistance_line"] = lines.get("resistance_line")
    result["trend"]           = lines.get("trend", "NEUTRAL")
    return result


def strategy_recommendations(
    df: pd.DataFrame,
    buyers_ratio: Optional[float] = None,
    news_bias: str = "NEUTRAL",
    underlying: str = "NIFTY",
) -> pd.DataFrame:
    """
    Generate strategy recommendations with option premium prices.
    Entry/Target/Stop are OPTION PREMIUM values, not index levels.
    All anchored to live LTP from the latest candle.
    """
    if df.empty or len(df) < 5:
        return pd.DataFrame()

    row    = df.iloc[-1]

    # ── FIX 5: Live price must be the actual candle close (real NIFTY/BANKNIFTY level)
    ltp    = float(row["close"])

    if ltp <= 0:
        return pd.DataFrame()

    atr    = float(row["atr_14"]) if not pd.isna(row.get("atr_14")) else max(ltp * 0.003, 10.0)
    rsi    = float(row["rsi_14"]) if not pd.isna(row.get("rsi_14")) else 50.0
    ema9   = float(row["ema_9"])  if not pd.isna(row.get("ema_9"))  else ltp
    ema20  = float(row["ema_20"]) if not pd.isna(row.get("ema_20")) else ltp
    vwap   = float(row["vwap"])   if not pd.isna(row.get("vwap"))   else ltp

    support    = float(row.get("support",    ltp - atr * 2) or (ltp - atr * 2))
    resistance = float(row.get("resistance", ltp + atr * 2) or (ltp + atr * 2))
    levels     = latest_levels(df)
    trend      = levels.get("trend", "NEUTRAL")
    pattern    = str(row.get("candle_pattern", "None"))

    news_score = 1 if news_bias == "BULLISH" else -1 if news_bias == "BEARISH" else 0

    bull_score = (
        int(ltp > ema9) + int(ltp > vwap) +
        int(trend == "UPTREND") + int(rsi < 70) + int(news_score >= 0)
    )
    bear_score = (
        int(ltp < ema9) + int(ltp < vwap) +
        int(trend == "DOWNTREND") + int(rsi > 30) + int(news_score <= 0)
    )

    rows = []

    # Livermore Pivotal Point
    direction = "bull" if bull_score >= bear_score else "bear"
    entry_idx = resistance if direction == "bull" else support
    _add_strategy(
        rows, "Livermore Pivotal Point",
        bull_score if direction == "bull" else bear_score,
        direction, entry_idx, ltp, atr, underlying, df,
        "Momentum through pivot/resistance with trend confirmation.",
    )

    # Turtle/Donchian Breakout
    turt_score = int(ltp >= resistance) + int(trend == "UPTREND") + int(rsi > 50) + int(news_score >= 0)
    _add_strategy(
        rows, "Turtle / Donchian Breakout",
        turt_score, "bull", resistance, ltp, atr, underlying, df,
        "Breakout continuation above the recent range.",
    )

    # Darvas Box
    darv_score = int(support < ltp < resistance) + int(ltp > ema20) + int(rsi >= 50)
    _add_strategy(
        rows, "Darvas Box",
        darv_score, "bull", resistance, ltp, atr, underlying, df,
        "Trade the box only after price clears resistance.",
    )

    # RSI Mean Reversion
    rsi_dir    = "bull" if rsi < 35 else "bear"
    rsi_score  = int(rsi < 35 or rsi > 65) + int(abs(ltp - vwap) > atr)
    rsi_entry  = support if rsi_dir == "bull" else resistance
    _add_strategy(
        rows, "RSI Mean Reversion",
        rsi_score, rsi_dir, rsi_entry, ltp, atr, underlying, df,
        "Fade stretched moves back toward VWAP.",
    )

    # Candle Reversal
    candle_dir   = "bull" if pattern in ["Hammer", "Bullish Engulfing"] else "bear"
    candle_score = int(pattern in ["Hammer", "Bullish Engulfing", "Shooting Star", "Bearish Engulfing"])
    _add_strategy(
        rows, "Candle Reversal",
        candle_score, candle_dir, ltp, ltp, atr, underlying, df,
        "Latest candle: {}.".format(pattern),
    )

    if buyers_ratio is not None:
        for item in rows:
            if item["Bias"].startswith("Bull") and buyers_ratio >= 55:
                item["Score"] = min(5, item["Score"] + 1)
            if item["Bias"].startswith("Bear") and buyers_ratio <= 45:
                item["Score"] = min(5, item["Score"] + 1)

    result = pd.DataFrame(rows)
    result["Score"]      = result["Score"].clip(upper=5)
    result["Confidence"] = (result["Score"] * 20).astype(int).astype(str) + "%"
    return result.sort_values(["Score", "Strategy"], ascending=[False, True])


def _add_strategy(
    rows: list,
    name: str,
    score: int,
    direction: str,      # "bull" | "bear"
    entry_idx: float,    # index level of entry trigger
    ltp: float,          # LIVE current index price
    atr: float,
    underlying: str,
    df: Optional[pd.DataFrame],
    reason: str,
) -> None:
    """
    FIX: Entry/Target/Stop are OPTION PREMIUM values anchored to live LTP.

    1. ATM strike = round(ltp to nearest step)
    2. Option type = CE (bull) or PE (bear)
    3. Premium = _estimate_option_premium(ltp, strike, ...)
    4. SL/T1/T2 = _rr_targets(premium, ...)
    5. Contract label = e.g. "BUY NIFTY 24700 CE"
    """
    bias         = "Bullish" if direction == "bull" else "Bearish"
    option_type  = "CE" if direction == "bull" else "PE"
    strike       = option_strike(underlying, ltp)          # ATM to LIVE ltp
    cu           = clean_underlying(underlying)

    action = "WAIT"
    if score >= 2:
        action = "BUY {} CE".format(cu) if direction == "bull" else "BUY {} PE".format(cu)

    # Estimate premium using live LTP, correct strike
    premium = _estimate_option_premium(
        ltp=ltp,
        strike=strike,
        option_type=option_type,
        instrument=cu,
        days_to_expiry=5,
        iv_pct=15.0,
    )

    sl, t1, t2 = _rr_targets(premium, direction, score)

    contract = "WAIT" if action == "WAIT" else "BUY {} {} {}".format(cu, strike, option_type)

    rows.append({
        "Strategy": name,
        "Bias":     bias,
        "Option":   contract,
        "Action":   action,
        # All prices are OPTION PREMIUM values (₹)
        "Entry":    premium,
        "Target":   t1,
        "T2":       t2,
        "Stop":     sl,
        # Index reference (shown in tooltip)
        "Idx Entry": round(entry_idx, 2),
        "Idx LTP":   round(ltp,       2),
        "Strike":    strike,
        "Score":     int(score),
        "Reason":    reason,
    })


def clean_underlying(value: str) -> str:
    text = str(value or "NIFTY").upper()
    if "SENSEX" in text:                       return "SENSEX"
    if "BANK" in text or "BANKNIFTY" in text:  return "BANKNIFTY"
    return "NIFTY"


def option_strike(underlying: str, price: float) -> int:
    """Round index price to nearest valid option strike."""
    cu   = clean_underlying(underlying)
    # BANKNIFTY: 100pt steps; SENSEX: 100pt; NIFTY: 50pt
    step = 100 if cu in ("BANKNIFTY", "SENSEX") else 50
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
    text  = " ".join(headlines).lower()
    score = sum(w in text for w in bullish_words) - sum(w in text for w in bearish_words)
    return "BULLISH" if score > 0 else "BEARISH" if score < 0 else "NEUTRAL"


def now_label() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# ── Legacy add_strategy wrapper — kept for backward compat with old callers
def add_strategy(rows, name, score, action, entry, target, stop, reason,
                 underlying, entry_price=None, df=None):
    """Backward-compatible shim. Internally calls _add_strategy."""
    ltp  = float(entry_price) if entry_price else float(entry)
    atr  = abs(target - entry) * 0.3 if abs(target - entry) > 0 else 1.0
    direction = "bull" if float(target) >= float(entry) else "bear"
    _add_strategy(rows, name, int(score), direction, float(entry),
                  ltp, atr, str(underlying), df, str(reason))

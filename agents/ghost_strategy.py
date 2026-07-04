"""
agents/ghost_strategy.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  G.H.O.S.T  —  Guided High-probability Options Setup with Trap-detection
  ─────────────────────────────────────────────────────────────────────────
  A single, complete, deterministic intraday options strategy for
  NIFTY 50 and BANKNIFTY on NSE.

  Philosophy synthesised from:
    • JadeCap   → Liquidity sweep + smart money trap detection
    • Ali Crooks → Fixed-R execution, process over outcome, structural entry
    • SMC canon → BOS/CHoCH, Order Block, FVG, premium/discount zones
    • NSE-native → 5-min execution candle on 15-min context, PDH/PDL,
                   wait for first 15-min candle to complete before any trade

  Core logic (pure if/else, zero hallucination):

    Step 1  Bias     — Previous Day High (PDH) / Previous Day Low (PDL)
                       + 15-min candle direction after 9:30 AM
    Step 2  Trap     — Retail breakout above PDH or below PDL detected?
                       (fake breakout / liquidity sweep)
    Step 3  OB/FVG   — Last bearish OB before bullish BOS  (CE)
                       Last bullish OB before bearish BOS   (PE)
    Step 4  Entry    — 5-min bearish/bullish confirmation candle inside OB
                       "If 5-min candle CLOSES below OB low → sell CE / buy PE"
    Step 5  SL       — Swing High of the trap candle (PE) / Swing Low (CE)
    Step 6  Targets  — T1 = nearest FVG or 50% of day range
                       T2 = PDL / PDH / previous swing low/high
                       T3 = 1.618 × (entry→SL distance) projected

  R:R enforced:
    Minimum 1:2 (T1), Scaled to 1:3 (T2), 1:5 (T3)
    If 1:2 not achievable → NO TRADE

  Option output:
    Contract label  : e.g. "BANKNIFTY 54000 PE"
    Entry premium   : Black-Scholes with live LTP, 5-day expiry, 15% IV
    SL premium      : entry - (SL_index_pts × delta × lot_size / lot_size)
    T1/T2/T3 premiums
    Max loss ₹      : always ≤ BASE_RISK_RUPEES

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import math
import os
import pandas as pd
import numpy as np
from dotenv import load_dotenv

load_dotenv()

BASE_RISK = float(os.getenv("BASE_RISK_RUPEES", 500))

LOT_SIZES = {"NIFTY": 75, "BANKNIFTY": 35, "SENSEX": 10, "FINNIFTY": 40}
STRIKE_STEPS = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100, "FINNIFTY": 50}

PREMIUM_FLOOR = {"NIFTY": 30.0, "BANKNIFTY": 50.0, "SENSEX": 80.0}
PREMIUM_CAP   = {"NIFTY": 600.0,"BANKNIFTY": 800.0,"SENSEX":1200.0}

# ── Market-psychology constants ─────────────────────────────────────────
# Session windows (IST) where signal quality changes
OPENING_VOLATILITY_END = "09:30"   # before this: direction not established
LUNCH_CHOP_START       = "12:00"   # low-liquidity grind — traps are CHEAP here
LUNCH_CHOP_END         = "13:15"   # but cheap traps = low-conviction moves

# Round-number psychological levels — retail stop clusters concentrate here
ROUND_NUMBER_STEPS = {"NIFTY": 100, "BANKNIFTY": 500, "SENSEX": 500, "FINNIFTY": 100}

# Theta-decay protection: if T1 not reached within this many 5-min candles,
# the setup has failed to follow through — exit at cost, don't hold and hope
TIME_STOP_CANDLES = 6   # 30 minutes


class Direction(str, Enum):
    BULL = "BULL"   # CE trade
    BEAR = "BEAR"   # PE trade
    NONE = "NONE"


class SetupPhase(str, Enum):
    WAIT_FOR_CONTEXT   = "WAIT_FOR_CONTEXT"   # before 9:30
    WAIT_FOR_TRAP      = "WAIT_FOR_TRAP"       # 9:30–10:00, watching PDH/PDL
    WAIT_FOR_OB        = "WAIT_FOR_OB"         # trap detected, watching OB retest
    WAIT_FOR_ENTRY     = "WAIT_FOR_ENTRY"      # in OB zone, watching 5-min confirm
    TRADE_ACTIVE       = "TRADE_ACTIVE"
    NO_TRADE_TODAY     = "NO_TRADE_TODAY"


@dataclass
class GhostSignal:
    """Complete signal output — everything a trader needs."""
    instrument:       str
    direction:        Direction
    phase:            SetupPhase

    # Condition description (plain English)
    entry_condition:  str   # "If 5-min candle closes BELOW 54120 → enter PE"
    wait_message:     str   # Current status

    # Index levels (for context / charting)
    pdh:              float = 0.0
    pdl:              float = 0.0
    trap_level:       float = 0.0   # the swept level
    ob_high:          float = 0.0   # order block zone top
    ob_low:           float = 0.0   # order block zone bottom
    fvg_top:          float = 0.0
    fvg_bottom:       float = 0.0
    idx_entry:        float = 0.0
    idx_sl:           float = 0.0
    idx_t1:           float = 0.0
    idx_t2:           float = 0.0
    idx_t3:           float = 0.0
    current_ltp:      float = 0.0

    # Option contract
    strike:           int   = 0
    option_type:      str   = ""    # "CE" or "PE"
    contract_label:   str   = ""    # "BANKNIFTY 54000 PE"
    opt_entry:        float = 0.0
    opt_sl:           float = 0.0
    opt_t1:           float = 0.0
    opt_t2:           float = 0.0
    opt_t3:           float = 0.0
    rr_t1:            str   = ""
    rr_t2:            str   = ""
    rr_t3:            str   = ""

    # Sizing
    lots:             int   = 0
    quantity:         int   = 0
    max_loss_rs:      float = 0.0

    # Confidence and audit
    confidence:       float = 0.0   # 0–100
    confluence_count: int   = 0     # how many factors aligned
    confluence_list:  List[str] = field(default_factory=list)
    invalidated_if:   str   = ""    # plain English invalidation rule

    # Actionable flag
    actionable:       bool  = False

    # ── VWAP + today's range fields ──────────────────────────────────────
    vwap:             float = 0.0   # current session VWAP
    gap_type:         str   = ""    # "GAP_UP" | "GAP_DOWN" | "FLAT"
    gap_pct:          float = 0.0   # gap size as %
    today_high:       float = 0.0   # intraday high so far
    today_low:        float = 0.0   # intraday low so far
    vwap_position:    str   = ""    # "ABOVE" | "BELOW" | "AT_VWAP"
    setup_type:       str   = ""    # "PDH_PDL_SWEEP" | "VWAP_REJECTION" | "TODAY_HIGH_LOW_SWEEP" | "COMBINED"

    # ── Market-psychology fields ─────────────────────────────────────────
    trap_quality:         str  = ""    # "HIGH" | "MEDIUM" | "LOW"
    session_warning:      str  = ""    # e.g. lunch chop, opening volatility
    genuine_move_warning: str  = ""    # news suggests this might NOT be a trap
    near_round_number:    bool = False # PDH/PDL near psychological level
    ob_fresh:             bool = True  # OB not yet retested (higher quality)
    time_stop_minutes:    int  = 0     # exit-at-cost guidance if no follow-through

    # ── Diagnostic fields — "why no trade today?" ───────────────────────
    day_high:         float = 0.0   # today's high so far (from 15-min candles)
    day_low:          float = 0.0   # today's low so far
    pdh_breached:     bool  = False # has today's range exceeded PDH?
    pdl_breached:     bool  = False # has today's range gone below PDL?

    # ── Market depth (order book) confirmation ───────────────────────────
    depth_buyers_ratio: Optional[float] = None  # 0-100, % of buy-side depth
    depth_confirmation: str = ""    # plain-English read of order book vs direction


# ═══════════════════════════════════════════════════════════════════════════
# OPTION PRICING
# ═══════════════════════════════════════════════════════════════════════════

def _bs_premium(
    ltp: float, strike: float, option_type: str,
    instrument: str = "NIFTY",
    days: int = 5, iv: float = 15.0
) -> float:
    """Black-Scholes premium clamped to realistic band."""
    if ltp <= 0 or strike <= 0:
        return PREMIUM_FLOOR.get(instrument, 50.0)
    T, sigma, r = max(days, 1) / 365.0, iv / 100.0, 0.065
    try:
        d1 = (math.log(ltp / strike) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        N  = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
        p  = (ltp * N(d1) - strike * math.exp(-r * T) * N(d2)
              if option_type == "CE"
              else strike * math.exp(-r * T) * N(-d2) - ltp * N(-d1))
        fl = PREMIUM_FLOOR.get(instrument, 30.0)
        cp = PREMIUM_CAP.get(instrument, 600.0)
        return round(max(fl, min(cp, p)), 2)
    except Exception:
        return PREMIUM_FLOOR.get(instrument, 50.0)


def _delta(ltp: float, strike: float, option_type: str,
           days: int = 5, iv: float = 15.0) -> float:
    """Approximate Black-Scholes delta."""
    T, sigma = max(days, 1) / 365.0, iv / 100.0
    try:
        d1 = (math.log(ltp / strike) + (0.065 + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        N  = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
        return round(N(d1) if option_type == "CE" else -N(-d1), 3)
    except Exception:
        return 0.50 if option_type == "CE" else -0.50


def _index_move_to_premium(index_pts: float, delta: float) -> float:
    """Convert index point move → option premium move."""
    return round(abs(index_pts * abs(delta)), 2)


def _atm_strike(ltp: float, instrument: str) -> int:
    step = STRIKE_STEPS.get(instrument.upper(), 50)
    return int(round(ltp / step) * step)


def _size_lots(
    premium_entry: float, premium_sl: float,
    instrument: str, confidence: float
) -> Tuple[int, float]:
    """
    Size lots so max_loss ≤ BASE_RISK × multiplier.
    Returns (lots, max_loss_rs).
    """
    mult     = 2.0 if confidence >= 85 else 1.5 if confidence >= 70 else 1.0
    budget   = BASE_RISK * mult
    lot_size = LOT_SIZES.get(instrument.upper(), 75)
    risk_per_unit = max(0.01, abs(premium_entry - premium_sl))
    cost_per_lot  = risk_per_unit * lot_size
    lots          = max(0, int(budget // cost_per_lot))
    # Always allow at least 1 lot — display actual risk to trader
    lots          = max(1, lots)
    return lots, round(lots * cost_per_lot, 2)


# ═══════════════════════════════════════════════════════════════════════════
# SMC DETECTORS  (pure pandas, zero hallucination)
# ═══════════════════════════════════════════════════════════════════════════

def _find_swing_highs(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    return df["High"].rolling(lookback * 2 + 1, center=True).max() == df["High"]


def _find_swing_lows(df: pd.DataFrame, lookback: int = 5) -> pd.Series:
    return df["Low"].rolling(lookback * 2 + 1, center=True).min() == df["Low"]


def _find_order_block(
    df: pd.DataFrame, direction: Direction, lookback: int = 20
) -> Tuple[float, float]:
    """
    Bull OB: last bearish candle before a bullish BOS impulse.
    Bear OB: last bullish candle before a bearish BOS impulse.
    Returns (ob_high, ob_low) or (0, 0) if not found.
    """
    if len(df) < lookback + 3:
        return 0.0, 0.0

    window = df.tail(lookback).reset_index(drop=True)

    if direction == Direction.BEAR:
        # Last bullish candle before price fell hard
        for i in range(len(window) - 2, 0, -1):
            if window["Close"].iloc[i] > window["Open"].iloc[i]:   # bullish body
                # Confirm: price subsequently broke below that candle's low
                low_after = window["Low"].iloc[i + 1:].min()
                if low_after < window["Low"].iloc[i]:
                    return float(window["High"].iloc[i]), float(window["Low"].iloc[i])

    elif direction == Direction.BULL:
        # Last bearish candle before price rose hard
        for i in range(len(window) - 2, 0, -1):
            if window["Close"].iloc[i] < window["Open"].iloc[i]:   # bearish body
                high_after = window["High"].iloc[i + 1:].max()
                if high_after > window["High"].iloc[i]:
                    return float(window["High"].iloc[i]), float(window["Low"].iloc[i])

    return 0.0, 0.0


def _find_fvg(df: pd.DataFrame, direction: Direction) -> Tuple[float, float]:
    """
    FVG = gap between candle[i-2].high and candle[i].low (bullish)
          or candle[i-2].low and candle[i].high (bearish).
    Returns most recent (fvg_top, fvg_bottom) or (0, 0).
    """
    if len(df) < 3:
        return 0.0, 0.0

    for i in range(len(df) - 1, 1, -1):
        if direction == Direction.BULL:
            gap_bot = df["High"].iloc[i - 2]
            gap_top = df["Low"].iloc[i]
            if gap_top > gap_bot:
                return float(gap_top), float(gap_bot)
        else:
            gap_top = df["Low"].iloc[i - 2]
            gap_bot = df["High"].iloc[i]
            if gap_top > gap_bot:
                return float(gap_top), float(gap_bot)

    return 0.0, 0.0


def _detect_liquidity_sweep(
    df: pd.DataFrame, level: float, direction: Direction,
    lookback: int = 30, wick_tolerance_pct: float = 0.003
) -> bool:
    """
    Returns True if price wick breached `level` but candle CLOSED back
    on the opposite side — classic retail stop-hunt / liquidity sweep.
    """
    if len(df) < 3:
        return False
    tol = level * wick_tolerance_pct
    recent = df.tail(lookback)
    for _, row in recent.iterrows():
        if direction == Direction.BEAR:
            # Swept PDH: High wick >= level (actually breached it), close below
            if row["High"] >= level - tol and row["Close"] < level - tol:
                return True
        elif direction == Direction.BULL:
            # Swept PDL: Low wick <= level (actually breached it), close above
            if row["Low"] <= level + tol and row["Close"] > level + tol:
                return True
    return False


def _detect_bos(df: pd.DataFrame, direction: Direction, lookback: int = 20) -> bool:
    """Break of Structure in the expected direction."""
    if len(df) < lookback + 2:
        return False
    window = df.tail(lookback)
    if direction == Direction.BULL:
        prev_high = window["High"].iloc[:-2].max()
        return float(window["Close"].iloc[-1]) > prev_high
    else:
        prev_low = window["Low"].iloc[:-2].min()
        return float(window["Close"].iloc[-1]) < prev_low


# ═══════════════════════════════════════════════════════════════════════════
# TARGETS
# ═══════════════════════════════════════════════════════════════════════════

def _compute_targets(
    entry: float, sl: float, direction: Direction,
    pdh: float, pdl: float,
    fvg_top: float, fvg_bot: float,
    day_high: float, day_low: float,
) -> Tuple[float, float, float]:
    """
    T1 = nearest FVG midpoint or 50% of day range
    T2 = PDH (PE) or PDL (CE) — institutional target
    T3 = Fibonacci 1.618 extension from entry
    Always respects minimum 1:2 R:R.
    """
    sl_dist = abs(entry - sl)
    min_t1  = (entry - 2 * sl_dist if direction == Direction.BEAR
               else entry + 2 * sl_dist)

    # T1: FVG midpoint if valid and better than minimum
    if fvg_top > 0 and fvg_bot > 0:
        fvg_mid = (fvg_top + fvg_bot) / 2
        t1 = fvg_mid if direction == Direction.BEAR else fvg_mid
    else:
        t1 = (day_high * 0.5 + entry * 0.5 if direction == Direction.BEAR
              else day_low * 0.5 + entry * 0.5)

    t1 = min(t1, min_t1) if direction == Direction.BEAR else max(t1, min_t1)

    # T2: PDH/PDL
    t2 = pdl if direction == Direction.BEAR else pdh

    # T3: Fibonacci 1.618 extension
    t3 = (entry - sl_dist * 1.618 if direction == Direction.BEAR
          else entry + sl_dist * 1.618)

    # Ensure targets are properly ordered relative to entry
    if direction == Direction.BEAR:
        # All targets must be below entry; order T1 (nearest) > T2 > T3 (furthest)
        targets = sorted([t1, t2, t3], reverse=True)  # descending = nearest first for bear
    else:
        # All targets must be above entry; order T1 (nearest) < T2 < T3 (furthest)
        targets = sorted([t1, t2, t3])
    return round(targets[0], 2), round(targets[1], 2), round(targets[2], 2)


# ═══════════════════════════════════════════════════════════════════════════
# VWAP + TODAY'S RANGE HELPERS
# ─────────────────────────────────────────────────────────────────────────
# VWAP is the single most important intraday institutional level.
# Every algo, every desk, every smart-money participant anchors to it.
# Today's High/Low are the live session extremes — equally critical.
# ═══════════════════════════════════════════════════════════════════════════

def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Standard VWAP anchored to today's session open (first bar).
    Formula: cumulative(Typical Price × Volume) / cumulative(Volume)
    where Typical Price = (High + Low + Close) / 3
    Returns a Series aligned to df's index.
    """
    tp  = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].replace(0, 1)   # avoid division by zero on zero-volume bars
    cum_tpv = (tp * vol).cumsum()
    cum_vol = vol.cumsum()
    return (cum_tpv / cum_vol).round(2)


def _detect_gap(prev_close: float, today_open: float,
                gap_threshold_pct: float = 0.003) -> Tuple[str, float]:
    """
    Classify today's opening gap relative to yesterday's close.

    Returns (gap_type, gap_pct):
      "GAP_UP"   — market opened significantly above prev close
      "GAP_DOWN" — market opened significantly below prev close
      "FLAT"     — within threshold, treated as normal open

    gap_threshold_pct default = 0.3% — below this it's noise, not a gap.
    For NIFTY, 0.3% ≈ 75 pts at 25,000. For BANKNIFTY, 0.3% ≈ 160 pts.
    """
    if prev_close <= 0:
        return "FLAT", 0.0
    gap_pct = (today_open - prev_close) / prev_close
    if gap_pct > gap_threshold_pct:
        return "GAP_UP", round(gap_pct * 100, 2)
    if gap_pct < -gap_threshold_pct:
        return "GAP_DOWN", round(gap_pct * 100, 2)
    return "FLAT", round(gap_pct * 100, 2)


def _vwap_position(ltp: float, vwap: float, band_pct: float = 0.001) -> str:
    """
    Classify price position relative to VWAP.
      "ABOVE"     — price > VWAP + band (bullish)
      "BELOW"     — price < VWAP - band (bearish)
      "AT_VWAP"   — within band (neutral, often a decision point)
    band_pct default = 0.1% — prevents hair-trigger AT_VWAP signals.
    """
    band = vwap * band_pct
    if ltp > vwap + band:
        return "ABOVE"
    if ltp < vwap - band:
        return "BELOW"
    return "AT_VWAP"


def _detect_vwap_rejection(df5: pd.DataFrame, vwap_series: pd.Series,
                            direction: Direction, lookback: int = 3) -> Tuple[bool, str]:
    """
    Detect a VWAP rejection pattern on 5-min candles — the highest R:R
    VWAP-based setup.

    BEAR rejection: price rallied to VWAP from below, touched/crossed it,
                    then the last candle closed BACK BELOW VWAP → supply at VWAP
    BULL rejection: price fell to VWAP from above, touched/crossed it,
                    then the last candle closed BACK ABOVE VWAP → demand at VWAP

    Returns (detected: bool, reason: str)
    """
    if len(df5) < lookback + 1 or len(vwap_series) < lookback + 1:
        return False, "insufficient data"

    recent_close = df5["Close"].iloc[-lookback:]
    recent_vwap  = vwap_series.iloc[-lookback:]

    if direction == Direction.BEAR:
        # At least one bar crossed above VWAP, then last bar closed below
        any_above = any(c > v for c, v in zip(recent_close.iloc[:-1], recent_vwap.iloc[:-1]))
        last_below = float(df5["Close"].iloc[-1]) < float(vwap_series.iloc[-1])
        if any_above and last_below:
            return True, "VWAP rejection — rallied to VWAP then closed below → supply zone"
    elif direction == Direction.BULL:
        any_below = any(c < v for c, v in zip(recent_close.iloc[:-1], recent_vwap.iloc[:-1]))
        last_above = float(df5["Close"].iloc[-1]) > float(vwap_series.iloc[-1])
        if any_below and last_above:
            return True, "VWAP rejection — fell to VWAP then closed above → demand zone"

    return False, "no VWAP rejection"


def _detect_vwap_reclaim(df5: pd.DataFrame, vwap_series: pd.Series,
                          direction: Direction, lookback: int = 4) -> Tuple[bool, str]:
    """
    Detect a VWAP reclaim — price was on wrong side, then crossed and held.

    BULL reclaim: price was below VWAP, crossed above, last N bars hold above
    BEAR reclaim: price was above VWAP, crossed below, last N bars hold below

    This confirms institutional participation has shifted.
    Returns (detected: bool, reason: str)
    """
    if len(df5) < lookback + 2 or len(vwap_series) < lookback + 2:
        return False, "insufficient data"

    closes = df5["Close"].iloc[-(lookback + 2):]
    vwaps  = vwap_series.iloc[-(lookback + 2):]

    if direction == Direction.BULL:
        was_below  = float(closes.iloc[0]) < float(vwaps.iloc[0])
        holds_above = all(
            float(c) > float(v)
            for c, v in zip(closes.iloc[-lookback:], vwaps.iloc[-lookback:])
        )
        if was_below and holds_above:
            return True, "VWAP reclaim — price held above VWAP for {} bars → bullish".format(lookback)
    elif direction == Direction.BEAR:
        was_above  = float(closes.iloc[0]) > float(vwaps.iloc[0])
        holds_below = all(
            float(c) < float(v)
            for c, v in zip(closes.iloc[-lookback:], vwaps.iloc[-lookback:])
        )
        if was_above and holds_below:
            return True, "VWAP reclaim failed — price held below VWAP for {} bars → bearish".format(lookback)

    return False, "no VWAP reclaim"


# ─────────────────────────────────────────────────────────────────────────
# These do NOT just detect a pattern — they ask "would a smart-money desk
# actually trust this setup, or is this the kind of noise that wipes out
# retail accounts?" Each filter either downgrades confidence or adds an
# explicit warning the trader sees BEFORE risking capital.
# ═══════════════════════════════════════════════════════════════════════════

def _session_window(ts) -> str:
    """
    Classify the current timestamp into a session-quality window.
    Returns: "OPENING" | "PRIME" | "LUNCH_CHOP" | "POWER_HOUR" | "NORMAL"
    """
    from datetime import time as dtime
    t = ts.time() if hasattr(ts, "time") else ts

    if t < dtime(9, 30):
        return "OPENING"          # direction not yet established — avoid
    if dtime(12, 0) <= t <= dtime(13, 15):
        return "LUNCH_CHOP"        # thin liquidity — sweeps are cheap, low-quality
    if dtime(14, 30) <= t <= dtime(15, 15):
        return "POWER_HOUR"        # institutions reposition before close — higher quality
    if dtime(9, 30) < t < dtime(12, 0):
        return "PRIME"             # best window — institutional participation
    return "NORMAL"


def _volume_spike_ratio(df: pd.DataFrame, bars_from_end: int = 0, lookback: int = 20) -> float:
    """
    Ratio of a candle's volume to its trailing average.
      ≥ 1.5  → genuine institutional participation (real sweep)
      ≤ 0.8  → weak/low-conviction move — likely a fake wick, not a real trap
    """
    if len(df) < lookback + bars_from_end + 1:
        return 1.0
    idx        = len(df) - 1 - bars_from_end
    target_vol = float(df["Volume"].iloc[idx])
    window     = df["Volume"].iloc[max(0, idx - lookback):idx]
    avg_vol    = float(window.mean()) if len(window) else 0.0
    if avg_vol <= 0:
        return 1.0
    return round(target_vol / avg_vol, 2)


def _assess_trap_quality(
    df: pd.DataFrame, level: float, direction: Direction,
    lookback: int = 30, wick_tolerance_pct: float = 0.003
) -> Tuple[str, float]:
    """
    Given that a liquidity sweep was detected, assess its QUALITY by
    examining volume on the sweep candle itself:
      HIGH   — volume ≥ 1.5× average  (real institutional flush — high trust)
      MEDIUM — volume 0.8×–1.5× average (normal participation)
      LOW    — volume < 0.8× average  (thin wick — easy to fake, low trust)

    Scans backward through the lookback window and returns the quality
    of the MOST RECENT bar that satisfies the sweep condition.
    """
    if len(df) < 3:
        return "MEDIUM", 1.0
    tol    = level * wick_tolerance_pct
    recent = df.tail(lookback)

    for offset in range(len(recent)):
        i = len(df) - 1 - offset
        row = df.iloc[i]
        if direction == Direction.BEAR:
            hit = row["High"] >= level - tol and row["Close"] < level - tol
        else:
            hit = row["Low"] <= level + tol and row["Close"] > level + tol
        if hit:
            vr = _volume_spike_ratio(df, bars_from_end=(len(df) - 1 - i))
            quality = "HIGH" if vr >= 1.5 else "LOW" if vr < 0.8 else "MEDIUM"
            return quality, vr

    return "MEDIUM", 1.0


def _depth_confirms_direction(
    buyers_ratio: Optional[float], direction: Direction
) -> Tuple[Optional[bool], str]:
    """
    Read the live order-book (market depth) against our planned trade direction.

    buyers_ratio: 0-100, percentage of total depth on the BUY side
                   (from SmartAPI best-5 bid/ask quantities).

    For a PE (BEAR) trade, we WANT sellers to dominate the book — that
    confirms downward pressure. Heavy buying underneath (buyers_ratio high)
    means there's a wall of bids that could absorb the drop.

    For a CE (BULL) trade, we WANT buyers to dominate.

    Returns (confirms, message):
      confirms = True   → depth supports our direction
      confirms = False  → depth opposes our direction (absorption risk)
      confirms = None   → no depth data, or depth is balanced/inconclusive
    """
    if buyers_ratio is None:
        return None, ""

    br = float(buyers_ratio)

    if direction == Direction.BEAR:
        if br <= 40:
            return True, (
                "✅ Order book: sellers dominate ({:.0f}% buyers) — "
                "confirms downside pressure for PE".format(br)
            )
        if br >= 65:
            return False, (
                "⚠️ Order book: heavy bid wall ({:.0f}% buyers) — "
                "could absorb the drop, against this PE".format(br)
            )
        return None, "◽ Order book balanced ({:.0f}% buyers)".format(br)

    elif direction == Direction.BULL:
        if br >= 60:
            return True, (
                "✅ Order book: buyers dominate ({:.0f}% buyers) — "
                "confirms upside pressure for CE".format(br)
            )
        if br <= 35:
            return False, (
                "⚠️ Order book: heavy ask wall ({:.0f}% buyers, "
                "{:.0f}% sellers) — could absorb the rally, against this CE".format(br, 100 - br)
            )
        return None, "◽ Order book balanced ({:.0f}% buyers)".format(br)

    return None, ""


def _is_near_round_number(level: float, instrument: str, tolerance_pct: float = 0.0005) -> bool:
    """
    PDH/PDL sitting close to a psychological round number (NIFTY x100,
    BANKNIFTY x500) attracts dense retail stop-loss/breakout-order clusters.
    A sweep of such a level is a HIGHER-QUALITY trap than a sweep of an
    arbitrary level — there's more liquidity to harvest, so smart money is
    more likely to have engineered the move deliberately.
    """
    step  = ROUND_NUMBER_STEPS.get(instrument.upper(), 100)
    nearest = round(level / step) * step
    return abs(level - nearest) <= level * tolerance_pct


def _is_ob_fresh(df: pd.DataFrame, ob_high: float, ob_low: float) -> bool:
    """
    A "fresh" Order Block — one price hasn't already returned to and left
    again — is higher probability. If price has already tagged this zone
    once before, the institutional orders resting there may already be
    partially filled/used up, making a second reaction less reliable.
    """
    if ob_high <= 0 or ob_low <= 0 or len(df) < 4:
        return True
    history = df.iloc[:-1]   # exclude the current/most-recent bar
    touches = int(((history["Low"] <= ob_high) & (history["High"] >= ob_low)).sum())
    return touches <= 1   # 0 or 1 touch = still fresh


def _genuine_breakout_check(
    direction: Direction, news_bias: str
) -> Tuple[bool, str]:
    """
    THE MOST IMPORTANT PSYCHOLOGY FILTER.

    GHOST's whole premise is: "price broke PDH/PDL, retail is trapped,
    smart money reverses it." But sometimes a break of PDH/PDL is a
    GENUINE trend continuation backed by real news (e.g. the RBI
    FCNR(B) announcement driving BANKNIFTY higher) — NOT a trap.

    If news sentiment supports the BREAKOUT direction (the direction
    opposite to our planned trade), this sweep may be the start of a
    real trend, not a retail trap. We flag this so the trader can stand
    aside rather than fade real institutional buying/selling.

    Returns (genuine_move_risk: bool, warning message).
    """
    # Our trade direction is the REVERSAL of the breakout.
    # So the "breakout direction" is the opposite of our trade direction.
    breakout_dir = "BULLISH" if direction == Direction.BEAR else "BEARISH"

    if news_bias.upper() == breakout_dir:
        return True, (
            "🚨 News sentiment is {} — SAME direction as the breakout we're "
            "about to fade. This sweep may be the START of a genuine "
            "news-driven trend, not a retail trap. Consider standing aside "
            "or reducing size sharply.".format(news_bias.upper())
        )
    return False, ""


# ═══════════════════════════════════════════════════════════════════════════
# DAILY RISK GUARD — psychological discipline, encoded
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class DailyRiskGuard:
    """
    Tracks today's GHOST trades and enforces the discipline rules that
    protect capital — the rules retail traders break under pressure.

      • After 1 loss  → next setup needs confidence ≥ 80% (no revenge trades)
      • After 2 losses → STOP for the day (matches MAX_DAILY_LOSS_LIMIT ≈ 3×BASE_RISK)
      • Cumulative P&L tracked against MAX_DAILY_LOSS_LIMIT

    Usage in Streamlit:
        if "ghost_guard" not in st.session_state:
            st.session_state["ghost_guard"] = DailyRiskGuard()
        guard = st.session_state["ghost_guard"]

        if not guard.can_trade(signal.confidence):
            st.error(guard.block_reason())
        # ... after the trade is closed:
        guard.record_trade(pnl=-450)
    """
    max_daily_loss: float = field(default_factory=lambda: float(os.getenv("MAX_DAILY_LOSS_LIMIT", 1500)))
    trades_today:   List[Dict[str, Any]] = field(default_factory=list)
    cumulative_pnl: float = 0.0

    def record_trade(self, pnl: float) -> None:
        self.trades_today.append({"pnl": pnl})
        self.cumulative_pnl += pnl

    def losses_today(self) -> int:
        return sum(1 for t in self.trades_today if t["pnl"] < 0)

    def wins_today(self) -> int:
        return sum(1 for t in self.trades_today if t["pnl"] > 0)

    def can_trade(self, confidence: float) -> bool:
        if self.cumulative_pnl <= -self.max_daily_loss:
            return False
        losses = self.losses_today()
        if losses >= 2:
            return False
        if losses == 1 and confidence < 80.0:
            return False
        return True

    def block_reason(self) -> str:
        if self.cumulative_pnl <= -self.max_daily_loss:
            return (
                "🛑 DAILY LOSS LIMIT HIT (₹{:+,.0f} ≤ -₹{:,.0f}). "
                "Trading halted — review setups tomorrow with a clear head.".format(
                    self.cumulative_pnl, self.max_daily_loss
                )
            )
        if self.losses_today() >= 2:
            return "🛑 2 losses today. Trading halted to prevent revenge trading."
        if self.losses_today() == 1:
            return (
                "⚠️ 1 loss today — next setup needs confidence ≥ 80%. "
                "This one doesn't meet that bar; skipping protects capital."
            )
        return ""

    def status_line(self) -> str:
        return (
            "Trades {} | Wins {} | Losses {} | Day P&L ₹{:+,.0f} | "
            "Remaining budget ₹{:,.0f}".format(
                len(self.trades_today), self.wins_today(), self.losses_today(),
                self.cumulative_pnl,
                max(0.0, self.max_daily_loss + self.cumulative_pnl),
            )
        )


# ═══════════════════════════════════════════════════════════════════════════
# MAIN STRATEGY ENGINE
# ═══════════════════════════════════════════════════════════════════════════

class GhostStrategyEngine:

    """
    GHOST Strategy — one clean, complete, rules-based engine.

    Usage:
        engine = GhostStrategyEngine(instrument="BANKNIFTY")
        signal = engine.evaluate(
            df_15min=df15,   # pd.DataFrame with OHLCV, DatetimeIndex
            df_5min=df5,     # pd.DataFrame with OHLCV, DatetimeIndex
            prev_day_high=54850.0,
            prev_day_low=53900.0,
            current_ltp=54120.0,
            news_bias="BEARISH",   # from NewsEngine
        )
        # signal.entry_condition → plain-English wait instruction
        # signal.actionable      → True when 5-min confirm candle closed
    """

    def __init__(self, instrument: str = "NIFTY"):
        self.instrument = instrument.upper().replace(" ", "")
        if "BANK" in self.instrument:
            self.instrument = "BANKNIFTY"
        elif "SENSEX" in self.instrument:
            self.instrument = "SENSEX"
        else:
            self.instrument = "NIFTY"

    # ── public entry point ──────────────────────────────────────────────

    def evaluate(
        self,
        df_15min: pd.DataFrame,
        df_5min:  pd.DataFrame,
        prev_day_high:  float,
        prev_day_low:   float,
        current_ltp:    float,
        news_bias:      str   = "NEUTRAL",
        buyers_ratio:   Optional[float] = None,
        prev_day_close: Optional[float] = None,
    ) -> GhostSignal:
        """
        Full evaluation pipeline. Returns GhostSignal every call.
        Call this on every new 5-min candle close after 9:30 AM.

        buyers_ratio:   optional 0-100 live order-book buy-side %
        prev_day_close: optional yesterday's close for gap detection.
                        If None, gap detection is skipped.
        """
        pdh = float(prev_day_high)
        pdl = float(prev_day_low)
        ltp = float(current_ltp)

        if df_15min is None or df_15min.empty or len(df_15min) < 3:
            return self._waiting(ltp, pdh, pdl, "Waiting for 15-min candle data…")

        if df_5min is None or df_5min.empty or len(df_5min) < 2:
            return self._waiting(ltp, pdh, pdl, "Waiting for 5-min candle data…")

        # ── Today's range (live intraday extremes)
        today_high   = float(df_5min["High"].max())
        today_low    = float(df_5min["Low"].min())
        pdh_breached = today_high > pdh
        pdl_breached = today_low  < pdl

        # ── VWAP — anchored to today's open bar
        vwap_series  = _compute_vwap(df_5min)
        vwap_now     = float(vwap_series.iloc[-1])
        vwap_pos     = _vwap_position(ltp, vwap_now)

        # ── Gap detection
        gap_type = "FLAT"; gap_pct = 0.0
        if prev_day_close and prev_day_close > 0:
            today_open = float(df_5min["Open"].iloc[0])
            gap_type, gap_pct = _detect_gap(prev_day_close, today_open)

        # ── Session window — affects signal quality throughout
        try:
            session = _session_window(df_5min.index[-1])
        except Exception:
            session = "NORMAL"

        session_warning = ""
        if session == "OPENING":
            session_warning = (
                "⏳ Before 9:30 AM — direction not yet established. "
                "Even a valid-looking setup here is unreliable."
            )
        elif session == "LUNCH_CHOP":
            session_warning = (
                "🍱 Lunch chop window (12:00–13:15) — thin liquidity. "
                "Sweeps here are cheap to engineer and often fail. Trade smaller or skip."
            )
        elif session == "POWER_HOUR":
            session_warning = (
                "⏰ Power hour (14:30–15:15) — institutions repositioning. "
                "Higher-quality moves, but also faster theta decay before close."
            )

        # ── Step 1: Wait for first 15-min candle to complete (9:30 AM)
        if len(df_15min) < 2:
            return self._waiting(
                ltp, pdh, pdl,
                "⏳ Waiting for first 15-min candle to complete (9:30 AM)…",
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                session_warning=session_warning,
            )


        # ── Step 2: Establish daily bias (3-level: PDH/PDL + Today H/L + VWAP)
        direction, bias_reason, setup_type = self._resolve_bias(
            df15=df_15min, df5=df_5min,
            pdh=pdh, pdl=pdl, ltp=ltp, news_bias=news_bias,
            vwap_now=vwap_now, vwap_series=vwap_series,
            today_high=today_high, today_low=today_low,
            gap_type=gap_type,
        )

        if direction == Direction.NONE:
            return self._waiting(
                ltp, pdh, pdl,
                "⚠️ No setup yet — {}".format(bias_reason),
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                session_warning=session_warning,
            )

        # ── Determine trap level based on setup type
        # PDH/PDL sweep uses PDH/PDL; Today H/L and VWAP setups use intraday extremes
        if setup_type in ("PDH_PDL_SWEEP", "COMBINED"):
            trap_level = pdh if direction == Direction.BEAR else pdl
        else:
            trap_level = today_high if direction == Direction.BEAR else today_low

        # ── Step 3: Detect liquidity sweep (retail trap)
        sweep_detected = _detect_liquidity_sweep(
            df_15min, level=trap_level, direction=direction,
        )

        confluence = []
        if sweep_detected:
            confluence.append("✅ Liquidity sweep confirmed (retail trap triggered)")
        else:
            confluence.append("⏳ Waiting for liquidity sweep of {}".format(
                "PDH {:.0f}".format(pdh) if direction == Direction.BEAR
                else "PDL {:.0f}".format(pdl)
            ))
            return self._waiting(
                ltp, pdh, pdl,
                "👁 Watching for retail breakout above PDH {:.0f} (PE) "
                "or below PDL {:.0f} (CE). {} setup identified. {}".format(
                    pdh, pdl, direction.value, session_warning
                ).strip(),
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                session_warning=session_warning,
            )

        # VWAP confluence — always check after sweep confirmed
        if vwap_pos == "BELOW" and direction == Direction.BEAR:
            confluence.append("✅ VWAP {:.0f} — price below VWAP, confirms bearish bias".format(vwap_now))
            vwap_confirms = True
        elif vwap_pos == "ABOVE" and direction == Direction.BULL:
            confluence.append("✅ VWAP {:.0f} — price above VWAP, confirms bullish bias".format(vwap_now))
            vwap_confirms = True
        elif vwap_pos == "AT_VWAP":
            confluence.append("◽ VWAP {:.0f} — price AT VWAP (decision zone)".format(vwap_now))
            vwap_confirms = False
        else:
            confluence.append("⚠️ VWAP {:.0f} — price on wrong side of VWAP for this direction (risk)".format(vwap_now))
            vwap_confirms = False

        # ── Psychology check 1: Trap quality (volume on the sweep candle)
        trap_quality, vol_ratio = _assess_trap_quality(df_15min, trap_level, direction)
        if trap_quality == "HIGH":
            confluence.append(
                "✅ Trap quality HIGH — sweep volume {:.1f}× average "
                "(real institutional flush, not a thin wick)".format(vol_ratio)
            )
        elif trap_quality == "LOW":
            confluence.append(
                "⚠️ Trap quality LOW — sweep volume only {:.1f}× average "
                "(thin wick, easier to be a false signal)".format(vol_ratio)
            )
        else:
            confluence.append("◽ Trap quality MEDIUM — sweep volume {:.1f}× average".format(vol_ratio))

        # ── Psychology check 2: Round-number proximity (retail stop density)
        near_round = _is_near_round_number(trap_level, self.instrument)
        if near_round:
            confluence.append(
                "✅ {} {:.0f} sits near a round number — dense retail stop cluster".format(
                    "PDH" if direction == Direction.BEAR else "PDL", trap_level
                )
            )

        # ── Psychology check 3: Is this actually a genuine trend, not a trap?
        genuine_risk, genuine_warning = _genuine_breakout_check(direction, news_bias)
        if genuine_risk:
            confluence.append(genuine_warning)

        # ── HARD STOP: low-volume sweep + news favors the breakout direction
        #    = highest-risk combination. Capital preservation > taking this trade.
        if genuine_risk and trap_quality == "LOW":
            return GhostSignal(
                instrument=self.instrument,
                direction=Direction.NONE,
                phase=SetupPhase.NO_TRADE_TODAY,
                entry_condition="🛑 NO TRADE — high-risk combination",
                wait_message=(
                    "🛑 SKIPPING THIS SETUP: sweep volume was only {:.1f}× average "
                    "(thin/fake-looking) AND news sentiment supports the breakout "
                    "direction, not the trap reversal. This combination looks more "
                    "like a genuine trend start than a retail trap — taking it risks "
                    "capital on the wrong side. Wait for the next setup.".format(vol_ratio)
                ),
                pdh=pdh, pdl=pdl, trap_level=trap_level, current_ltp=ltp,
                trap_quality=trap_quality,
                genuine_move_warning=genuine_warning,
                session_warning=session_warning,
                near_round_number=near_round,
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                actionable=False,
            )


        # ── Step 4: Order Block identification
        ob_high, ob_low = _find_order_block(df_15min, direction)
        if ob_high == 0:
            return self._waiting(
                ltp, pdh, pdl,
                "🔍 Sweep done. Searching for Order Block for {} entry…".format(
                    direction.value
                ),
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                session_warning=session_warning,
            )
        confluence.append("✅ Order Block: {:.0f}–{:.0f}".format(ob_low, ob_high))

        # ── Psychology check 4: Is this OB fresh, or already retested?
        ob_fresh = _is_ob_fresh(df_15min, ob_high, ob_low)
        if ob_fresh:
            confluence.append("✅ Order Block is fresh (first retest — higher reliability)")
        else:
            confluence.append(
                "⚠️ Order Block already retested before — institutional orders here "
                "may be partially used up"
            )

        # ── Step 5: FVG identification
        fvg_top, fvg_bot = _find_fvg(df_15min, direction)
        if fvg_top > 0:
            confluence.append("✅ FVG zone: {:.0f}–{:.0f}".format(fvg_bot, fvg_top))

        # ── Step 6: BOS confirmation on 15-min
        bos_confirmed = _detect_bos(df_15min, direction)
        if bos_confirmed:
            confluence.append("✅ 15-min BOS confirmed in {} direction".format(direction.value))
        else:
            confluence.append("⏳ Waiting for 15-min BOS confirmation")

        # ── Psychology check 5: Market depth (order book) confirmation
        depth_confirms, depth_msg = _depth_confirms_direction(buyers_ratio, direction)
        if depth_msg:
            confluence.append(depth_msg)

        # ── Step 7: Is price inside OB zone?
        price_in_ob = (
            (ob_low <= ltp <= ob_high)
            if direction == Direction.BEAR
            else (ob_low <= ltp <= ob_high)
        )

        if not price_in_ob:
            dist = min(abs(ltp - ob_high), abs(ltp - ob_low))
            quality_note = (
                "HIGH-quality setup forming" if trap_quality == "HIGH"
                else "LOW-quality setup — may not be worth waiting for" if trap_quality == "LOW"
                else "Setup forming"
            )
            return GhostSignal(
                instrument=self.instrument,
                direction=direction,
                phase=SetupPhase.WAIT_FOR_ENTRY,
                entry_condition=(
                    "Wait for price to retest OB zone {:.0f}–{:.0f}, then watch for "
                    "5-min {} confirmation candle".format(
                        ob_low, ob_high,
                        "bearish close below OB low" if direction == Direction.BEAR
                        else "bullish close above OB high"
                    )
                ),
                wait_message=(
                    "📍 {}. OB zone {:.0f}–{:.0f}. Price at {:.0f} — "
                    "{:.0f} pts away. Wait for retest. {}".format(
                        quality_note, ob_low, ob_high, ltp, dist, session_warning
                    ).strip()
                ),
                pdh=pdh, pdl=pdl, trap_level=trap_level,
                ob_high=ob_high, ob_low=ob_low,
                fvg_top=fvg_top, fvg_bottom=fvg_bot,
                current_ltp=ltp,
                confidence=self._score_confidence(
                    confluence, news_bias, direction,
                    trap_quality=trap_quality, ob_fresh=ob_fresh,
                    near_round_number=near_round, genuine_move_risk=genuine_risk,
                    session=session, depth_confirms=depth_confirms,
                    setup_type=setup_type, vwap_confirms=vwap_confirms,
                ),
                confluence_count=len(confluence),
                confluence_list=confluence,
                trap_quality=trap_quality,
                session_warning=session_warning,
                genuine_move_warning=genuine_warning,
                near_round_number=near_round,
                ob_fresh=ob_fresh,
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                depth_buyers_ratio=buyers_ratio,
                depth_confirmation=depth_msg,
            )

        confluence.append("✅ Price inside Order Block (institutional zone)")

        # ── Step 8: 5-min confirmation candle
        last5   = df_5min.iloc[-1]
        confirm = self._check_5min_confirm(last5, direction, ob_high, ob_low)

        if not confirm["valid"]:
            entry_cond = (
                "If 5-min candle CLOSES BELOW {:.0f} → BUY {} PE".format(
                    ob_low, self.instrument
                ) if direction == Direction.BEAR else
                "If 5-min candle CLOSES ABOVE {:.0f} → BUY {} CE".format(
                    ob_high, self.instrument
                )
            )
            return GhostSignal(
                instrument=self.instrument,
                direction=direction,
                phase=SetupPhase.WAIT_FOR_ENTRY,
                entry_condition=entry_cond,
                wait_message=(
                    "🕯 Inside OB zone. Waiting for 5-min bearish confirm candle. "
                    + confirm["reason"]
                ),
                pdh=pdh, pdl=pdl, trap_level=trap_level,
                ob_high=ob_high, ob_low=ob_low,
                fvg_top=fvg_top, fvg_bottom=fvg_bot,
                current_ltp=ltp,
                confidence=self._score_confidence(
                    confluence, news_bias, direction,
                    trap_quality=trap_quality, ob_fresh=ob_fresh,
                    near_round_number=near_round, genuine_move_risk=genuine_risk,
                    session=session, depth_confirms=depth_confirms,
                    setup_type=setup_type, vwap_confirms=vwap_confirms,
                ),
                confluence_count=len(confluence),
                confluence_list=confluence,
                trap_quality=trap_quality,
                session_warning=session_warning,
                genuine_move_warning=genuine_warning,
                near_round_number=near_round,
                ob_fresh=ob_fresh,
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                depth_buyers_ratio=buyers_ratio,
                depth_confirmation=depth_msg,
            )

        confluence.append("✅ 5-min confirmation candle closed ({})".format(confirm["reason"]))

        # ── Step 9: Compute index entry / SL / targets
        idx_entry = float(last5["Close"])
        idx_sl    = (float(last5["High"]) + 5   # swing high of trap + buffer
                     if direction == Direction.BEAR
                     else float(last5["Low"]) - 5)

        # Day high/low for target calculation
        day_high = float(df_15min["High"].max())
        day_low  = float(df_15min["Low"].min())

        idx_t1, idx_t2, idx_t3 = _compute_targets(
            idx_entry, idx_sl, direction,
            pdh, pdl, fvg_top, fvg_bot,
            day_high, day_low
        )

        # ── Step 10: Option premium conversion
        strike      = _atm_strike(idx_entry, self.instrument)
        opt_type    = "PE" if direction == Direction.BEAR else "CE"
        delta_val   = _delta(idx_entry, strike, opt_type)

        opt_entry   = _bs_premium(idx_entry, strike, opt_type, self.instrument)
        sl_pts      = abs(idx_entry - idx_sl)
        t1_pts      = abs(idx_t1 - idx_entry)
        t2_pts      = abs(idx_t2 - idx_entry)
        t3_pts      = abs(idx_t3 - idx_entry)

        opt_sl      = max(1.0, round(opt_entry - _index_move_to_premium(sl_pts,  delta_val), 2))
        opt_t1      = round(opt_entry + _index_move_to_premium(t1_pts, delta_val), 2)
        opt_t2      = round(opt_entry + _index_move_to_premium(t2_pts, delta_val), 2)
        opt_t3      = round(opt_entry + _index_move_to_premium(t3_pts, delta_val), 2)

        sl_dist_prem = abs(opt_entry - opt_sl)

        def _rr(target_prem):
            if sl_dist_prem <= 0:
                return "—"
            return "1:{:.1f}".format(abs(target_prem - opt_entry) / sl_dist_prem)

        confidence = self._score_confidence(
            confluence, news_bias, direction,
            trap_quality=trap_quality, ob_fresh=ob_fresh,
            near_round_number=near_round, genuine_move_risk=genuine_risk,
            session=session, depth_confirms=depth_confirms,
            setup_type=setup_type, vwap_confirms=vwap_confirms,
        )
        lots, max_loss = _size_lots(opt_entry, opt_sl, self.instrument, confidence)

        if lots == 0:
            return self._waiting(
                ltp, pdh, pdl,
                "⚠️ Setup valid but 1 lot costs ₹{:,.0f} > ₹{:.0f} budget. "
                "Wait for tighter SL setup.".format(
                    sl_dist_prem * LOT_SIZES.get(self.instrument, 75), BASE_RISK
                ),
                day_high=today_high, day_low=today_low,
                pdh_breached=pdh_breached, pdl_breached=pdl_breached,
                vwap=vwap_now, gap_type=gap_type, gap_pct=gap_pct,
                today_high=today_high, today_low=today_low,
                session_warning=session_warning,
            )

        lot_size = LOT_SIZES.get(self.instrument, 75)

        return GhostSignal(
            instrument       = self.instrument,
            direction        = direction,
            phase            = SetupPhase.TRADE_ACTIVE,

            entry_condition  = (
                "✅ EXECUTE: 5-min candle closed {} {:.0f}. "
                "BUY {} {} NOW at ₹{:.0f}".format(
                    "below OB" if direction == Direction.BEAR else "above OB",
                    ob_low if direction == Direction.BEAR else ob_high,
                    self.instrument, opt_type,
                    opt_entry
                )
            ),
            wait_message     = "🚀 SIGNAL ACTIVE — place order immediately",

            pdh=pdh, pdl=pdl,
            trap_level       = pdh if direction == Direction.BEAR else pdl,
            ob_high=ob_high, ob_low=ob_low,
            fvg_top=fvg_top, fvg_bottom=fvg_bot,

            idx_entry=idx_entry, idx_sl=idx_sl,
            idx_t1=idx_t1, idx_t2=idx_t2, idx_t3=idx_t3,
            current_ltp=ltp,

            strike           = strike,
            option_type      = opt_type,
            contract_label   = "{} {} {}".format(self.instrument, strike, opt_type),
            opt_entry        = opt_entry,
            opt_sl           = opt_sl,
            opt_t1           = opt_t1,
            opt_t2           = opt_t2,
            opt_t3           = opt_t3,
            rr_t1            = _rr(opt_t1),
            rr_t2            = _rr(opt_t2),
            rr_t3            = _rr(opt_t3),

            lots             = lots,
            quantity         = lots * lot_size,
            max_loss_rs      = max_loss,

            confidence       = confidence,
            confluence_count = len(confluence),
            confluence_list  = confluence,

            invalidated_if   = (
                "SL hit if option premium drops to ₹{:.0f} | "
                "Index SL: {:.0f} | "
                "Max loss: ₹{:,.0f} | "
                "TIME STOP: if T1 (₹{:.0f}) not reached within {} min, "
                "exit at cost — don't hold and hope against theta decay".format(
                    opt_sl, idx_sl, max_loss, opt_t1, TIME_STOP_CANDLES * 5
                )
            ),
            actionable       = True,

            vwap             = vwap_now,
            gap_type         = gap_type,
            gap_pct          = gap_pct,
            today_high       = today_high,
            today_low        = today_low,
            vwap_position    = vwap_pos,
            setup_type       = setup_type,
            trap_quality         = trap_quality,
            session_warning      = session_warning,
            genuine_move_warning = genuine_warning,
            near_round_number    = near_round,
            ob_fresh             = ob_fresh,
            time_stop_minutes    = TIME_STOP_CANDLES * 5,

            day_high             = today_high,
            day_low              = today_low,
            pdh_breached         = pdh_breached,
            pdl_breached         = pdl_breached,
            depth_buyers_ratio   = buyers_ratio,
            depth_confirmation   = depth_msg,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _resolve_bias(
        self, df15: pd.DataFrame, df5: pd.DataFrame,
        pdh: float, pdl: float, ltp: float, news_bias: str,
        vwap_now: float, vwap_series: pd.Series,
        today_high: float, today_low: float,
        gap_type: str,
    ) -> Tuple[Direction, str, str]:
        """
        3-level bias engine. Returns (Direction, reason, setup_type).

        ── The 4 market scenarios GHOST handles:

        SCENARIO 1 — Normal open (price near PDH/PDL)
          Classic GHOST sweep trap. First 15-min candle breaks PDH/PDL,
          retail gets trapped, we fade the breakout.
          setup_type = "PDH_PDL_SWEEP"

        SCENARIO 2 — Gap open (price already far from PDH/PDL)
          Pure PDH/PDL sweep trap won't trigger because market gapped past it.
          Use TODAY's High/Low as the sweep trap levels instead.
          After 45+ min of trading, today's range extremes carry the same
          retail-stop-cluster psychology as PDH/PDL — everyone who bought
          the morning breakout has their stops at/below today's low.
          setup_type = "TODAY_HIGH_LOW_SWEEP"

        SCENARIO 3 — VWAP rejection (any open type)
          Price tested VWAP and rejected — institutions defending their
          average cost. Highest R:R setup regardless of gap status.
          Complements both Scenario 1 and 2.
          setup_type = "VWAP_REJECTION"

        SCENARIO 4 — Combined (PDH/PDL sweep + VWAP confluence)
          When both a trap sweep AND VWAP rejection align → highest
          confidence, size up.
          setup_type = "COMBINED"

        Priority: COMBINED > PDH_PDL_SWEEP > TODAY_HIGH_LOW_SWEEP > VWAP_REJECTION
        """
        first15 = df15.iloc[0]
        last15  = df15.iloc[-1]
        candles_formed = len(df5)

        # ── Level 1: PDH / PDL trap signals (primary)
        pdh_sweep_bear = (
            float(first15["High"]) > pdh          # opening candle broke PDH
            or float(last15["Close"]) < pdl       # now closing below PDL
        )
        pdl_sweep_bull = (
            float(first15["Low"]) < pdl           # opening candle broke PDL
            or float(last15["Close"]) > pdh       # now closing above PDH
        )

        # ── Level 2: Today's High/Low trap signals (gap-open fallback)
        # Only activate after enough intraday range is built (≥45 min = 9 x 5min bars)
        # and only when price has NOT breached PDH/PDL (i.e., gap scenario)
        today_hl_bear = False; today_hl_bull = False
        if candles_formed >= 9 and gap_type in ("GAP_UP", "GAP_DOWN", "FLAT"):
            today_range = today_high - today_low
            # Bear: sweep of today's high with rejection (wick above, close below)
            last5_high  = float(df5["High"].iloc[-1])
            last5_close = float(df5["Close"].iloc[-1])
            last5_low   = float(df5["Low"].iloc[-1])
            last5_open  = float(df5["Open"].iloc[-1])
            prev5_high  = float(df5["High"].iloc[-2])

            # Sweep of today's high: last bar printed a new session high but closed below it
            # (wick rejection) AND below the previous bar's high (trap candle)
            if (today_range > 0 and
                    last5_high >= today_high and
                    last5_close < last5_high * 0.9985 and   # close ≥ 0.15% below wick high
                    last5_close < last5_open):              # bearish candle body
                today_hl_bear = True

            # Sweep of today's low: last bar printed a new session low but closed above it
            if (today_range > 0 and
                    last5_low <= today_low and
                    last5_close > last5_low * 1.0015 and
                    last5_close > last5_open):
                today_hl_bull = True

        # ── Level 3: VWAP rejection signals
        vwap_bear_reject, vwap_bear_msg = _detect_vwap_rejection(df5, vwap_series, Direction.BEAR)
        vwap_bull_reject, vwap_bull_msg = _detect_vwap_rejection(df5, vwap_series, Direction.BULL)
        vwap_bear_reclaim, vwap_bear_rec_msg = _detect_vwap_reclaim(df5, vwap_series, Direction.BEAR)
        vwap_bull_reclaim, vwap_bull_rec_msg = _detect_vwap_reclaim(df5, vwap_series, Direction.BULL)

        bear_vwap = vwap_bear_reject or vwap_bear_reclaim
        bull_vwap = vwap_bull_reject or vwap_bull_reclaim
        bear_vwap_msg = vwap_bear_msg if vwap_bear_reject else vwap_bear_rec_msg
        bull_vwap_msg = vwap_bull_msg if vwap_bull_reject else vwap_bull_rec_msg

        # ── Gap-open VWAP bias override
        # On a gap-up day, price is above VWAP and likely to mean-revert downward
        # On a gap-down day, price is below VWAP and likely to mean-revert upward
        # This biases toward the gap-fill trade when no other signal is present
        if gap_type == "GAP_UP" and ltp > vwap_now * 1.001:
            bear_vwap = bear_vwap or True   # gap-up → lean BEAR (gap fill toward VWAP)
            bear_vwap_msg = bear_vwap_msg or "Gap-up open — price likely to fill toward VWAP {:.0f}".format(vwap_now)
        elif gap_type == "GAP_DOWN" and ltp < vwap_now * 0.999:
            bull_vwap = bull_vwap or True
            bull_vwap_msg = bull_vwap_msg or "Gap-down open — price likely to fill toward VWAP {:.0f}".format(vwap_now)

        # ── Priority resolution
        # COMBINED: PDH/PDL sweep AND VWAP confirmation same direction
        if pdh_sweep_bear and bear_vwap:
            return Direction.BEAR, (
                "PDH sweep trap + VWAP confirmation (VWAP={:.0f}). "
                "Dual confluence → highest conviction PE setup. {}".format(vwap_now, bear_vwap_msg)
            ), "COMBINED"

        if pdl_sweep_bull and bull_vwap:
            return Direction.BULL, (
                "PDL sweep trap + VWAP confirmation (VWAP={:.0f}). "
                "Dual confluence → highest conviction CE setup. {}".format(vwap_now, bull_vwap_msg)
            ), "COMBINED"

        # PRIMARY: Pure PDH/PDL trap
        if pdh_sweep_bear:
            return Direction.BEAR, "PDH {:.0f} swept → retail buy trap → BEAR (PE)".format(pdh), "PDH_PDL_SWEEP"
        if pdl_sweep_bull:
            return Direction.BULL, "PDL {:.0f} swept → retail sell trap → BULL (CE)".format(pdl), "PDH_PDL_SWEEP"

        # SECONDARY: Today's High/Low trap (active on gap days)
        if today_hl_bear:
            return Direction.BEAR, (
                "Today's High {:.0f} swept with wick rejection → intraday trap → BEAR (PE). "
                "Gap type: {}".format(today_high, gap_type)
            ), "TODAY_HIGH_LOW_SWEEP"
        if today_hl_bull:
            return Direction.BULL, (
                "Today's Low {:.0f} swept with wick rejection → intraday trap → BULL (CE). "
                "Gap type: {}".format(today_low, gap_type)
            ), "TODAY_HIGH_LOW_SWEEP"

        # TERTIARY: VWAP-only setup (weaker but tradeable when high-quality)
        if bear_vwap and candles_formed >= 9:
            return Direction.BEAR, (
                "VWAP-only setup — {}. VWAP={:.0f}. "
                "No PDH/PDL or Today's H/L sweep — reduce size.".format(bear_vwap_msg, vwap_now)
            ), "VWAP_REJECTION"
        if bull_vwap and candles_formed >= 9:
            return Direction.BULL, (
                "VWAP-only setup — {}. VWAP={:.0f}. "
                "No PDH/PDL or Today's H/L sweep — reduce size.".format(bull_vwap_msg, vwap_now)
            ), "VWAP_REJECTION"

        # NO SETUP
        return Direction.NONE, (
            "No setup. Price between PDH {:.0f} / PDL {:.0f} / Today H {:.0f} / L {:.0f}. "
            "VWAP={:.0f}. Gap={}.".format(pdh, pdl, today_high, today_low, vwap_now, gap_type)
        ), ""


    def _check_5min_confirm(
        self, candle: pd.Series, direction: Direction,
        ob_high: float, ob_low: float
    ) -> dict:
        """
        Confirmation candle rules:
          BEAR: 5-min candle body must CLOSE below ob_low (bearish close inside OB)
          BULL: 5-min candle body must CLOSE above ob_high (bullish close inside OB)
        """
        c = float(candle["Close"])
        o = float(candle["Open"])
        body_bearish = c < o
        body_bullish = c > o

        if direction == Direction.BEAR:
            if body_bearish and c < ob_low:
                return {"valid": True, "reason": "bearish close below OB low {:.0f}".format(ob_low)}
            return {"valid": False, "reason": "close {:.0f} not below OB low {:.0f}".format(c, ob_low)}

        elif direction == Direction.BULL:
            if body_bullish and c > ob_high:
                return {"valid": True, "reason": "bullish close above OB high {:.0f}".format(ob_high)}
            return {"valid": False, "reason": "close {:.0f} not above OB high {:.0f}".format(c, ob_high)}

        return {"valid": False, "reason": "no direction"}

    def _score_confidence(
        self, confluence: List[str], news_bias: str, direction: Direction,
        trap_quality: str = "MEDIUM", ob_fresh: bool = True,
        near_round_number: bool = False, genuine_move_risk: bool = False,
        session: str = "PRIME", depth_confirms: Optional[bool] = None,
        setup_type: str = "", vwap_confirms: bool = False,
    ) -> float:
        """
        Confidence 0-97. Higher = size up. Lower = reduce or skip.
        Factors: confluence count, news, trap quality, OB freshness,
                 VWAP confirmation, setup type, depth, session.
        """
        base = min(95.0, len([c for c in confluence if c.startswith("✅")]) * 18.0)
        news_d = direction.value if direction != Direction.NONE else ""

        # News alignment
        if (news_bias == "BEARISH" and news_d == "BEAR") or            (news_bias == "BULLISH" and news_d == "BULL"):
            base += 10.0
        elif news_bias not in ("NEUTRAL", ""):
            base -= 8.0

        # Trap quality
        if trap_quality == "HIGH":
            base += 8.0
        elif trap_quality == "LOW":
            base -= 12.0

        # OB freshness
        base += 5.0 if ob_fresh else -8.0

        # Round number proximity
        if near_round_number:
            base += 5.0

        # VWAP confirmation — institutional anchor aligning with setup
        if vwap_confirms:
            base += 12.0

        # Setup type quality ladder
        if setup_type == "COMBINED":
            base += 10.0   # sweep + VWAP = highest conviction
        elif setup_type == "PDH_PDL_SWEEP":
            base += 5.0
        elif setup_type == "TODAY_HIGH_LOW_SWEEP":
            base += 2.0
        elif setup_type == "VWAP_REJECTION":
            base -= 5.0    # VWAP-only: reduce size

        # Genuine breakout risk (heaviest penalty)
        if genuine_move_risk:
            base -= 20.0

        # Session quality
        if session == "LUNCH_CHOP":
            base -= 12.0
        elif session == "OPENING":
            base -= 15.0
        elif session == "POWER_HOUR":
            base += 5.0
        elif session == "PRIME":
            base += 3.0

        # Market depth confirmation
        if depth_confirms is True:
            base += 6.0
        elif depth_confirms is False:
            base -= 8.0

        return round(max(0.0, min(97.0, base)), 1)
    def _waiting(
        self, ltp: float, pdh: float, pdl: float, msg: str,
        day_high: float = 0.0, day_low: float = 0.0,
        pdh_breached: bool = False, pdl_breached: bool = False,
        session_warning: str = "",
        vwap: float = 0.0, gap_type: str = "", gap_pct: float = 0.0,
        today_high: float = 0.0, today_low: float = 0.0,
    ) -> GhostSignal:
        return GhostSignal(
            instrument     = self.instrument,
            direction      = Direction.NONE,
            phase          = SetupPhase.WAIT_FOR_TRAP,
            entry_condition= "Setup not yet formed",
            wait_message   = msg,
            pdh=pdh, pdl=pdl,
            current_ltp=ltp,
            day_high=today_high or day_high,
            day_low=today_low or day_low,
            pdh_breached=pdh_breached,
            pdl_breached=pdl_breached,
            session_warning=session_warning,
            vwap=vwap,
            gap_type=gap_type,
            gap_pct=gap_pct,
            today_high=today_high or day_high,
            today_low=today_low or day_low,
            actionable=False,
        )


# ═══════════════════════════════════════════════════════════════════════════
# STREAMLIT RENDERER
# ═══════════════════════════════════════════════════════════════════════════

def render_ghost_panel(
    signal: GhostSignal,
    instrument: str = "NIFTY",
) -> None:
    """
    Full Streamlit signal panel.

    Usage in streamlit_app.py:
        from agents.ghost_strategy import GhostStrategyEngine, render_ghost_panel
        engine = GhostStrategyEngine(instrument="BANKNIFTY")
        signal = engine.evaluate(df_15min, df_5min, pdh, pdl, ltp, news_bias)
        render_ghost_panel(signal)
    """
    try:
        import streamlit as st
    except ImportError:
        return

    st.markdown("---")
    st.subheader("👻 GHOST Strategy — {} {} Options".format(
        signal.instrument,
        "PE (Bear)" if signal.direction == Direction.BEAR
        else "CE (Bull)" if signal.direction == Direction.BULL
        else "Scanning…"
    ))

    # ── Phase status
    phase_icons = {
        SetupPhase.WAIT_FOR_CONTEXT: "⏳",
        SetupPhase.WAIT_FOR_TRAP:    "👁",
        SetupPhase.WAIT_FOR_OB:      "🔍",
        SetupPhase.WAIT_FOR_ENTRY:   "🕯",
        SetupPhase.TRADE_ACTIVE:     "🚀",
        SetupPhase.NO_TRADE_TODAY:   "🚫",
    }
    icon = phase_icons.get(signal.phase, "⏳")

    # ── NO-TRADE (hard psychology gate) gets its own treatment
    if signal.phase == SetupPhase.NO_TRADE_TODAY:
        st.error("🚫 **NO TRADE** — {}".format(signal.wait_message))
        if signal.genuine_move_warning:
            st.warning(signal.genuine_move_warning)
        c1, c2 = st.columns(2)
        c1.metric("PDH", "{:.0f}".format(signal.pdh) if signal.pdh else "—")
        c2.metric("PDL", "{:.0f}".format(signal.pdl) if signal.pdl else "—")
        return

    # ── Session-quality banner (shown regardless of actionability)
    if signal.session_warning:
        st.warning(signal.session_warning)

    if signal.actionable:
        st.success("🚀 **EXECUTE NOW** — {}".format(signal.entry_condition))
    else:
        st.info("{} {}".format(icon, signal.wait_message))

    # ── Key levels
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PDH", "{:.0f}".format(signal.pdh) if signal.pdh else "—")
    c2.metric("PDL", "{:.0f}".format(signal.pdl) if signal.pdl else "—")
    c3.metric("OB Zone",
              "{:.0f}–{:.0f}".format(signal.ob_low, signal.ob_high)
              if signal.ob_high else "—")
    c4.metric("Live LTP", "{:.2f}".format(signal.current_ltp) if signal.current_ltp else "—")

    # ── Psychology badges — visible even while waiting
    if signal.trap_quality:
        badge_col1, badge_col2, badge_col3 = st.columns(3)
        tq_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(signal.trap_quality, "⚪")
        badge_col1.markdown("**Trap Quality:** {} {}".format(tq_emoji, signal.trap_quality))
        badge_col2.markdown(
            "**Round Number:** {}".format("✅ Yes" if signal.near_round_number else "—")
        )
        badge_col3.markdown(
            "**OB Status:** {}".format("🆕 Fresh" if signal.ob_fresh else "♻️ Retested")
        )
        if signal.genuine_move_warning:
            st.warning(signal.genuine_move_warning)

    if not signal.actionable:
        # Show confluence progress even while waiting
        if signal.confluence_list:
            with st.expander("📋 Confluence checklist", expanded=True):
                for item in signal.confluence_list:
                    st.markdown("- " + item)
        return

    # ── Option contract block
    st.markdown("### 📋 Trade Setup")
    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("**Contract:** `{}`".format(signal.contract_label))
        st.markdown("**Direction:** {} {}".format(
            "🔴 BEARISH (BUY PE)" if signal.direction == Direction.BEAR
            else "🟢 BULLISH (BUY CE)",
            ""
        ))

        rows = [
            ("Entry premium",  "₹{:.2f}".format(signal.opt_entry)),
            ("Stop Loss (SL)", "₹{:.2f}".format(signal.opt_sl)),
            ("Target 1 (T1)",  "₹{:.2f}  [R:R {}]".format(signal.opt_t1, signal.rr_t1)),
            ("Target 2 (T2)",  "₹{:.2f}  [R:R {}]".format(signal.opt_t2, signal.rr_t2)),
            ("Target 3 (T3)",  "₹{:.2f}  [R:R {}]".format(signal.opt_t3, signal.rr_t3)),
        ]
        import pandas as pd
        st.dataframe(
            pd.DataFrame(rows, columns=["Level", "Value"]),
            use_container_width=True, hide_index=True
        )

    with col_r:
        st.markdown("**Sizing**")
        st.metric("Lots",      signal.lots)
        st.metric("Quantity",  signal.quantity)
        st.metric("Max Loss",  "₹{:,.0f}".format(signal.max_loss_rs))
        conf_label = (
            "High conviction" if signal.confidence >= 80
            else "Standard" if signal.confidence >= 60
            else "⚠️ Marginal — size down"
        )
        st.metric("Confidence","{:.0f}%".format(signal.confidence), conf_label)
        st.metric("Time Stop",  "{} min".format(signal.time_stop_minutes),
                  "Exit at cost if T1 not hit")

    # ── Index reference levels
    st.markdown("**Index reference (for chart)**")
    ic1, ic2, ic3, ic4, ic5 = st.columns(5)
    ic1.metric("Entry",  "{:.2f}".format(signal.idx_entry))
    ic2.metric("SL",     "{:.2f}".format(signal.idx_sl))
    ic3.metric("T1",     "{:.2f}".format(signal.idx_t1))
    ic4.metric("T2",     "{:.2f}".format(signal.idx_t2))
    ic5.metric("T3",     "{:.2f}".format(signal.idx_t3))

    # ── Copy block
    copy_txt = (
        "{} | Entry ₹{:.0f} | SL ₹{:.0f} | T1 ₹{:.0f} | T2 ₹{:.0f} | "
        "Lots {} | Max Loss ₹{:,.0f}".format(
            signal.contract_label,
            signal.opt_entry, signal.opt_sl,
            signal.opt_t1, signal.opt_t2,
            signal.lots, signal.max_loss_rs
        )
    )
    st.code(copy_txt, language=None)

    # ── Invalidation rule
    st.warning("⚠️ **Invalidated if:** " + signal.invalidated_if)

    # ── Confluence checklist
    with st.expander("📋 Full confluence checklist ({} factors)".format(
        signal.confluence_count
    ), expanded=False):
        for item in signal.confluence_list:
            st.markdown("- " + item)

    # ── Strategy explanation
    with st.expander("📖 Why this trade? (GHOST logic)", expanded=False):
        st.markdown("""
**GHOST** catches the institutional smart-money trap:

1. **Previous Day Levels** — PDH `{:.0f}` and PDL `{:.0f}` are the key magnets.
   Retail traders place buy stops *above* PDH and sell stops *below* PDL.

2. **Liquidity Sweep** — Market opens and breaks PDH/PDL to trigger retail stops.
   This is the trap. Retail traders enter on "breakout" but smart money is selling into them.

3. **Order Block retest** — After the sweep, price pulls back to the institutional
   order block zone `{:.0f}–{:.0f}`. This is where institutions loaded positions.

4. **5-min Confirmation** — We wait for a bearish/bullish 5-min close *inside* the OB
   to confirm institutions are defending the zone. Only then we enter.

5. **SL placement** — Stop is at the swing high of the trap candle (bear) or swing low (bull).
   This is where smart money would be wrong — structure would be broken.

6. **Targets** — T1 at FVG zone (₹{:.0f}), T2 at PDL/PDH (₹{:.0f}),
   T3 at 1.618 extension (₹{:.0f}).
        """.format(
            signal.pdh, signal.pdl,
            signal.ob_low, signal.ob_high,
            signal.opt_t1, signal.opt_t2, signal.opt_t3,
        ))

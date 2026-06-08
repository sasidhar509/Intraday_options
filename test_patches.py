"""
test_patches.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Validates all three patches against deterministic mock data.
No Streamlit, no network calls, no live API required.

Run:  python test_patches.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys

# Allow running from repo root OR from patch directory
sys.path.insert(0, os.path.dirname(__file__))

from agents.brain import StrategyBrainEngine
from news_api_integration import AsyncGuruNewsEngine

brain = StrategyBrainEngine()
PASS  = "\033[92mPASS\033[0m"
FAIL  = "\033[91mFAIL\033[0m"
results = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    tag    = f"[{label}]"
    print(f"  {tag:<45} {status}  {detail}")
    results.append(condition)


print("\n" + "═" * 70)
print("  PATCH TEST HARNESS — Intraday Options Trading System")
print("═" * 70)

# ─────────────────────────────────────────────────────────────
# PATCH 1 — Risk Shield
# ─────────────────────────────────────────────────────────────
print("\n[PATCH 1] Risk Shield — generate_position_sizing()")
print("-" * 70)

# 1a. 100% conviction, NIFTY, 20-point SL → 2× budget = ₹1000
#     1 lot = 75 units × 20 pts = ₹1500 per lot → 0 lots (exceeds budget)
r = brain.generate_position_sizing(
    probability_score=100, risk_per_point=20, symbol="NIFTY"
)
check(
    "100% prob, 20pt SL → 0 lots (budget ₹1000 < ₹1500/lot)",
    r["lots"] == 0 and r["status"] == "NO_TRADE",
    f"lots={r['lots']} status={r['status']}",
)

# 1b. 100% conviction, NIFTY, 5-point SL → budget ₹1000 / (5×75=₹375) = 2 lots
r = brain.generate_position_sizing(
    probability_score=100, risk_per_point=5, symbol="NIFTY"
)
check(
    "100% prob, 5pt SL → 2 lots (₹1000/₹375=2.66 → floor 2)",
    r["lots"] == 2 and r["status"] == "EXECUTE_TRADE",
    f"lots={r['lots']} max_loss=₹{r['max_loss_rupees']}",
)
check(
    "Max loss ≤ capital budget",
    r["max_loss_rupees"] <= r["capital_budget"],
    f"max_loss=₹{r['max_loss_rupees']} budget=₹{r['capital_budget']}",
)

# 1c. 75% conviction, BANKNIFTY lot=35, 8pt SL → budget ₹500 / (8×35=₹280) = 1 lot
r = brain.generate_position_sizing(
    probability_score=75, risk_per_point=8, symbol="BANKNIFTY"
)
check(
    "75% prob BANKNIFTY 8pt SL → 1 lot (₹500/₹280=1.78 → 1)",
    r["lots"] == 1 and r["status"] == "EXECUTE_TRADE",
    f"lots={r['lots']} qty={r['quantity']} lot_size={r['lot_size']}",
)
check(
    "Quantity = lots × lot_size",
    r["quantity"] == r["lots"] * r["lot_size"],
    f"qty={r['quantity']} lots={r['lots']} lot_size={r['lot_size']}",
)

# 1d. Below threshold → NO_TRADE
r = brain.generate_position_sizing(
    probability_score=60, risk_per_point=5, symbol="NIFTY"
)
check(
    "60% prob (< 75% threshold) → NO_TRADE",
    r["status"] == "NO_TRADE" and r["lots"] == 0,
    f"status={r['status']}",
)

# 1e. SENSEX lot=10, 15pt SL, 85% → budget ₹750 / (15×10=₹150) = 5 lots
r = brain.generate_position_sizing(
    probability_score=85, risk_per_point=15, symbol="SENSEX"
)
check(
    "85% prob SENSEX 15pt SL → 5 lots (₹750/₹150=5.0)",
    r["lots"] == 5 and r["status"] == "EXECUTE_TRADE",
    f"lots={r['lots']} max_loss=₹{r['max_loss_rupees']}",
)

# 1f. risk_per_point = 0 → NO_TRADE guard
r = brain.generate_position_sizing(
    probability_score=100, risk_per_point=0, symbol="NIFTY"
)
check(
    "risk_per_point=0 → NO_TRADE (division guard)",
    r["status"] == "NO_TRADE",
    f"status={r['status']}",
)

# ─────────────────────────────────────────────────────────────
# PATCH 2 — Unified Signal Reconciler
# ─────────────────────────────────────────────────────────────
print("\n[PATCH 2] Unified Signal — reconcile_signals()")
print("-" * 70)

# 2a. Full bullish confluence: all layers + classic score aligned
r = brain.reconcile_signals(
    classic_score      = 5,
    classic_bias       = "Bullish",
    current_price      = 23500.0,
    ema_9              = 23450.0,    # price above EMA → bullish
    vwap               = 23440.0,   # price above VWAP → bullish
    buyers_ratio       = 0.68,      # >60% buyers → bullish
    raw_news_sentiment = 2,         # net positive → bullish news
    nifty_trend        = "BULLISH",
    risk_per_point     = 5,
    symbol             = "NIFTY",
)
check(
    "Full bullish: direction=BULLISH",
    r["direction"] == "BULLISH",
    f"direction={r['direction']}",
)
check(
    "Full bullish: matrix_probability=100",
    r["matrix_probability"] == 100,
    f"matrix_prob={r['matrix_probability']}",
)
# unified = 0.70×100 + 0.30×(5/5×100) = 70+30 = 100
check(
    "Full bullish: unified_probability=100.0",
    r["unified_probability"] == 100.0,
    f"unified={r['unified_probability']}",
)
check(
    "Full bullish: actionable=True",
    r["actionable"] is True,
    f"actionable={r['actionable']}",
)
check(
    "Full bullish: sizing returns lots > 0",
    r["sizing"]["lots"] > 0,
    f"lots={r['sizing']['lots']}",
)

# 2b. Chop zone → NEUTRAL direction → NO_TRADE
r = brain.reconcile_signals(
    classic_score      = 2,
    classic_bias       = "Bullish",
    current_price      = 23400.0,
    ema_9              = 23410.0,   # price below EMA
    vwap               = 23390.0,  # price above VWAP  → chop
    buyers_ratio       = 0.50,
    raw_news_sentiment = 0,
    nifty_trend        = "NEUTRAL",
    risk_per_point     = 10,
    symbol             = "NIFTY",
)
check(
    "Chop zone (price between EMA/VWAP) → direction=NEUTRAL",
    r["direction"] == "NEUTRAL",
    f"direction={r['direction']}",
)
check(
    "Chop zone → actionable=False",
    r["actionable"] is False,
    f"actionable={r['actionable']}",
)

# 2c. Classic score=1 (weak) → matrix direction dominates
r = brain.reconcile_signals(
    classic_score      = 1,
    classic_bias       = "Bullish",   # weak classic
    current_price      = 23300.0,
    ema_9              = 23350.0,     # price BELOW ema
    vwap               = 23340.0,    # price BELOW vwap → bearish matrix
    buyers_ratio       = 0.35,
    raw_news_sentiment = -3,
    nifty_trend        = "BEARISH",
    risk_per_point     = 8,
    symbol             = "NIFTY",
)
check(
    "Weak classic (score=1) → matrix direction wins (BEARISH)",
    r["direction"] == "BEARISH",
    f"direction={r['direction']}",
)

# 2d. Logs are non-empty and contain Layer 3 bridge entry
check(
    "Logs contain news bridge entry",
    any("News bridge" in log for log in r["logs"]),
    f"log count={len(r['logs'])}",
)

# ─────────────────────────────────────────────────────────────
# PATCH 3 — News Bridge
# ─────────────────────────────────────────────────────────────
print("\n[PATCH 3] News Bridge — news_score_from_sentiment()")
print("-" * 70)

cases = [
    # (raw, direction, expected, description)
    ( 3, "BULLISH",  25, "positive raw + BULLISH → +25"),
    (-2, "BEARISH", -25, "negative raw + BEARISH → -25"),
    ( 2, "BEARISH",   0, "positive raw + BEARISH → 0 (mismatch)"),
    (-1, "BULLISH",   0, "negative raw + BULLISH → 0 (mismatch)"),
    ( 0, "BULLISH",   0, "zero raw → 0"),
    ( 0, "BEARISH",   0, "zero raw → 0"),
]
for raw, direction, expected, desc in cases:
    got = brain.news_score_from_sentiment(raw, direction)
    check(f"news_score_from_sentiment({raw:+d}, {direction})", got == expected,
          f"expected={expected} got={got}  [{desc}]")

# Patch 3 — Aggregation
print("\n[PATCH 3] News Bridge — AsyncGuruNewsEngine.get_aggregated_sentiment()")
print("-" * 70)

mock_articles = [
    {"sentiment_score":  2},
    {"sentiment_score": -1},
    {"sentiment_score":  0},
    {"sentiment_score":  3},
    {"sentiment_score": -1},
    {"sentiment_score":  5},   # beyond top_n=5, should be ignored
]
agg = AsyncGuruNewsEngine.get_aggregated_sentiment(mock_articles, top_n=5)
check(
    "Aggregation top_n=5: 2+(-1)+0+3+(-1)=3",
    agg == 3,
    f"got={agg}",
)
agg0 = AsyncGuruNewsEngine.get_aggregated_sentiment([], top_n=5)
check(
    "Empty article list → 0",
    agg0 == 0,
    f"got={agg0}",
)
agg_all_bearish = AsyncGuruNewsEngine.get_aggregated_sentiment(
    [{"sentiment_score": -2}] * 5, top_n=5
)
check(
    "5 articles each -2 → -10",
    agg_all_bearish == -10,
    f"got={agg_all_bearish}",
)

# Missing key → treat as 0
agg_missing = AsyncGuruNewsEngine.get_aggregated_sentiment(
    [{"title": "No score key here"}] * 3, top_n=5
)
check(
    "Articles without sentiment_score key → 0",
    agg_missing == 0,
    f"got={agg_missing}",
)

# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
print("\n" + "═" * 70)
passed = sum(results)
total  = len(results)
colour = "\033[92m" if passed == total else "\033[91m"
print(f"  {colour}{passed}/{total} checks passed.\033[0m")
if passed == total:
    print("  \033[92mAll patches validated ✓\033[0m")
else:
    failed = [i + 1 for i, ok in enumerate(results) if not ok]
    print(f"  \033[91mFailed checks: {failed}\033[0m")
print("═" * 70 + "\n")

sys.exit(0 if passed == total else 1)

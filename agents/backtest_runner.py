"""
agents/backtest_runner.py  ── FIXED v2

FIX: _resolve_backtest_engine_path() was looking in Downloads folder.
     Now correctly imports from agents/backtest_engine.py which is
     already in the project at agents/backtest_engine.py.
"""

from __future__ import annotations
import os
import sys
from typing import Tuple, Any, Dict


def _resolve_backtest_engine_path() -> str:
    """
    Resolve path to backtest_engine.py.
    Priority:
      1. agents/backtest_engine.py (same package — correct location)
      2. Project root backtest_engine.py
    """
    # agents/ is one level up from this file
    agents_dir   = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(agents_dir)

    candidates = [
        os.path.join(agents_dir,   "backtest_engine.py"),   # agents/backtest_engine.py
        os.path.join(project_root, "backtest_engine.py"),   # root fallback
    ]

    for path in candidates:
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        "backtest_engine.py not found. Expected at: {}".format(candidates[0])
    )


def load_backtest_module() -> Any:
    """Load backtest_engine as a module regardless of import path."""
    path = _resolve_backtest_engine_path()

    # Add agents dir to sys.path so internal imports inside backtest_engine work
    agents_dir = os.path.dirname(path)
    if agents_dir not in sys.path:
        sys.path.insert(0, agents_dir)

    import importlib.util
    spec   = importlib.util.spec_from_file_location("backtest_engine", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_backtest_and_module(
    instruments=None,
    interval: str = "1day",
    start: str = "2024-06-01",
    end: str = "2025-06-01",
) -> Tuple[Any, Dict]:
    """
    Load backtest_engine and run run_backtest().
    Returns (module, results_dict).
    """
    module  = load_backtest_module()
    results = module.run_backtest(
        instruments=instruments or ["NIFTY", "BANKNIFTY"],
        interval=interval,
        start=start,
        end=end,
        kite=None,
        verbose=False,
    )
    return module, results

import os
import importlib.util
from typing import Tuple, Any, Dict


def _resolve_backtest_engine_path() -> str:
    # Path relative to this file: go up 4 levels then into Downloads
    base = os.path.dirname(__file__)
    candidate = os.path.abspath(os.path.join(base, '..', '..', '..', '..', 'Downloads', 'backtest_engine.py'))
    if os.path.exists(candidate):
        return candidate
    # fallback: try user Downloads in home directory
    home_dl = os.path.join(os.path.expanduser('C:\\Users\\sasid'), 'Downloads', 'backtest_engine.py')
    if os.path.exists(home_dl):
        return home_dl
    raise FileNotFoundError(f"backtest_engine.py not found at {candidate} or {home_dl}")


def load_backtest_module() -> Any:
    path = _resolve_backtest_engine_path()
    spec = importlib.util.spec_from_file_location('external_backtest_engine', path)
    module = importlib.util.module_from_spec(spec)
    loader = spec.loader
    assert loader is not None
    loader.exec_module(module)
    return module


def run_backtest_and_module(instruments=None, interval='15min', start='2024-06-01', end='2025-06-01') -> Tuple[Any, Dict]:
    """Load the external backtest_engine.py and run run_backtest.

    Returns (module, results_dict)
    """
    module = load_backtest_module()
    results = module.run_backtest(instruments=instruments, interval=interval, start=start, end=end, kite=None, verbose=False)
    return module, results

"""
tuner.py — Runner Optimasi Parameter
Mencari kombinasi parameter terbaik. WAJIB out-of-sample validation.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass
class OptConfig:
    n_trials: int = 100
    n_jobs: int = -1  # parallel (-1 = semua core)
    objective: str = "sharpe"  # sharpe | sortino | calmar | profit_factor
    min_trades: int = 30
    timeout_seconds: int = 3600
    sampler: str = "tpe"  # tpe | random | cma-es


@dataclass
class OptResult:
    best_params: dict
    best_score: float
    n_trials_completed: int
    all_trials: list[dict]
    objective: str


def run_optimization(
    param_space: dict[str, Any],
    objective_fn: Callable[[dict], float],
    config: OptConfig | None = None,
    method: str = "bayesian",
) -> OptResult:
    """
    Jalankan optimasi parameter.

    Args:
        param_space: dict parameter → range/choices
        objective_fn: callable(params) -> score (higher is better)
        config: konfigurasi optimasi
        method: "bayesian" | "grid"
    """
    cfg = config or OptConfig()

    if method == "grid":
        return _run_grid_search(param_space, objective_fn, cfg)
    else:
        return _run_bayesian(param_space, objective_fn, cfg)


def _run_bayesian(
    param_space: dict[str, Any],
    objective_fn: Callable[[dict], float],
    config: OptConfig,
) -> OptResult:
    """Delegasi ke bayesian_opt.py."""
    from research.optimization.bayesian_opt import run_bayesian_optimization

    return run_bayesian_optimization(param_space, objective_fn, config)


def _run_grid_search(
    param_space: dict[str, Any],
    objective_fn: Callable[[dict], float],
    config: OptConfig,
) -> OptResult:
    """Grid search sederhana untuk parameter sedikit (<3)."""
    import itertools

    # Build grid
    keys = list(param_space.keys())
    values = list(param_space.values())

    # Pastikan semua values adalah list/tuple
    grid_values = []
    for v in values:
        if isinstance(v, (list, tuple)):
            grid_values.append(v)
        elif isinstance(v, dict) and "low" in v and "high" in v:
            # Range → sample 10 titik
            import numpy as np

            grid_values.append(np.linspace(v["low"], v["high"], min(10, config.n_trials)).tolist())
        else:
            grid_values.append([v])

    combinations = list(itertools.product(*grid_values))
    log.info(f"Grid search: {len(combinations)} kombinasi")

    best_score = float("-inf")
    best_params: dict = {}
    all_trials: list[dict] = []

    for i, combo in enumerate(combinations):
        params = dict(zip(keys, combo))
        try:
            score = objective_fn(params)
        except Exception as e:
            log.warning(f"Trial {i+1} gagal: {e}")
            score = float("-inf")

        all_trials.append({"params": params, "score": score})

        if score > best_score:
            best_score = score
            best_params = params

        if (i + 1) % 10 == 0:
            log.info(f"Grid search {i+1}/{len(combinations)}: best={best_score:.4f}")

    return OptResult(
        best_params=best_params,
        best_score=best_score,
        n_trials_completed=len(combinations),
        all_trials=all_trials,
        objective=config.objective,
    )


def save_best_params(
    params: dict,
    symbol: str,
    strategy: str,
    output_dir: str = "research/results",
) -> Path:
    """Simpan best_params.json."""
    os.makedirs(output_dir, exist_ok=True)
    filename = f"best_params_{symbol}_{strategy}.json"
    path = Path(output_dir) / filename

    with open(path, "w") as f:
        json.dump({"symbol": symbol, "strategy": strategy, "params": params}, f, indent=2)

    log.info(f"Best params saved: {path}")
    return path


def load_best_params(
    symbol: str,
    strategy: str,
    output_dir: str = "research/results",
) -> dict:
    """Load best_params.json."""
    filename = f"best_params_{symbol}_{strategy}.json"
    path = Path(output_dir) / filename

    if not path.exists():
        raise FileNotFoundError(f"Best params tidak ditemukan: {path}")

    with open(path) as f:
        data = json.load(f)
    return data.get("params", {})

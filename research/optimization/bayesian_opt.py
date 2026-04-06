"""
bayesian_opt.py — Bayesian Optimization via Optuna
Efisien untuk parameter banyak (>3) dan range besar.
Menggunakan TPE sampler (default Optuna).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger(__name__)


def run_bayesian_optimization(
    param_space: dict[str, Any],
    objective_fn: Callable[[dict], float],
    config: Any,
) -> Any:
    """
    Jalankan Bayesian optimization via Optuna.

    param_space format:
    {
        "learning_rate": {"type": "float", "low": 0.01, "high": 0.3, "log": True},
        "num_leaves":    {"type": "int",   "low": 15,   "high": 63},
        "subsample":     {"type": "float", "low": 0.5,  "high": 1.0},
        "model_type":    {"type": "categorical", "choices": ["lightgbm", "xgboost"]},
    }
    """
    try:
        import optuna
    except ImportError:
        raise ImportError("Install optuna: pip install optuna")

    # Suppress Optuna verbosity
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Sampler selection
    sampler_map = {
        "tpe": optuna.samplers.TPESampler(seed=42),
        "random": optuna.samplers.RandomSampler(seed=42),
    }
    # CMA-ES hanya untuk continuous params
    if config.sampler == "cma-es":
        try:
            sampler = optuna.samplers.CmaEsSampler(seed=42)
        except Exception:
            sampler = sampler_map["tpe"]
    else:
        sampler = sampler_map.get(config.sampler, sampler_map["tpe"])

    def _objective(trial: optuna.Trial) -> float:
        """Convert param_space ke Optuna trial params, lalu panggil objective_fn."""
        params: dict[str, Any] = {}
        for name, spec in param_space.items():
            if isinstance(spec, dict):
                ptype = spec.get("type", "float")
                if ptype == "float":
                    params[name] = trial.suggest_float(
                        name, spec["low"], spec["high"],
                        log=spec.get("log", False),
                    )
                elif ptype == "int":
                    params[name] = trial.suggest_int(
                        name, spec["low"], spec["high"],
                    )
                elif ptype == "categorical":
                    params[name] = trial.suggest_categorical(
                        name, spec["choices"],
                    )
                else:
                    params[name] = spec.get("default", 0)
            elif isinstance(spec, (list, tuple)):
                params[name] = trial.suggest_categorical(name, spec)
            else:
                params[name] = spec

        try:
            score = objective_fn(params)
        except Exception as e:
            log.warning(f"Trial {trial.number} gagal: {e}")
            return float("-inf")

        return score

    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
        study_name="crypto_ai_opt",
    )

    study.optimize(
        _objective,
        n_trials=config.n_trials,
        timeout=config.timeout_seconds,
        n_jobs=1,  # sequential untuk reproducibility
        show_progress_bar=False,
    )

    # Collect results
    from research.optimization.tuner import OptResult

    all_trials = []
    for trial in study.trials:
        all_trials.append({
            "params": trial.params,
            "score": trial.value if trial.value is not None else float("-inf"),
            "state": str(trial.state),
        })

    best = study.best_trial
    log.info(
        f"Bayesian optimization selesai: {len(study.trials)} trials. "
        f"Best score={best.value:.4f}, params={best.params}"
    )

    return OptResult(
        best_params=best.params,
        best_score=best.value if best.value is not None else 0.0,
        n_trials_completed=len(study.trials),
        all_trials=all_trials,
        objective=config.objective,
    )

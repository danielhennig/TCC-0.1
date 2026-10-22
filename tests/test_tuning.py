"""Ajuste na validação e sensibilidade a hiperparâmetros (dados sintéticos pequenos)."""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pandas as pd

from evaluation.sensitivity import hyperparameter_sensitivity
from evaluation.tuning import ModelConfig, grid_params, load_model_config, tune
from sacla.features import FEATURE_COLUMNS


def synthetic(n: int, anomalies: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.poisson(3, size=(n, len(FEATURE_COLUMNS))).astype(float), columns=list(FEATURE_COLUMNS)
    )
    frame["label"] = 0
    frame.loc[: anomalies - 1, "n_commands"] = 300.0
    frame.loc[: anomalies - 1, "label"] = 1
    return frame


def small_config() -> ModelConfig:
    config = load_model_config()
    grid = {
        "n_estimators": [20, 40],
        "max_samples": [64],
        "max_features": [1.0],
        "log1p": [True, False],
    }
    return replace(config, grid=grid, tuning_seeds=(0, 1))


def test_grid_is_the_full_cartesian_product_of_the_project_config() -> None:
    config = load_model_config()
    expected = math.prod(len(values) for values in config.grid.values())
    assert len(grid_params(config)) == expected == len({p.slug() for p in grid_params(config)})


def test_tune_reports_every_config_and_seed_and_picks_the_best_mean_f1() -> None:
    config = small_config()
    runs, summary, best = tune(synthetic(300, 0, 1), synthetic(200, 10, 2), config)
    assert len(runs) == len(grid_params(config)) * len(config.tuning_seeds)
    assert summary.iloc[0]["f1_mean"] == summary["f1_mean"].max()
    assert best.slug() in set(summary["config"])
    chosen = summary.set_index("config").loc[best.slug(), "f1_mean"]
    assert chosen == summary["f1_mean"].max()


def test_sensitivity_with_mixed_integer_and_fraction_max_samples() -> None:
    # Regressão: 256 e 0.5 na mesma coluna viravam float e a comparação com o escolhido falhava.
    config = replace(
        small_config(),
        grid={
            "n_estimators": [20, 40],
            "max_samples": [64, 0.5],
            "max_features": [1.0],
            "log1p": [True],
        },
    )
    _, summary, best = tune(synthetic(300, 0, 1), synthetic(200, 10, 2), config)
    table = hyperparameter_sensitivity(summary, best)
    assert table.groupby("hyperparameter").size().to_dict() == {
        "n_estimators": 2,
        "max_samples": 2,
        "max_features": 1,
        "log1p": 1,
    }


def test_hyperparameter_sensitivity_varies_one_parameter_at_a_time() -> None:
    config = small_config()
    _, summary, best = tune(synthetic(300, 0, 1), synthetic(200, 10, 2), config)
    table = hyperparameter_sensitivity(summary, best)
    by_param = table.groupby("hyperparameter").size().to_dict()
    assert by_param == {"n_estimators": 2, "max_samples": 1, "max_features": 1, "log1p": 2}
    assert table.groupby("hyperparameter")["chosen"].sum().eq(1).all()

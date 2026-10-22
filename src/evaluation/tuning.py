"""Ajuste do Isolation Forest SÓ na validação (config/model.yaml).

Para cada combinação da grade e cada semente de ajuste: treina no split de treino, pontua a
validação, escolhe o limiar de melhor F1 e registra F1, precisão, recall, ROC-AUC e PR-AUC.
A combinação escolhida é a de maior F1 médio (desempate: PR-AUC médio). O teste não é tocado.
"""

from __future__ import annotations

import itertools
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from evaluation.metrics import best_f1_threshold, ranking_metrics
from sacla.config import CONFIG_DIR, ConfigError, SaclaSettings, load_settings, load_yaml
from sacla.model import ModelParams, SaclaModel

log = logging.getLogger(__name__)

PARAM_NAMES = ("n_estimators", "max_samples", "max_features", "log1p")
METRICS = ("f1", "precision", "recall", "pr_auc", "roc_auc")


@dataclass(frozen=True)
class ModelConfig:
    log1p_features: tuple[str, ...]
    grid: dict[str, list[Any]]
    tuning_seeds: tuple[int, ...]
    evaluation_seeds: tuple[int, ...]
    selection_metric: str
    n_jobs: int
    label_free: ModelParams
    label_free_quantile: float
    sensitivity_windows: tuple[int, ...]
    results_dir: str
    models_dir: str
    robustness_dir: str
    raw: dict[str, Any]


def load_model_config(
    path: Path | None = None, settings: SaclaSettings | None = None
) -> ModelConfig:
    raw = load_yaml(path or CONFIG_DIR / "model.yaml")
    # A transformação dos atributos é do SACLA (sacla.yaml); a avaliação usa a mesma lista.
    log1p_features = (settings or load_settings()).detector.log1p_features
    try:
        lf = raw["label_free"]
        config = ModelConfig(
            log1p_features=log1p_features,
            grid={name: list(raw["grid"][name]) for name in PARAM_NAMES},
            tuning_seeds=tuple(int(s) for s in raw["tuning_seeds"]),
            evaluation_seeds=tuple(int(s) for s in raw["evaluation_seeds"]),
            selection_metric=str(raw["selection_metric"]),
            n_jobs=int(raw["n_jobs"]),
            label_free=ModelParams(
                n_estimators=int(lf["n_estimators"]),
                max_samples=lf["max_samples"],
                max_features=float(lf["max_features"]),
                log1p=bool(lf["log1p"]),
            ),
            label_free_quantile=float(lf["train_quantile"]),
            sensitivity_windows=tuple(int(w) for w in raw["sensitivity_windows"]),
            results_dir=str(raw["results_dir"]),
            models_dir=str(raw["models_dir"]),
            robustness_dir=str(raw["robustness"]["results_dir"]),
            raw=raw,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"model.yaml malformado: {exc!r}") from exc
    if config.selection_metric not in METRICS:
        raise ConfigError(f"selection_metric deve ser um de {METRICS}")
    return config


def grid_params(config: ModelConfig) -> list[ModelParams]:
    return [
        ModelParams(n_estimators=int(ne), max_samples=ms, max_features=float(mf), log1p=bool(lg))
        for ne, ms, mf, lg in itertools.product(*(config.grid[name] for name in PARAM_NAMES))
    ]


def validate_once(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    params: ModelParams,
    seed: int,
    config: ModelConfig,
) -> dict[str, Any]:
    started = time.perf_counter()
    model = SaclaModel.fit(train, params, seed, config.log1p_features, config.n_jobs)
    fit_s = time.perf_counter() - started
    started = time.perf_counter()
    scores = model.score(validation)
    score_s = time.perf_counter() - started
    labels = validation["label"].to_numpy()
    best = best_f1_threshold(labels, scores)
    return {
        **params.as_dict(),
        # Texto: a coluna mistura inteiros (256) e frações (0.1); numérica, viraria 256.0 e não
        # bateria com a configuração escolhida nas comparações da sensibilidade.
        "max_samples": str(params.max_samples),
        "config": params.slug(),
        "seed": seed,
        **best,
        **{k: v for k, v in ranking_metrics(labels, scores).items() if k != "prevalence"},
        "fit_s": fit_s,
        "score_s": score_s,
    }


def tune(
    train: pd.DataFrame, validation: pd.DataFrame, config: ModelConfig
) -> tuple[pd.DataFrame, pd.DataFrame, ModelParams]:
    """Resultados por (combinação, semente), resumo por combinação e a combinação escolhida."""
    params_list = grid_params(config)
    rows = []
    for i, params in enumerate(params_list, start=1):
        for seed in config.tuning_seeds:
            rows.append(validate_once(train, validation, params, seed, config))
        if i % 9 == 0 or i == len(params_list):
            log.info("grade: %d/%d combinações avaliadas", i, len(params_list))
    runs = pd.DataFrame(rows)
    summary = summarize(runs)
    key = config.selection_metric
    tie = "pr_auc" if key != "pr_auc" else "f1"
    best_row = summary.sort_values([f"{key}_mean", f"{tie}_mean"], ascending=False).iloc[0]
    best = next(p for p in params_list if p.slug() == best_row["config"])
    return runs, summary, best


def flat_columns(columns: pd.Index) -> list[str]:
    """('f1', 'mean') -> 'f1_mean' (colunas de um agg com várias estatísticas)."""
    return ["_".join(str(part) for part in column) for column in columns.to_list()]


def summarize(runs: pd.DataFrame) -> pd.DataFrame:
    grouped = runs.groupby(["config", *PARAM_NAMES], sort=False)
    summary = grouped[list(METRICS)].agg(["mean", "std"])
    summary.columns = flat_columns(summary.columns)
    timing = grouped[["fit_s", "score_s"]].mean()
    return summary.join(timing).reset_index().sort_values("f1_mean", ascending=False)

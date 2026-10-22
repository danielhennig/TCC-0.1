"""Análise de sensibilidade, feita SÓ na validação.

- Hiperparâmetros: F1 (média ± desvio nas sementes de ajuste) ao variar um hiperparâmetro com
  os demais fixos na combinação escolhida — sai direto da grade, sem novos treinos.
- Tamanho da janela: para cada tamanho, o conjunto é reagregado e a grade inteira é refeita.
  Como o número e o significado das janelas mudam com o tamanho, a comparação também é feita
  por cenário (fração de cenários da validação detectados), que não depende da janela.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from evaluation.metrics import best_f1_threshold, scenario_detection
from evaluation.tuning import PARAM_NAMES, ModelConfig, tune
from sacla.model import ModelParams, SaclaModel

log = logging.getLogger(__name__)


def hyperparameter_sensitivity(summary: pd.DataFrame, best: ModelParams) -> pd.DataFrame:
    chosen = best.as_dict()
    rows = []
    for name in PARAM_NAMES:
        others = [p for p in PARAM_NAMES if p != name]
        mask = pd.Series(True, index=summary.index)
        for other in others:
            mask &= summary[other].astype(str) == str(chosen[other])
        for _, row in summary[mask].iterrows():
            rows.append(
                {
                    "hyperparameter": name,
                    "value": str(row[name]),
                    "chosen": str(row[name]) == str(chosen[name]),
                    "f1_mean": row["f1_mean"],
                    "f1_std": row["f1_std"],
                    "pr_auc_mean": row["pr_auc_mean"],
                    "roc_auc_mean": row["roc_auc_mean"],
                }
            )
    return pd.DataFrame(rows)


def window_sensitivity(
    datasets: dict[int, pd.DataFrame],
    manifest: pd.DataFrame,
    config: ModelConfig,
    already_tuned: dict[int, tuple[pd.DataFrame, ModelParams]] | None = None,
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    """`already_tuned`: grades já calculadas (a da janela principal), que não são refeitas."""
    rows: list[dict[str, Any]] = []
    grids: dict[int, pd.DataFrame] = {}
    for window, data in sorted(datasets.items()):
        train = data[data["split"] == "train"]
        validation = data[data["split"] == "validation"]
        log.info("sensibilidade: janela de %d min (%d janelas)", window, len(data))
        if already_tuned and window in already_tuned:
            summary, best = already_tuned[window]
        else:
            _, summary, best = tune(train, validation, config)
        grids[window] = summary
        best_row = summary[summary["config"] == best.slug()].iloc[0]
        detected, alerts = [], []
        for seed in config.tuning_seeds:
            model = SaclaModel.fit(train, best, seed, config.log1p_features, config.n_jobs)
            scores = model.score(validation)
            threshold = best_f1_threshold(validation["label"].to_numpy(), scores)["threshold"]
            flags = scores >= threshold
            detection = scenario_detection(validation, flags, manifest)
            detected.append(float(detection["detected"].mean()))
            alerts.append(int(flags.sum()))
        rows.append(
            {
                "window_minutes": window,
                "windows_validation": len(validation),
                "anomalous_validation": int(validation["label"].sum()),
                "best_config": best.slug(),
                "f1_mean": best_row["f1_mean"],
                "f1_std": best_row["f1_std"],
                "pr_auc_mean": best_row["pr_auc_mean"],
                "scenario_recall_mean": float(np.mean(detected)),
                "alerts_mean": float(np.mean(alerts)),
            }
        )
    return pd.DataFrame(rows), grids

"""Métricas de detecção.

- Por janela: precisão, recall, F1, falsos positivos (contagem e taxa), ROC-AUC e PR-AUC
  (precisão média) sobre o score.
- Por tipo × intensidade: recall das janelas anômalas. Precisão por tipo não existe (um falso
  positivo não tem tipo), então só a precisão global é reportada.
- Por cenário: um cenário é "detectado" se ao menos uma de suas janelas for sinalizada, que é
  o que importa para quem recebe o alerta.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score

ArrayLike = np.ndarray[Any, Any] | pd.Series


def best_f1_threshold(labels: ArrayLike, scores: ArrayLike) -> dict[str, float]:
    """Limiar de score (sinaliza score >= limiar) que maximiza o F1."""
    precision, recall, thresholds = precision_recall_curve(np.asarray(labels), np.asarray(scores))
    precision, recall = precision[:-1], recall[:-1]  # o último ponto não tem limiar
    with np.errstate(divide="ignore", invalid="ignore"):
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0)
    best = int(np.argmax(f1))
    return {
        "threshold": float(thresholds[best]),
        "f1": float(f1[best]),
        "precision": float(precision[best]),
        "recall": float(recall[best]),
    }


def binary_metrics(labels: ArrayLike, flags: ArrayLike) -> dict[str, float]:
    y = np.asarray(labels).astype(bool)
    p = np.asarray(flags).astype(bool)
    tp = int(np.sum(y & p))
    fp = int(np.sum(~y & p))
    fn = int(np.sum(y & ~p))
    tn = int(np.sum(~y & ~p))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "fp_rate": fp / (fp + tn) if fp + tn else 0.0,
        "alerts": tp + fp,
    }


def ranking_metrics(labels: ArrayLike, scores: ArrayLike) -> dict[str, float]:
    y = np.asarray(labels)
    s = np.asarray(scores)
    return {
        "roc_auc": float(roc_auc_score(y, s)),
        "pr_auc": float(average_precision_score(y, s)),
        "prevalence": float(np.mean(y)),
    }


def recall_by_type(windows: pd.DataFrame, flags: ArrayLike) -> pd.DataFrame:
    """Recall por janela para cada tipo × intensidade (só janelas anômalas)."""
    frame = windows.assign(flag=np.asarray(flags).astype(bool))
    anomalous = frame[frame["label"] == 1]
    table = (
        anomalous.groupby(["anomaly_type", "intensity"])
        .agg(windows=("flag", "size"), detected=("flag", "sum"))
        .reset_index()
    )
    table["recall"] = table["detected"] / table["windows"]
    return table


def scenario_detection(
    windows: pd.DataFrame, flags: ArrayLike, manifest: pd.DataFrame
) -> pd.DataFrame:
    """Uma linha por cenário do manifesto presente nas janelas, com `detected`."""
    flagged = np.asarray(flags).astype(bool)
    hits: dict[int, bool] = {}
    for ids, flag in zip(windows["scenario_ids"], flagged, strict=True):
        if not ids:
            continue
        for scenario in str(ids).split("|"):
            key = int(scenario)
            hits[key] = hits.get(key, False) or bool(flag)
    present = manifest[manifest["scenario_id"].isin(list(hits))].copy()
    present["detected"] = present["scenario_id"].map(hits).astype(bool)
    return present[["scenario_id", "user", "type", "intensity", "novel", "split", "detected"]]


def scenario_recall_table(detection: pd.DataFrame) -> pd.DataFrame:
    table = (
        detection.groupby(["type", "intensity"])
        .agg(scenarios=("detected", "size"), detected=("detected", "sum"))
        .reset_index()
    )
    table["recall"] = table["detected"] / table["scenarios"]
    return table

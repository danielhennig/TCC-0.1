"""Métricas de detecção: limiar de melhor F1, métricas binárias e detecção por cenário."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evaluation.metrics import (
    best_f1_threshold,
    binary_metrics,
    ranking_metrics,
    recall_by_type,
    scenario_detection,
)


def test_best_f1_threshold_separates_perfectly_ranked_scores() -> None:
    labels = np.array([0, 0, 0, 1, 1])
    scores = np.array([0.1, 0.2, 0.3, 0.8, 0.9])
    best = best_f1_threshold(labels, scores)
    assert best["f1"] == 1.0 and best["threshold"] == 0.8
    assert binary_metrics(labels, scores >= best["threshold"])["f1"] == 1.0


def test_binary_metrics_counts() -> None:
    labels = np.array([1, 1, 0, 0, 0])
    flags = np.array([1, 0, 1, 0, 0])
    m = binary_metrics(labels, flags)
    assert (m["tp"], m["fn"], m["fp"], m["tn"], m["alerts"]) == (1, 1, 1, 2, 2)
    assert m["precision"] == 0.5 and m["recall"] == 0.5 and m["fp_rate"] == pytest.approx(1 / 3)


def test_binary_metrics_without_alerts_is_zero_not_error() -> None:
    m = binary_metrics(np.array([1, 0]), np.array([0, 0]))
    assert m["precision"] == 0.0 and m["f1"] == 0.0


def test_ranking_metrics() -> None:
    m = ranking_metrics(np.array([0, 0, 1, 1]), np.array([0.1, 0.4, 0.35, 0.8]))
    assert m["roc_auc"] == 0.75 and m["prevalence"] == 0.5


WINDOWS = pd.DataFrame(
    {
        "label": [1, 1, 1, 0],
        "anomaly_type": ["rajada", "rajada", "falha_auth", ""],
        "intensity": ["alta", "alta", "baixa", ""],
        "scenario_ids": ["1", "1|2", "2", ""],
    }
)
MANIFEST = pd.DataFrame(
    {
        "scenario_id": [1, 2, 3],
        "user": ["u", "u", "u"],
        "type": ["rajada", "falha_auth", "volume_alto"],
        "intensity": ["alta", "baixa", "media"],
        "novel": [False, False, False],
        "split": ["test", "test", "validation"],
    }
)


def test_scenario_detected_if_any_of_its_windows_is_flagged() -> None:
    detection = scenario_detection(WINDOWS, np.array([0, 1, 0, 0]), MANIFEST)
    assert dict(zip(detection["scenario_id"], detection["detected"], strict=True)) == {
        1: True,  # a segunda janela (cenários 1 e 2) foi sinalizada
        2: True,
    }
    # O cenário 3 não tem janelas neste conjunto e não entra.
    assert 3 not in set(detection["scenario_id"])


def test_recall_by_type_uses_only_anomalous_windows() -> None:
    table = recall_by_type(WINDOWS, np.array([1, 0, 1, 1])).set_index("anomaly_type")
    assert table.loc["rajada", "recall"] == 0.5 and table.loc["falha_auth", "recall"] == 1.0
    assert "" not in table.index

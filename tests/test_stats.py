"""Estatística da Fase 7 (D52): casos com resposta conhecida e reprodutibilidade."""

from __future__ import annotations

import numpy as np
import pytest

from evaluation.experiment import DETECTORS
from evaluation.metrics import binary_metrics
from evaluation.stats import (
    block_counts,
    holm,
    load_stats_config,
    mcnemar_exact,
    mean_interval,
    paired_permutation,
    percentile_interval,
    scenario_samples,
    stratified_weights,
    window_metric,
    window_samples,
)
from evaluation.tuning import load_model_config
from sacla.config import ConfigError


def test_holm_matches_hand_computation() -> None:
    # Ordenados: 0,01·3 = 0,03; 0,03·2 = 0,06; 0,04·1 = 0,04 → máximo acumulado 0,06.
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5, 0.01]) == pytest.approx([0.5, 0.02])
    # 0,8·2 = 1,6 → limitado a 1; o seguinte (0,9·1) não pode ficar abaixo do anterior.
    assert holm([0.9, 0.8]) == pytest.approx([1.0, 1.0])


def test_mcnemar_exact_uses_only_the_discordant_pairs() -> None:
    a = np.array([True] * 9 + [True] * 5 + [False] * 6)
    b = np.array([False] * 9 + [True] * 5 + [False] * 6)
    result = mcnemar_exact(a, b)
    assert (result["only_a"], result["only_b"]) == (9, 0)
    assert result["p"] == pytest.approx(2 * 0.5**9)  # bicaudal: 0,0039
    assert mcnemar_exact(np.array([True, False]), np.array([False, True]))["p"] == 1.0
    assert mcnemar_exact(np.array([True, False]), np.array([True, False]))["p"] == 1.0


def test_window_metric_from_block_counts_matches_binary_metrics() -> None:
    rng = np.random.default_rng(0)
    labels = rng.random(500) < 0.1
    flags = np.column_stack([rng.random(500) < 0.15, labels | (rng.random(500) < 0.05)])
    blocks = rng.integers(0, 40, size=500)
    counts = block_counts(labels, blocks, 40, flags)
    for metric in ("precision", "recall", "f1"):
        expected = np.mean([binary_metrics(labels, flags[:, s])[metric] for s in range(2)])
        assert window_metric(counts.sum(axis=0), metric) == pytest.approx(expected)


def test_stratified_weights_keep_each_stratum_size_and_repeat_with_the_seed() -> None:
    strata = np.array(["a"] * 3 + ["b"] * 5)
    first = stratified_weights(strata, 200, np.random.default_rng(7))
    again = stratified_weights(strata, 200, np.random.default_rng(7))
    assert np.array_equal(first, again)
    assert (first[:, :3].sum(axis=1) == 3).all() and (first[:, 3:].sum(axis=1) == 5).all()
    assert first.min() >= 0 and len(np.unique(first[:, 0])) > 1  # há reposição


def test_bootstrap_interval_covers_a_known_proportion() -> None:
    rng = np.random.default_rng(1)
    detected = (rng.random(600) < 0.7).astype(float)  # proporção verdadeira 0,7
    strata = np.repeat(["s1", "s2", "s3"], 200)
    weights = stratified_weights(strata, 2000, np.random.default_rng(0))
    low, high = percentile_interval(scenario_samples(weights, detected), 0.95)
    assert low < detected.mean() < high
    assert low < 0.7 < high
    assert 0.05 < high - low < 0.1  # ~ 2 · 1,96 · √(0,21 / 600) ≈ 0,073


def test_window_bootstrap_of_identical_detectors_has_zero_difference() -> None:
    rng = np.random.default_rng(2)
    labels = rng.random(300) < 0.2
    flags = (rng.random(300) < 0.25)[:, None]
    blocks = np.repeat(np.arange(30), 10)
    counts = block_counts(labels, blocks, 30, flags)
    weights = stratified_weights(np.zeros(30), 500, np.random.default_rng(0))
    gap = window_samples(weights, counts, "f1") - window_samples(weights, counts, "f1")
    assert np.all(gap == 0)


def test_paired_permutation_identical_detectors_and_a_clear_difference() -> None:
    rng = np.random.default_rng(3)
    labels = rng.random(600) < 0.2
    blocks = np.repeat(np.arange(60), 10)
    good = block_counts(labels, blocks, 60, labels[:, None])  # detector perfeito
    noise = block_counts(labels, blocks, 60, np.column_stack([rng.random(600) < 0.2] * 10))
    same = paired_permutation(good, good, "f1", 999, np.random.default_rng(0))
    assert same["diff"] == 0 and same["p"] == 1.0
    clear = paired_permutation(good, noise, "f1", 999, np.random.default_rng(0))
    assert clear["diff"] > 0.5 and clear["p"] == pytest.approx(1 / 1000)
    again = paired_permutation(good, noise, "f1", 999, np.random.default_rng(0))
    assert again == clear


def test_mean_interval_is_the_student_t_interval() -> None:
    mean, low, high = mean_interval(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), 0.95)
    half = 2.7764451 * np.std([1, 2, 3, 4, 5], ddof=1) / np.sqrt(5)  # t(0,975; 4)
    assert mean == 3.0
    assert (low, high) == pytest.approx((3 - half, 3 + half))


def test_project_stats_config_has_the_preregistered_hypotheses() -> None:
    config = load_stats_config(load_model_config().raw, DETECTORS)
    assert [h.id for h in config.hypotheses] == ["H1", "H2", "H3"]
    assert [(h.metric, h.subset, h.a, h.b) for h in config.hypotheses] == [
        ("scenarios", "all", "if_mais_regras", "regras"),
        ("f1", "all", "if_mais_regras", "regras"),
        ("scenarios", "novel", "isolation_forest", "regras"),
    ]
    assert (config.alpha, config.confidence, config.majority) == (0.05, 0.95, 0.5)


def test_stats_config_rejects_window_metric_on_a_scenario_subset() -> None:
    raw = load_model_config().raw
    bad = {
        **raw,
        "stats": {
            **raw["stats"],
            "descriptive": [
                {"metric": "f1", "subset": "novel", "a": "regras", "b": "isolation_forest"}
            ],
        },
    }
    with pytest.raises(ConfigError):
        load_stats_config(bad, DETECTORS)

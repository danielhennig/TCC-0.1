"""Validação confirmatória (Fase 7, D52): agregação por blocos e cenários, hipóteses."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from evaluation.confirmatory import (
    Resampler,
    RunData,
    descriptive_table,
    hypothesis_table,
    pool,
    scenario_rates,
)
from evaluation.experiment import DETECTORS
from evaluation.metrics import binary_metrics
from evaluation.stats import load_stats_config
from evaluation.tuning import load_model_config

SEEDS = 4


def fake_run(run_id: str, seed: int) -> RunData:
    """Dois usuários × três dias × 20 janelas; cenários 1–6, o 6 do tipo inédito."""
    rng = np.random.default_rng(seed)
    n = 120
    windows = pd.DataFrame(
        {
            "user": np.tile(np.repeat(["ana", "bia"], 20), 3),
            "window_start_local": np.repeat(
                ["2026-03-23T10:00-0300", "2026-03-24T10:00-0300", "2026-03-25T10:00-0300"], 40
            ),
            "label": 0,
            "scenario_ids": "",
        }
    )
    anomalous = rng.choice(n, size=12, replace=False)
    windows.loc[anomalous, "label"] = 1
    windows.loc[anomalous, "scenario_ids"] = [str(1 + i % 6) for i in range(12)]
    labels = windows["label"].to_numpy() == 1
    rules = (labels & (rng.random(n) < 0.6)) | (rng.random(n) < 0.02)
    forest = np.column_stack([labels & (rng.random(n) < 0.7) for _ in range(SEEDS)])
    flags = {
        "isolation_forest": forest,
        "if_sem_rotulos": np.column_stack([rng.random(n) < 0.1 for _ in range(SEEDS)]),
        "regras": rules[:, None],
        "if_mais_regras": forest | rules[:, None],
    }
    manifest = pd.DataFrame(
        {
            "scenario_id": range(1, 7),
            "user": "ana",
            "type": ["rajada"] * 5 + ["leitura_atipica"],
            "intensity": "alta",
            "novel": [False] * 5 + [True],
            "split": "test",
        }
    )
    return RunData(
        run_id, "confirmatorio", windows, flags, scenario_rates(windows, flags, manifest)
    )


def test_scenario_rates_are_the_fraction_of_seeds_that_detect() -> None:
    run = fake_run("r1", 0)
    rates = run.scenarios
    assert list(rates["scenario_id"]) == [1, 2, 3, 4, 5, 6]
    assert rates["novel"].tolist() == [False] * 5 + [True]
    assert set(rates["regras"]) <= {0.0, 1.0}
    assert rates["isolation_forest"].between(0, 1).all()
    assert (rates["if_mais_regras"] >= rates[["isolation_forest", "regras"]].max(axis=1)).all()


def test_pooled_values_match_direct_metrics() -> None:
    runs = [fake_run("r1", 0), fake_run("r2", 1)]
    pooled = pool(runs)
    assert len(pooled.block_strata) == 12  # 2 simulações × 2 usuários × 3 dias
    resampler = Resampler(pooled, load_stats_config(load_model_config().raw, DETECTORS))
    labels = np.concatenate([r.windows["label"].to_numpy() for r in runs])
    for detector in DETECTORS:
        flags = np.vstack([r.flags[detector] for r in runs])
        expected = np.mean(
            [binary_metrics(labels, flags[:, s])["f1"] for s in range(flags.shape[1])]
        )
        assert resampler.value(detector, "f1", "all") == pytest.approx(expected)
        low, high = resampler.interval(detector, "f1", "all")
        assert low <= high
    scenarios = pooled.scenarios
    assert resampler.value("regras", "scenarios", "novel") == pytest.approx(
        scenarios.loc[scenarios["novel"], "regras"].mean()
    )


def test_hypotheses_follow_the_config_and_are_holm_adjusted() -> None:
    config = load_stats_config(load_model_config().raw, DETECTORS)
    resampler = Resampler(pool([fake_run("r1", 0), fake_run("r2", 1)]), config)
    table = hypothesis_table(resampler, config)
    assert table["id"].tolist() == ["H1", "H2", "H3"]
    assert (table["p_holm"] >= table["p"]).all() and table["p_holm"].le(1).all()
    h1 = table.iloc[0]
    assert h1["only_b"] == 0  # a união contém as regras: nenhum cenário só das regras
    assert table.iloc[1]["test"] == "permutacao_pareada_blocos"
    described = descriptive_table(resampler, config)
    assert len(described) == len(config.descriptive)
    assert (described["diff_low"] <= described["diff_high"]).all()

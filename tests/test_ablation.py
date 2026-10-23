"""Ablação de atributos (Fase 7, D52): só treino e validação; variantes e regras corretas."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from evaluation.ablation import (
    FULL,
    AblationConfig,
    ablate_run,
    load_ablation_config,
    summarize,
    type_recall,
    variants,
)
from evaluation.rules_baseline import Rule, load_rules
from evaluation.tuning import ModelConfig, load_model_config
from sacla.config import ConfigError
from sacla.features import FEATURE_COLUMNS
from sacla.model import ModelParams

PARAMS = ModelParams(n_estimators=20, max_samples=64, max_features=0.5, log1p=True)
START_MS = 1_773_000_000_000
WINDOW_MS = 300_000


def windows(n: int, split: str, anomalies: int, seed: int, offset: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.poisson(3, size=(n, len(FEATURE_COLUMNS))).astype(float), columns=list(FEATURE_COLUMNS)
    )
    frame.index = range(offset, offset + n)
    frame["user"] = np.where(np.arange(n) % 2 == 0, "ana", "bia")
    frame["window_start_ms"] = START_MS + (offset + np.arange(n)) * WINDOW_MS
    frame["split"] = split
    frame["label"] = 0
    frame["scenario_ids"] = ""
    for i in range(anomalies):
        row = frame.index[i]
        frame.loc[row, ["n_commands", "rows_returned_sum", "label"]] = (400.0, 9_000.0, 1)
        frame.loc[row, "scenario_ids"] = str(i + 1)
    return frame


def manifest(scenarios: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "scenario_id": range(1, scenarios + 1),
            "user": "ana",
            "type": "volume_alto",
            "intensity": "alta",
            "novel": False,
            "split": "validation",
        }
    )


def small_setup() -> tuple[ModelConfig, list[Rule], AblationConfig]:
    config = replace(load_model_config(), tuning_seeds=(0, 1))
    rules = load_rules()
    return config, rules, load_ablation_config(config.raw, rules)


def test_ablation_refuses_test_windows() -> None:
    config, rules, ablation = small_setup()
    train = windows(200, "train", 0, 1, 0)
    leaked = windows(60, "test", 4, 2, 1000)
    with pytest.raises(ValueError):
        ablate_run("r", train, leaked, manifest(4), PARAMS, config, ablation, rules, "UTC")
    mixed = pd.concat([windows(60, "validation", 4, 2, 1000), windows(5, "test", 0, 3, 2000)])
    with pytest.raises(ValueError):
        ablate_run("r", train, mixed, manifest(4), PARAMS, config, ablation, rules, "UTC")


def test_each_variant_removes_exactly_its_group() -> None:
    _, _, ablation = small_setup()
    found = dict(variants(ablation))
    assert found[FULL] == FEATURE_COLUMNS
    for name, group in ablation.groups.items():
        assert set(found[name]) == set(FEATURE_COLUMNS) - set(group)
    no_explain = ablation.groups[ablation.auto_explain_group]
    assert {"rows_returned_sum", "rows_affected_sum", "n_tables"} <= set(no_explain)
    assert set(ablation.auto_explain_rules) == {"volume", "dml_volume"}


def test_ablation_rows_and_deltas_against_the_full_set() -> None:
    config, rules, ablation = small_setup()
    train = windows(300, "train", 0, 1, 0)
    validation = windows(120, "validation", 6, 2, 1000)
    rows, types = ablate_run(
        "r1", train, validation, manifest(6), PARAMS, config, ablation, rules, "UTC"
    )
    seeds = len(config.tuning_seeds)
    forest = rows[rows["detector"] == "isolation_forest"]
    assert len(forest) == len(variants(ablation)) * seeds
    assert set(rows.loc[rows["detector"] == "regras", "variant"]) == {
        FULL,
        ablation.auto_explain_group,
    }
    assert set(rows.loc[rows["detector"] == "if_mais_regras", "variant"]) == {
        FULL,
        ablation.auto_explain_group,
    }
    by_run, summary = summarize(pd.concat([rows, rows.assign(run_id="r2")]), 0.95)
    full = summary[(summary["variant"] == FULL) & (summary["detector"] == "isolation_forest")]
    assert full["delta_f1_mean"].iloc[0] == 0.0 and full["runs"].iloc[0] == 2
    assert (by_run["delta_f1"] == by_run["f1"] - by_run["f1_completo"]).all()
    # Por tipo: as mesmas detecções, somadas por tipo (aqui um tipo só, com os 6 cenários).
    assert (types["scenarios"] == 6).all() and len(types) == len(rows)
    by_type = type_recall(pd.concat([types, types.assign(run_id="r2")]))
    assert (by_type["scenarios"] == 12).all()
    forest_full = rows[(rows["variant"] == FULL) & (rows["detector"] == "isolation_forest")]
    chosen = by_type[(by_type["variant"] == FULL) & (by_type["detector"] == "isolation_forest")]
    assert chosen["recall"].iloc[0] == forest_full["scenario_recall"].mean()


def test_ablation_config_rejects_unknown_features() -> None:
    raw = load_model_config().raw
    bad = {**raw, "ablation": {**raw["ablation"], "groups": {"x": ["nao_existe"]}}}
    with pytest.raises(ConfigError):
        load_ablation_config(bad, load_rules())

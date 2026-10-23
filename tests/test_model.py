"""Isolation Forest do SACLA: determinismo, sentido do score, log1p e persistência."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sacla.features import FEATURE_COLUMNS
from sacla.model import ModelParams, SaclaModel, feature_matrix

PARAMS = ModelParams(n_estimators=50, max_samples=64, max_features=1.0, log1p=True)
LOG_FEATURES = ("n_commands", "rows_returned_sum")


def synthetic(n: int, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frame = pd.DataFrame(
        rng.poisson(3, size=(n, len(FEATURE_COLUMNS))).astype(float), columns=list(FEATURE_COLUMNS)
    )
    frame["rows_returned_sum"] = rng.poisson(20, size=n)
    return frame


def test_same_seed_gives_same_scores_and_outlier_scores_higher() -> None:
    train = synthetic(500)
    probe = pd.concat(
        [
            synthetic(5, seed=1),
            synthetic(1, seed=2).assign(n_commands=500.0, rows_returned_sum=50_000.0),
        ],
        ignore_index=True,
    )
    first = SaclaModel.fit(train, PARAMS, 7, LOG_FEATURES).score(probe)
    second = SaclaModel.fit(train, PARAMS, 7, LOG_FEATURES).score(probe)
    assert np.array_equal(first, second)
    assert first[-1] > first[:-1].max()  # maior score = mais anômala


def test_log1p_only_on_listed_features() -> None:
    frame = synthetic(3)
    raw = feature_matrix(frame, False, LOG_FEATURES)
    logged = feature_matrix(frame, True, LOG_FEATURES)
    columns = list(FEATURE_COLUMNS)
    i, j = columns.index("rows_returned_sum"), columns.index("n_tables")
    assert np.allclose(logged[:, i], np.log1p(raw[:, i]))
    assert np.array_equal(logged[:, j], raw[:, j])


def test_negative_values_are_rejected_for_log1p() -> None:
    frame = synthetic(3).assign(n_commands=-1.0)
    with pytest.raises(ValueError):
        feature_matrix(frame, True, LOG_FEATURES)


def test_flag_requires_threshold_and_model_round_trips(tmp_path: Path) -> None:
    train = synthetic(200)
    model = SaclaModel.fit(train, PARAMS, 1, LOG_FEATURES)
    with pytest.raises(ValueError):
        model.flag(train)
    model.with_threshold(float(np.quantile(model.score(train), 0.9)))
    path = tmp_path / "modelo.joblib"
    model.save(path)
    loaded = SaclaModel.load(path)
    assert np.array_equal(loaded.flag(train), model.flag(train))
    assert loaded.params == PARAMS and loaded.threshold == model.threshold


def test_slug_identifies_the_configuration() -> None:
    assert PARAMS.slug() == "ne50-ms64-mf1-log1"


def test_default_features_are_all_and_give_the_same_scores_as_before() -> None:
    # A ablação (D52) não pode mudar o modelo padrão: todos os atributos, mesmos scores.
    train, probe = synthetic(300), synthetic(20, seed=3)
    default = SaclaModel.fit(train, PARAMS, 4, LOG_FEATURES)
    explicit = SaclaModel.fit(train, PARAMS, 4, LOG_FEATURES, features=FEATURE_COLUMNS)
    assert default.features == FEATURE_COLUMNS
    assert np.array_equal(default.score(probe), explicit.score(probe))


def test_feature_subset_ignores_the_removed_columns() -> None:
    train, probe = synthetic(300), synthetic(20, seed=3)
    removed = ("rows_returned_sum", "n_tables")
    kept = tuple(c for c in reversed(FEATURE_COLUMNS) if c not in removed)
    model = SaclaModel.fit(train, PARAMS, 4, LOG_FEATURES, features=kept)
    assert model.features == tuple(c for c in FEATURE_COLUMNS if c not in removed)  # ordem fixa
    changed = probe.assign(rows_returned_sum=1e9, n_tables=1e9)
    assert np.array_equal(model.score(probe), model.score(changed))
    retrained = SaclaModel.fit(train.assign(n_tables=-5.0), PARAMS, 4, LOG_FEATURES, features=kept)
    assert np.array_equal(model.score(probe), retrained.score(probe))


def test_unknown_or_empty_feature_subset_is_rejected() -> None:
    with pytest.raises(ValueError):
        SaclaModel.fit(synthetic(50), PARAMS, 0, LOG_FEATURES, features=("nao_existe",))
    with pytest.raises(ValueError):
        SaclaModel.fit(synthetic(50), PARAMS, 0, LOG_FEATURES, features=())


def test_model_saved_before_the_features_field_still_uses_every_feature() -> None:
    model = SaclaModel.fit(synthetic(200), PARAMS, 2, LOG_FEATURES)
    old = SaclaModel.__new__(SaclaModel)
    old.__dict__.update({k: v for k, v in model.__dict__.items() if k != "features"})
    assert old.features == FEATURE_COLUMNS
    assert np.array_equal(old.score(synthetic(10, seed=5)), model.score(synthetic(10, seed=5)))

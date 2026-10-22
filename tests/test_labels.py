"""Regra de rótulo das janelas a partir do manifesto e atribuição de split."""

from __future__ import annotations

import pandas as pd
import pytest

from evaluation.dataset import _check_window_fits_splits
from evaluation.labels import assign_split, label_windows
from sacla.config import ConfigError

W = 300_000  # janela de 5 min


def windows(user: str, starts: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        {"user": user, "window_start_ms": starts, "window_end_ms": [s + W for s in starts]}
    )


def manifest(*rows: tuple[int, str, int, int, str, str, bool]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["scenario_id", "user", "start_ms", "end_ms", "type", "intensity", "novel"]
    )


def test_half_open_overlap_rule() -> None:
    w = windows("u1", [0, W, 2 * W, 3 * W])
    # Cenário [W, 2W): toca só a segunda janela. Fim exclusivo: a janela que começa em 2W não
    # é anômala; a primeira termina exatamente no início do cenário e também não é.
    labeled = label_windows(w, manifest((1, "u1", W, 2 * W, "rajada", "alta", False)))
    assert list(labeled["label"]) == [0, 1, 0, 0]
    assert labeled["overlap_frac"].iloc[1] == 1.0


def test_partial_overlap_marks_every_touched_window() -> None:
    w = windows("u1", [0, W, 2 * W])
    labeled = label_windows(w, manifest((7, "u1", W - 1, W + 1, "falha_auth", "baixa", False)))
    assert list(labeled["label"]) == [1, 1, 0]
    assert labeled["overlap_frac"].iloc[0] == 1 / W


def test_other_users_are_not_labeled() -> None:
    w = pd.concat([windows("u1", [0]), windows("u2", [0])], ignore_index=True)
    labeled = label_windows(w, manifest((1, "u1", 0, W, "rajada", "media", False)))
    assert list(labeled["label"]) == [1, 0]


def test_multiple_scenarios_keep_all_ids_and_largest_overlap_type() -> None:
    w = windows("u1", [0])
    labeled = label_windows(
        w,
        manifest(
            (3, "u1", 0, W // 10, "sem_permissao", "baixa", False),
            (4, "u1", W // 2, W, "leitura_atipica", "alta", True),
        ),
    )
    row = labeled.iloc[0]
    assert row.scenario_ids == "3|4"
    assert (row.anomaly_type, row.intensity, row.novel) == ("leitura_atipica", "alta", True)
    assert row.overlap_frac == 0.5


def test_window_without_scenario_has_empty_metadata() -> None:
    labeled = label_windows(windows("u1", [0]), manifest())
    row = labeled.iloc[0]
    assert (row.label, row.scenario_ids, row.anomaly_type, row.novel) == (0, "", "", False)


def test_assign_split_by_window_start() -> None:
    w = windows("u1", [0, W, 2 * W])
    splits = {"train": {"start_ms": 0, "end_ms": W}, "validation": {"start_ms": W, "end_ms": 3 * W}}
    assert list(assign_split(w, splits)) == ["train", "validation", "validation"]


def test_window_size_must_divide_split_bounds() -> None:
    splits = {"train": {"start_ms": 0, "end_ms": 24 * 3_600_000}}
    _check_window_fits_splits(5, splits)
    with pytest.raises(ConfigError):
        _check_window_fits_splits(7, splits)

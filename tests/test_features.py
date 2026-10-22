"""Agregação por usuário x janela e atributos relativos ao histórico (sem vazamento)."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any

import pandas as pd
import pytest

from sacla.config import ConfigError, load_settings
from sacla.events import EVENT_COLUMNS
from sacla.features import FEATURE_COLUMNS, aggregate_windows, build_features, fit_baselines
from sacla.reader import parse_timestamp

SETTINGS = load_settings()  # janela de 5 min, America/Sao_Paulo
MIN = 60_000


def ts(value: str) -> int:
    return parse_timestamp(value + " UTC")


def ev(when: str, kind: str = "query", user: str = "u1", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ts_ms": ts(when),
        "user": user,
        "session_id": "s",
        "remote_host": "10.0.0.1",
        "application_name": "app",
        "kind": kind,
        "sql_class": "SELECT",
        "duration_ms": 1.0,
        "rows_returned": 1,
        "rows_affected": None,
        "tables": ("film",),
        "state_code": None,
        "query_id": 1,
    }
    base.update(fields)
    return base


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(EVENT_COLUMNS))
    for column in ("rows_returned", "rows_affected", "query_id"):
        df[column] = df[column].astype("Int64")
    df["duration_ms"] = df["duration_ms"].astype("Float64")
    return df


def test_window_boundaries_are_half_open() -> None:
    windows = aggregate_windows(
        frame([ev("2026-03-02 13:04:59.999"), ev("2026-03-02 13:05:00.000")]), SETTINGS
    )
    assert list(windows["window_start_ms"]) == [
        ts("2026-03-02 13:00:00.000"),
        ts("2026-03-02 13:05:00.000"),
    ]
    assert list(windows["n_commands"]) == [1, 1]
    assert (windows["window_end_ms"] - windows["window_start_ms"]).eq(5 * MIN).all()


def test_counts_rows_durations_and_distinct_sets() -> None:
    rows = [
        ev("2026-03-02 13:00:01", rows_returned=10, duration_ms=1.0, tables=("film", "actor")),
        ev("2026-03-02 13:00:02", rows_returned=30, duration_ms=3.0, remote_host="10.0.0.2"),
        ev(
            "2026-03-02 13:00:03",
            sql_class="UPDATE",
            rows_returned=0,
            rows_affected=7,
            duration_ms=2.0,
            tables=("customer",),
        ),
        ev("2026-03-02 13:00:04", sql_class="TCL", rows_returned=None, tables=()),
        ev(
            "2026-03-02 13:00:05",
            kind="query_error",
            state_code="42501",
            rows_returned=None,
            duration_ms=None,
            tables=("staff",),
        ),
        ev(
            "2026-03-02 13:00:06",
            kind="auth_failure",
            sql_class=None,
            rows_returned=None,
            duration_ms=None,
            tables=(),
        ),
        ev(
            "2026-03-02 13:00:07",
            kind="connection",
            sql_class=None,
            rows_returned=None,
            duration_ms=None,
            tables=(),
        ),
    ]
    w = aggregate_windows(frame(rows), SETTINGS).iloc[0]
    assert w.n_commands == 4  # BEGIN/COMMIT (TCL) não contam; o comando com erro conta
    assert (w.n_select, w.n_update, w.n_errors, w.n_permission_denied) == (3, 1, 1, 1)
    assert (w.n_auth_failures, w.n_connections) == (1, 1)
    assert (w.rows_returned_sum, w.rows_returned_max, w.rows_affected_sum) == (40, 30, 7)
    # Média de 1, 3 e 2 ms: a duração do BEGIN/COMMIT (TCL) não entra.
    assert w.duration_mean_ms == pytest.approx(2.0) and w.duration_max_ms == 3.0
    assert w.n_tables == 4 and w.n_ips == 2


def test_time_features_use_local_timezone() -> None:
    # 13:00 UTC = 10:00 em São Paulo; 2026-03-07 é sábado.
    weekday = aggregate_windows(frame([ev("2026-03-02 13:00:00")]), SETTINGS).iloc[0]
    saturday = aggregate_windows(frame([ev("2026-03-07 13:00:00")]), SETTINGS).iloc[0]
    assert weekday.hour_sin == pytest.approx(math.sin(2 * math.pi * 10 / 24))
    assert weekday.hour_cos == pytest.approx(math.cos(2 * math.pi * 10 / 24))
    assert (weekday.weekend, saturday.weekend) == (0, 1)


def _history_and_test() -> tuple[pd.DataFrame, int]:
    rows = []
    for day in range(2, 9):  # histórico: 10h locais, 3 comandos por janela
        for i in range(3):
            rows.append(ev(f"2026-03-0{day} 13:00:0{i}"))
    rows += [  # teste: 03h locais, tabela, IP e formato inéditos
        ev("2026-03-10 06:00:00", tables=("staff",), remote_host="10.9.9.9", query_id=99),
        ev("2026-03-10 06:00:01"),
    ]
    return frame(rows), ts("2026-03-09 03:00:00.000")


def test_relative_features_use_history_only() -> None:
    events, history_end = _history_and_test()
    features, baselines = build_features(events, SETTINGS, history_end)
    test_window = features[features["window_start_ms"] >= history_end].iloc[0]
    normal_window = features[features["window_start_ms"] < history_end].iloc[0]

    assert baselines["u1"].windows == 7
    assert test_window.new_tables_frac == pytest.approx(1 / 2)  # {film, staff}
    assert test_window.new_ips_frac == pytest.approx(1 / 2)
    assert test_window.new_query_shapes_frac == pytest.approx(1 / 2)
    assert test_window.hour_surprisal > normal_window.hour_surprisal
    assert test_window.z_commands < 0  # 2 comandos contra mediana 3
    assert normal_window.new_tables_frac == 0 and normal_window.z_commands == 0
    assert list(features.columns[4 : 4 + len(FEATURE_COLUMNS)]) == list(FEATURE_COLUMNS)


def test_changing_data_after_history_end_does_not_change_baselines() -> None:
    events, history_end = _history_and_test()
    altered = events.copy()
    future = altered["ts_ms"] >= history_end
    altered.loc[future, "user"] = "u1"
    altered.loc[future, "tables"] = pd.Series(
        [("payment",)] * int(future.sum()), index=altered.index[future]
    )
    original = fit_baselines(aggregate_windows(events, SETTINGS), SETTINGS, history_end)
    changed = fit_baselines(aggregate_windows(altered, SETTINGS), SETTINGS, history_end)
    assert original == changed


def test_user_without_history_gets_neutral_reference() -> None:
    events, history_end = _history_and_test()
    newcomer = frame([ev("2026-03-10 13:00:00", user="novo")])
    features, _ = build_features(
        pd.concat([events, newcomer], ignore_index=True), SETTINGS, history_end
    )
    row = features[features["user"] == "novo"].iloc[0]
    assert row.new_tables_frac == 1.0 and row.hour_surprisal == pytest.approx(math.log(24))


def test_window_crossing_history_end_is_not_part_of_history() -> None:
    seven = replace(SETTINGS, features=replace(SETTINGS.features, window_minutes=7))
    history_end = ts("2026-03-02 13:10:00.000")  # não é múltiplo de 7 min
    events = frame(
        [
            ev("2026-03-02 13:00:00"),
            ev("2026-03-02 13:09:00"),
            ev("2026-03-02 13:12:00", tables=("staff",)),
        ]
    )
    windows = aggregate_windows(events, seven)
    baselines = fit_baselines(windows, seven, history_end)
    # A janela que contém 13:09 vai até depois de 13:10 e leva junto o evento de 13:12.
    assert "staff" not in baselines["u1"].tables
    assert baselines["u1"].windows == int((windows["window_end_ms"] <= history_end).sum())


@pytest.mark.parametrize("field", ["window_minutes", "hour_smoothing", "robust_scale_floor"])
def test_non_positive_settings_are_rejected_even_via_replace(field: str) -> None:
    with pytest.raises(ConfigError):
        replace(SETTINGS.features, **{field: 0})

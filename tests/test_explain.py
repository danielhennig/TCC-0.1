"""Explicação dos alertas: comparação com o histórico do próprio usuário."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from sacla.config import load_settings
from sacla.explain import (
    NO_HISTORY,
    NO_SINGLE_DEVIATION,
    describe,
    explain,
    fit_reference,
    format_number,
)

SETTINGS = load_settings()
FEATURES = SETTINGS.report.explain_features


def history(n: int = 200, user: str = "u1") -> pd.DataFrame:
    rng = np.random.default_rng(0)
    frame = pd.DataFrame(0.0, index=range(n), columns=list(FEATURES))
    frame["n_commands"] = rng.integers(5, 16, size=n)
    frame["n_select"] = frame["n_commands"]
    frame["rows_returned_sum"] = rng.integers(50, 151, size=n)
    frame["rows_returned_max"] = 20.0
    frame["hour_surprisal"] = rng.uniform(2.0, 3.0, size=n)
    frame["n_tables"] = 1.0
    frame["n_ips"] = 1.0
    frame["user"] = user
    frame["window_start_local"] = [
        f"2026-03-0{2 + i % 5}T{9 + i % 8:02d}:00-0300" for i in range(n)
    ]
    frame["tables"] = "film"
    frame["ips"] = "10.0.0.1"
    return frame


def normal_row(**changes: Any) -> dict[str, Any]:
    row = history(1).iloc[0].to_dict()
    row.update({"n_commands": 10.0, "n_select": 10.0, "rows_returned_sum": 100.0})
    row.update(changes)
    return row


REFERENCE = fit_reference(history(), FEATURES)["u1"]


def test_value_above_the_user_maximum_is_the_first_deviation() -> None:
    deviations = explain(normal_row(rows_returned_sum=50_000.0), REFERENCE, SETTINGS.report)
    assert deviations[0].feature == "rows_returned_sum"
    assert deviations[0].tail == 0.0
    assert deviations[0].maximum == REFERENCE.values["rows_returned_sum"][-1]
    assert "50.000 (acima do máximo do histórico" in deviations[0].text


def test_values_inside_the_normal_range_produce_no_deviation() -> None:
    deviations = explain(normal_row(), REFERENCE, SETTINGS.report)
    assert deviations == []
    assert describe(deviations, REFERENCE) == [NO_SINGLE_DEVIATION]


def test_user_without_history_is_reported_as_such() -> None:
    assert explain(normal_row(), None, SETTINGS.report) == []
    assert describe([], None) == [NO_HISTORY]


def test_new_tables_and_ips_are_named() -> None:
    row = normal_row(
        tables="film|staff", new_tables_frac=0.5, ips="10.0.0.1|172.30.30.9", new_ips_frac=0.5
    )
    texts = [d.text for d in explain(row, REFERENCE, SETTINGS.report)]
    assert "tabelas nunca acessadas pelo usuário: staff" in texts
    assert "IP de origem nunca usado pelo usuário: 172.30.30.9" in texts


def test_rare_hour_is_described_with_the_local_hour_and_its_share() -> None:
    row = normal_row(hour_surprisal=9.0, window_start_local="2026-03-23T03:05-0300")
    (deviation,) = explain(row, REFERENCE, SETTINGS.report)
    assert deviation.text == "horário incomum para o usuário: 03h (0,0% das janelas do histórico)"


def test_at_most_top_features_ordered_from_rarest() -> None:
    row = normal_row(
        n_commands=15.0,  # no topo do histórico, mas atingido por algumas janelas
        rows_returned_sum=10_000.0,
        n_permission_denied=3.0,
        n_auth_failures=5.0,
        new_tables_frac=1.0,
        tables="staff",
    )
    settings = replace(SETTINGS.report, top_features=3)
    deviations = explain(row, REFERENCE, settings)
    assert len(deviations) == 3
    assert [d.tail for d in deviations] == sorted(d.tail for d in deviations)
    assert "n_commands" not in {d.feature for d in deviations}  # há desvios mais raros


def test_brazilian_number_format() -> None:
    assert format_number(51805) == "51.805"
    assert format_number(3.5) == "3,5"
    assert format_number(1234.56) == "1.234,6"

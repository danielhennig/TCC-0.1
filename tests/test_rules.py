"""Baseline de regras: regras fixas, por estatística do usuário (só treino) e hora inédita."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evaluation.rules_baseline import Rule, apply_rules, fit_rule_stats, load_rules
from sacla.config import ConfigError

RULES = [
    Rule(name="auth", kind="fixed", feature="n_auth_failures", op=">=", value=3),
    Rule(name="rajada", kind="user_stat", feature="n_commands", stat="max", multiplier=2),
    Rule(name="hora", kind="unseen_hour"),
]


def frame(rows: list[tuple[str, int, int, int]]) -> tuple[pd.DataFrame, pd.Series]:
    data = pd.DataFrame(rows, columns=["user", "n_commands", "n_auth_failures", "hour"])
    return data, data["hour"]


def test_rules_fire_as_specified_with_stats_from_train_only() -> None:
    train, train_hours = frame([("u1", 4, 0, 10), ("u1", 5, 0, 11)])
    stats = fit_rule_stats(train, RULES, train_hours)
    test, test_hours = frame(
        [
            ("u1", 5, 0, 10),  # normal
            ("u1", 11, 0, 10),  # 11 > 2 x 5: rajada
            ("u1", 1, 3, 10),  # 3 falhas de login
            ("u1", 1, 0, 3),  # hora nunca vista
            ("u9", 99, 0, 3),  # usuário sem histórico: sem referência, nenhuma regra relativa
        ]
    )
    fired = apply_rules(test, RULES, stats, test_hours)
    assert list(fired["rajada"]) == [False, True, False, False, False]
    assert list(fired["auth"]) == [False, False, True, False, False]
    assert list(fired["hora"]) == [False, False, False, True, False]
    assert list(fired["any"]) == [False, True, True, True, False]


def test_project_rules_file_is_valid() -> None:
    rules = load_rules()
    assert {r.kind for r in rules} <= {"fixed", "user_stat", "unseen_hour"}
    assert "leitura_atipica" not in {r.name for r in rules}  # o tipo inédito não tem regra


def test_rule_without_feature_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - {name: x, kind: fixed, op: '>=', value: 1}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="feature"):
        load_rules(path)

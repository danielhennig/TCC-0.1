"""Baseline de regras simples (config/rules.yaml).

As estatísticas por usuário (p99, máximo, horas vistas) vêm só das janelas de treino e não
usam rótulos. Uma janela é sinalizada se qualquer regra disparar; a saída também diz quais
regras dispararam, para a análise de "o que o Isolation Forest pega que as regras não pegam".
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sacla.config import CONFIG_DIR, ConfigError, load_yaml

KINDS = ("fixed", "user_stat", "unseen_hour")
OPERATORS: dict[str, Callable[[Any, Any], Any]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}
STATS = ("p99", "max")


@dataclass(frozen=True)
class Rule:
    name: str
    kind: str
    feature: str | None = None
    op: str | None = None
    value: float | None = None
    stat: str | None = None
    multiplier: float = 1.0


@dataclass(frozen=True)
class RuleStats:
    """Estatísticas por usuário calculadas no treino."""

    per_user: dict[str, dict[str, float]]  # usuário -> "atributo:estatística" -> valor
    hours_seen: dict[str, frozenset[int]]


def load_rules(path: Path | None = None) -> list[Rule]:
    raw = load_yaml(path or CONFIG_DIR / "rules.yaml")
    rules = []
    try:
        for spec in raw["rules"]:
            rule = Rule(
                name=str(spec["name"]),
                kind=str(spec["kind"]),
                feature=spec.get("feature"),
                op=spec.get("op"),
                value=float(spec["value"]) if "value" in spec else None,
                stat=spec.get("stat"),
                multiplier=float(spec.get("multiplier", 1.0)),
            )
            if rule.kind not in KINDS:
                raise ConfigError(f"regra {rule.name}: kind deve ser um de {KINDS}")
            if rule.kind == "fixed" and (rule.op not in OPERATORS or rule.value is None):
                raise ConfigError(f"regra {rule.name}: op/value inválidos")
            if rule.kind in ("fixed", "user_stat") and not rule.feature:
                raise ConfigError(f"regra {rule.name}: feature obrigatório para kind {rule.kind}")
            if rule.kind == "user_stat" and rule.stat not in STATS:
                raise ConfigError(f"regra {rule.name}: stat deve ser um de {STATS}")
            rules.append(rule)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"rules.yaml malformado: {exc!r}") from exc
    return rules


def fit_rule_stats(train: pd.DataFrame, rules: list[Rule], local_hour: pd.Series) -> RuleStats:
    per_user: dict[str, dict[str, float]] = {}
    for user, frame in train.groupby("user"):
        values = {}
        for rule in rules:
            if rule.kind == "user_stat" and rule.feature and rule.stat:
                column = frame[rule.feature].astype(float)
                stat = column.quantile(0.99) if rule.stat == "p99" else column.max()
                values[f"{rule.feature}:{rule.stat}"] = float(stat)
        per_user[str(user)] = values
    hours = {
        str(user): frozenset(int(h) for h in hours_of_user)
        for user, hours_of_user in local_hour.loc[train.index].groupby(train["user"])
    }
    return RuleStats(per_user=per_user, hours_seen=hours)


def apply_rules(
    windows: pd.DataFrame, rules: list[Rule], stats: RuleStats, local_hour: pd.Series
) -> pd.DataFrame:
    """Uma coluna booleana por regra e a coluna `any`."""
    fired = pd.DataFrame(index=windows.index)
    users = windows["user"].astype(str)
    for rule in rules:
        if rule.kind == "fixed" and rule.feature and rule.op:
            fired[rule.name] = OPERATORS[rule.op](windows[rule.feature].astype(float), rule.value)
        elif rule.kind == "user_stat" and rule.feature and rule.stat:
            key = f"{rule.feature}:{rule.stat}"
            limit = users.map(lambda u, k=key: stats.per_user.get(u, {}).get(k, np.nan))
            # Usuário sem histórico: sem referência, a regra não dispara.
            fired[rule.name] = (
                windows[rule.feature].astype(float) > rule.multiplier * limit
            ).fillna(False)
        elif rule.kind == "unseen_hour":
            hours = local_hour.loc[windows.index]
            # Como nas regras por estatística: usuário sem histórico não tem referência.
            fired[rule.name] = [
                u in stats.hours_seen and int(h) not in stats.hours_seen[u]
                for u, h in zip(users, hours, strict=True)
            ]
    fired["any"] = fired[[r.name for r in rules]].any(axis=1)
    return fired.astype(bool)


def local_hours(windows: pd.DataFrame, timezone: str) -> pd.Series:
    local = pd.to_datetime(windows["window_start_ms"], unit="ms", utc=True).dt.tz_convert(timezone)
    return local.dt.hour.astype(int)

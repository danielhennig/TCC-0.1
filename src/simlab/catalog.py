"""Catálogo de ações SQL (config/queries.yaml) e sorteio dos seus parâmetros."""

from __future__ import annotations

import random
import re
import string
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sacla.config import CONFIG_DIR, ConfigError, load_yaml

PLACEHOLDER = re.compile(r"%\((\w+)\)s")
PARAM_KINDS = frozenset({"id", "int", "choice", "prefix", "month", "format", "context"})
TYPO_FROM, TYPO_TO = "SELECT", "SELCT"

IdPools = Mapping[str, list[int]]


@dataclass(frozen=True)
class Statement:
    """Um comando pronto para execução. Valores {"$capture": nome} vêm de comandos anteriores."""

    sql: str
    params: dict[str, Any] | None = None
    capture: str | None = None


@dataclass(frozen=True)
class StatementTemplate:
    sql: str
    capture: str | None


@dataclass(frozen=True)
class ActionTemplate:
    name: str
    statements: tuple[StatementTemplate, ...]
    params: dict[str, dict[str, Any]]
    transaction: bool


@dataclass(frozen=True)
class Catalog:
    actions: dict[str, ActionTemplate]
    id_pools: dict[str, tuple[str, str]]  # pool -> (tabela, coluna)

    def require(self, names: list[str] | tuple[str, ...], where: str) -> None:
        missing = sorted(set(names) - self.actions.keys())
        if missing:
            raise ConfigError(f"{where} referencia ações inexistentes em queries.yaml: {missing}")

    def render(
        self,
        name: str,
        rng: random.Random,
        ids: IdPools,
        context: Mapping[str, Any] | None = None,
        typo_probability: float = 0.0,
    ) -> list[Statement]:
        """Sorteia os parâmetros da ação e devolve seus comandos, na ordem.

        Com `typo_probability`, cada SELECT de uma ação sem transação pode virar um comando
        com erro de digitação (ruído legítimo: gera ERROR 42601 no log).
        """
        action = self.actions[name]
        values = {
            key: _sample(spec, rng, ids, context or {}) for key, spec in action.params.items()
        }
        statements = [Statement("BEGIN")] if action.transaction else []
        for template in action.statements:
            # Referências que não são parâmetros foram validadas como capturas anteriores.
            params: dict[str, Any] = {}
            for ref in PLACEHOLDER.findall(template.sql):
                params[ref] = values.get(ref, {"$capture": ref})
            sql = template.sql
            typo = typo_probability > 0 and not action.transaction and sql.startswith(TYPO_FROM)
            if typo and rng.random() < typo_probability:
                sql = TYPO_TO + sql[len(TYPO_FROM) :]
            statements.append(Statement(sql, params or None, template.capture))
        if action.transaction:
            statements.append(Statement("COMMIT"))
        return statements


def load_catalog(path: Path | None = None) -> Catalog:
    raw = load_yaml(path or CONFIG_DIR / "queries.yaml")
    try:
        id_pools = {str(k): (str(v[0]), str(v[1])) for k, v in raw["id_pools"].items()}
        actions = {
            str(n): _parse_action(str(n), spec, id_pools) for n, spec in raw["actions"].items()
        }
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ConfigError(f"queries.yaml malformado: {exc!r}") from exc
    return Catalog(actions=actions, id_pools=id_pools)


def _parse_action(
    name: str, spec: dict[str, Any], id_pools: dict[str, tuple[str, str]]
) -> ActionTemplate:
    params = {str(k): dict(v) for k, v in (spec.get("params") or {}).items()}
    for key, param in params.items():
        _validate_param(name, key, param, id_pools)
    statements = []
    captures: set[str] = set()
    for item in spec["statements"]:
        sql, capture = (item, None) if isinstance(item, str) else (item["sql"], item.get("capture"))
        sql = " ".join(str(sql).split())
        if "%" in PLACEHOLDER.sub("", sql):
            raise ConfigError(f"ação {name}: '%' literal no SQL (use um parâmetro): {sql}")
        for ref in PLACEHOLDER.findall(sql):
            if ref not in params and ref not in captures:
                raise ConfigError(f"ação {name}: %({ref})s não é parâmetro nem captura anterior")
        statements.append(StatementTemplate(sql=sql, capture=str(capture) if capture else None))
        if capture:
            captures.add(str(capture))
    if not statements:
        raise ConfigError(f"ação {name} sem comandos")
    return ActionTemplate(
        name=name,
        statements=tuple(statements),
        params=params,
        transaction=bool(spec.get("transaction", False)),
    )


def _validate_param(
    action: str, key: str, spec: dict[str, Any], id_pools: dict[str, tuple[str, str]]
) -> None:
    kinds = PARAM_KINDS & spec.keys()
    if len(kinds) != 1:
        raise ConfigError(f"ação {action}, parâmetro {key}: tipo inválido {spec}")
    if "id" in spec and spec["id"] not in id_pools:
        raise ConfigError(f"ação {action}, parâmetro {key}: pool desconhecido {spec['id']}")


def _sample(
    spec: dict[str, Any], rng: random.Random, ids: IdPools, context: Mapping[str, Any]
) -> Any:
    if "id" in spec:
        return rng.choice(ids[spec["id"]])
    if "int" in spec:
        low, high = spec["int"]
        return rng.randint(int(low), int(high))
    if "choice" in spec:
        return rng.choice(list(spec["choice"]))
    if "prefix" in spec:
        return rng.choice(string.ascii_uppercase) + "%"
    if "month" in spec:
        first, last = (_month_index(str(m)) for m in spec["month"])
        month = rng.randint(first, last)
        return f"{month // 12:04d}-{month % 12 + 1:02d}-01"
    if "format" in spec:
        low, high = spec["n"]
        return str(spec["format"]).format(n=rng.randint(int(low), int(high)))
    if "context" in spec:
        key = spec["context"]
        if key not in context:
            raise ConfigError(f"parâmetro de contexto '{key}' não fornecido pelo gerador")
        return context[key]
    raise ConfigError(f"parâmetro inválido: {spec}")


def _month_index(value: str) -> int:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match:
        raise ConfigError(f"mês inválido (esperado AAAA-MM): {value}")
    return int(match.group(1)) * 12 + int(match.group(2)) - 1

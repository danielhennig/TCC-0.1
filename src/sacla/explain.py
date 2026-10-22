"""Módulo 4a — explicação de cada alerta em relação ao histórico do próprio usuário.

Para cada atributo de `report.explain_features`, o valor da janela é comparado com as janelas
do mesmo usuário no período de treino: calcula-se a fração do histórico com valor maior ou
igual (cauda superior). Os atributos de menor cauda, isto é, os mais raros para aquele usuário,
são os mostrados no alerta. Só entram desvios para cima (mais comandos, mais linhas, hora mais
rara, mais tabelas inéditas...) e com cauda até `report.max_tail_fraction`.

É uma explicação descritiva (o que mais foge do normal do usuário), não a atribuição interna do
Isolation Forest: o modelo pode sinalizar uma combinação incomum de valores que, um a um, são
normais. Nesse caso o alerta diz isso explicitamente.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from sacla.config import ConfigError, ReportSettings
from sacla.features import FEATURE_LABELS

NO_HISTORY = "usuário sem histórico no período de treino"
NO_SINGLE_DEVIATION = (
    "nenhum atributo isolado fora do padrão do usuário; a combinação de valores é que é incomum"
)


@dataclass(frozen=True)
class UserReference:
    """Distribuição dos atributos de um usuário no período de treino."""

    windows: int
    values: dict[str, np.ndarray[Any, np.dtype[np.float64]]]  # ordenados, por atributo
    hour_counts: tuple[int, ...]  # 24 contagens de janelas por hora local
    tables: frozenset[str]
    ips: frozenset[str]


@dataclass(frozen=True)
class Deviation:
    feature: str
    value: float
    median: float
    maximum: float
    tail: float  # fração das janelas do histórico com valor >= value
    text: str


def fit_reference(history: pd.DataFrame, features: tuple[str, ...]) -> dict[str, UserReference]:
    """Referência por usuário a partir das janelas do treino (com `tables`, `ips` e hora local)."""
    missing = [f for f in (*features, "tables", "ips", "window_start_local") if f not in history]
    if missing:
        raise ConfigError(f"colunas ausentes para a referência dos usuários: {missing}")
    references = {}
    for user, frame in history.groupby("user", sort=True):
        hours = np.bincount(_local_hours(frame["window_start_local"]), minlength=24)
        references[str(user)] = UserReference(
            windows=len(frame),
            values={f: np.sort(frame[f].to_numpy(dtype=np.float64)) for f in features},
            hour_counts=tuple(int(h) for h in hours),
            tables=_split_union(frame["tables"]),
            ips=_split_union(frame["ips"]),
        )
    return references


def explain(
    row: Mapping[Any, Any], reference: UserReference | None, settings: ReportSettings
) -> list[Deviation]:
    """Até `top_features` atributos mais raros para o usuário, do mais raro ao menos raro."""
    if reference is None:
        return []
    found = []
    for feature in settings.explain_features:
        history = reference.values[feature]
        value = float(row[feature])
        median = float(np.median(history))
        if value <= median:
            continue
        tail = float(len(history) - np.searchsorted(history, value, side="left")) / len(history)
        if tail > settings.max_tail_fraction:
            continue
        maximum = float(history[-1])
        found.append(
            Deviation(
                feature,
                value,
                median,
                maximum,
                tail,
                _text(feature, row, value, median, maximum, tail, reference),
            )
        )
    # Mais raro primeiro; entre iguais (ex.: acima do máximo), o que mais passa do máximo.
    found.sort(key=lambda d: (d.tail, -(d.value - d.maximum) / max(abs(d.maximum), 1.0)))
    return found[: settings.top_features]


def describe(deviations: list[Deviation], reference: UserReference | None) -> list[str]:
    """Frases do alerta (sempre ao menos uma)."""
    if reference is None:
        return [NO_HISTORY]
    if not deviations:
        return [NO_SINGLE_DEVIATION]
    return [d.text for d in deviations]


def format_decimal(value: float, digits: int) -> str:
    """Número com casas fixas e vírgula decimal: 0,5547."""
    return f"{value:.{digits}f}".replace(".", ",")


def format_number(value: float) -> str:
    """Número no formato brasileiro: 51.805 ou 3,5."""
    if math.isfinite(value) and float(value).is_integer():
        return f"{int(value):,}".replace(",", ".")
    return f"{value:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _text(
    feature: str,
    row: Mapping[Any, Any],
    value: float,
    median: float,
    maximum: float,
    tail: float,
    reference: UserReference,
) -> str:
    if feature == "hour_surprisal":
        hour = _local_hour(str(row["window_start_local"]))
        share = reference.hour_counts[hour] / reference.windows
        return (
            f"horário incomum para o usuário: {hour:02d}h ({_pct(share)} das janelas do histórico)"
        )
    if feature == "weekend":
        share = float(reference.values["weekend"].mean()) if "weekend" in reference.values else 0.0
        return f"atividade no fim de semana ({_pct(share)} das janelas do histórico)"
    if feature == "new_tables_frac":
        new = sorted(_split(row["tables"]) - reference.tables)
        return f"tabelas nunca acessadas pelo usuário: {', '.join(new)}"
    if feature == "new_ips_frac":
        new = sorted(_split(row["ips"]) - reference.ips)
        return f"IP de origem nunca usado pelo usuário: {', '.join(new)}"
    if feature == "new_query_shapes_frac":
        return f"formatos de consulta inéditos: {_pct(value)} dos formatos da janela"
    position = (
        "acima do máximo do histórico"
        if value > maximum
        else f"atingido em só {_pct(tail)} das janelas do histórico"
    )
    return (
        f"{FEATURE_LABELS[feature]}: {format_number(value)} ({position}; "
        f"mediana {format_number(median)}, máximo {format_number(maximum)})"
    )


def _pct(share: float) -> str:
    if 0 < share < 0.001:
        return "<0,1%"
    return f"{100 * share:.1f}%".replace(".", ",")


def _local_hour(stamp: str) -> int:
    """'2026-03-23T03:05-0300' -> 3 (hora local gravada pelo módulo de atributos)."""
    return int(stamp[11:13])


def _local_hours(stamps: pd.Series) -> np.ndarray[Any, np.dtype[np.int64]]:
    return np.array([_local_hour(str(s)) for s in stamps], dtype=np.int64)


def _split(value: Any) -> frozenset[str]:
    return frozenset(part for part in str(value or "").split("|") if part)


def _split_union(values: pd.Series) -> frozenset[str]:
    result: set[str] = set()
    for value in values:
        result |= _split(value)
    return frozenset(result)

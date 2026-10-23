"""Estatística da validação confirmatória (Fase 7; pré-registro em docs/decisoes.md, D52).

- Intervalos de confiança por bootstrap percentil, estratificado por simulação. A unidade é o
  bloco usuário × dia local para as métricas por janela (janelas vizinhas não são
  independentes) e o cenário para os cenários detectados.
- Hipóteses: McNemar exato sobre cenários detectados e permutação pareada por blocos sobre o
  F1, com correção de Holm.

As funções recebem arrays prontos; quem os monta a partir das simulações é
`evaluation.confirmatory`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.stats import binomtest
from scipy.stats import t as student_t

from sacla.config import ConfigError

Array = np.ndarray[Any, Any]

WINDOW_METRICS = ("precision", "recall", "f1")
METRICS = (*WINDOW_METRICS, "scenarios")
SUBSETS = ("all", "novel")  # todos os cenários ou só os do tipo inédito
# Permutações processadas por vez (limita a memória da matriz de trocas).
PERMUTATION_CHUNK = 1000
# Folga numérica ao comparar |Δ*| com |Δ| (diferenças iguais podem diferir no último bit).
TIE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class Comparison:
    """Detector `a` contra `b` numa métrica; `id` só nas hipóteses (H1, H2...)."""

    metric: str
    subset: str
    a: str
    b: str
    id: str = ""


@dataclass(frozen=True)
class StatsConfig:
    confidence: float
    alpha: float
    bootstrap_resamples: int
    permutation_resamples: int
    seed: int
    majority: float
    hypotheses: tuple[Comparison, ...]
    descriptive: tuple[Comparison, ...]


def load_stats_config(raw: dict[str, Any], detectors: Sequence[str]) -> StatsConfig:
    """Seção `stats` de config/model.yaml, conferida contra os detectores conhecidos."""
    try:
        section = raw["stats"]
        config = StatsConfig(
            confidence=float(section["confidence"]),
            alpha=float(section["alpha"]),
            bootstrap_resamples=int(section["bootstrap_resamples"]),
            permutation_resamples=int(section["permutation_resamples"]),
            seed=int(section["seed"]),
            majority=float(section["majority"]),
            hypotheses=tuple(_comparison(item) for item in section["hypotheses"]),
            descriptive=tuple(_comparison(item) for item in section["descriptive"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"model.yaml: seção stats malformada ({exc!r})") from exc
    if not 0 < config.confidence < 1 or not 0 < config.alpha < 1 or not 0 < config.majority <= 1:
        raise ConfigError("stats: confidence, alpha e majority devem estar em (0, 1]")
    for item in (*config.hypotheses, *config.descriptive):
        if item.metric not in METRICS or item.subset not in SUBSETS:
            raise ConfigError(f"stats: métrica ou subconjunto inválido em {item}")
        if item.metric in WINDOW_METRICS and item.subset != "all":
            raise ConfigError(f"stats: métricas por janela só com subset all ({item})")
        if item.a not in detectors or item.b not in detectors or item.a == item.b:
            raise ConfigError(f"stats: detectores inválidos em {item}")
    ids = [h.id for h in config.hypotheses]
    if any(not i for i in ids) or len(set(ids)) != len(ids):
        raise ConfigError("stats: cada hipótese precisa de um id único")
    return config


def _comparison(item: dict[str, Any]) -> Comparison:
    return Comparison(
        metric=str(item["metric"]),
        subset=str(item["subset"]),
        a=str(item["a"]),
        b=str(item["b"]),
        id=str(item.get("id", "")),
    )


# --- Métricas por janela a partir de contagens por bloco -------------------------------------


def block_counts(labels: Array, blocks: Array, n_blocks: int, flags: Array) -> Array:
    """Acertos (tp), falsos positivos (fp) e falsos negativos (fn) por bloco e semente.

    labels: (janelas,); blocks: índice do bloco de cada janela; flags: (janelas, sementes).
    Saída: (blocos, sementes, 3).
    """
    y = np.asarray(labels).astype(bool)[:, None]
    f = np.asarray(flags).astype(bool)
    counts = np.zeros((n_blocks, f.shape[1], 3))
    for k, part in enumerate((f & y, f & ~y, ~f & y)):
        np.add.at(counts[:, :, k], np.asarray(blocks), part)
    return counts


def window_metric(totals: Array, metric: str) -> Array:
    """Métrica a partir de (..., sementes, 3) contagens; média nas sementes."""
    tp, fp, fn = totals[..., 0], totals[..., 1], totals[..., 2]
    if metric == "precision":
        num, den = tp, tp + fp
    elif metric == "recall":
        num, den = tp, tp + fn
    elif metric == "f1":
        num, den = 2 * tp, 2 * tp + fp + fn
    else:
        raise ValueError(f"métrica por janela desconhecida: {metric}")
    value = np.divide(num, den, out=np.zeros_like(num, dtype=float), where=den > 0)
    return np.asarray(value.mean(axis=-1), dtype=float)


# --- Bootstrap -------------------------------------------------------------------------------


def stratified_weights(strata: Array, resamples: int, rng: np.random.Generator) -> Array:
    """Quantas vezes cada unidade entra em cada reamostragem (reamostragens × unidades).

    Dentro de cada estrato (simulação), sorteia com reposição tantas unidades quantas ele tem:
    o tamanho de cada estrato fica igual em todas as reamostragens.
    """
    strata = np.asarray(strata)
    weights = np.zeros((resamples, len(strata)))
    for stratum in np.unique(strata):
        members = np.flatnonzero(strata == stratum)
        size = len(members)
        weights[:, members] = rng.multinomial(size, np.full(size, 1 / size), size=resamples)
    return weights


def percentile_interval(samples: Array, confidence: float) -> tuple[float, float]:
    tail = 100 * (1 - confidence) / 2
    low, high = np.percentile(np.asarray(samples, dtype=float), [tail, 100 - tail])
    return float(low), float(high)


def window_samples(weights: Array, counts: Array, metric: str) -> Array:
    """Métrica em cada reamostragem de blocos: (reamostragens,)."""
    totals = np.einsum("rb,bsk->rsk", weights, counts)
    return window_metric(totals, metric)


def scenario_samples(weights: Array, rates: Array) -> Array:
    """Fração de cenários detectados em cada reamostragem de cenários.

    rates: por cenário, a fração das sementes que o detectam (0 ou 1 para as regras). A média
    disso é a média, nas sementes, da fração de cenários detectados.
    """
    return np.asarray(weights @ np.asarray(rates, dtype=float) / weights.sum(axis=1))


# --- Testes ----------------------------------------------------------------------------------


def mcnemar_exact(a: Array, b: Array) -> dict[str, float]:
    """McNemar exato (binomial bicaudal nos pares discordantes)."""
    a, b = np.asarray(a).astype(bool), np.asarray(b).astype(bool)
    only_a, only_b = int((a & ~b).sum()), int((~a & b).sum())
    discordant = only_a + only_b
    p = float(binomtest(only_a, discordant, 0.5).pvalue) if discordant else 1.0
    return {"only_a": only_a, "only_b": only_b, "p": p}


def paired_permutation(
    counts_a: Array, counts_b: Array, metric: str, resamples: int, rng: np.random.Generator
) -> dict[str, float]:
    """Permutação pareada por blocos para a diferença de uma métrica por janela (a − b).

    Sob a hipótese nula, as saídas dos dois detectores são trocáveis dentro de cada bloco: cada
    permutação troca, ou não, com probabilidade 1/2, os dois detectores em cada bloco. Um
    detector com uma semente só (regras) é repetido para todas as sementes do outro.
    p = (1 + nº de |Δ*| ≥ |Δ|) / (permutações + 1).
    """
    seeds = max(counts_a.shape[1], counts_b.shape[1])
    shape = (counts_a.shape[0], seeds, 3)
    a = np.broadcast_to(counts_a, shape)
    b = np.broadcast_to(counts_b, shape)
    total_a, total_b = a.sum(axis=0), b.sum(axis=0)
    observed = float(window_metric(total_a, metric) - window_metric(total_b, metric))
    gap = b - a
    extreme = 0
    done = 0
    while done < resamples:
        size = min(PERMUTATION_CHUNK, resamples - done)
        swaps = rng.integers(0, 2, size=(size, shape[0])).astype(float)
        shift = np.einsum("rb,bsk->rsk", swaps, gap)
        permuted = window_metric(total_a + shift, metric) - window_metric(total_b - shift, metric)
        extreme += int(np.sum(np.abs(permuted) >= abs(observed) - TIE_TOLERANCE))
        done += size
    return {"diff": observed, "p": (1 + extreme) / (resamples + 1)}


def holm(pvalues: Sequence[float]) -> list[float]:
    """p-valores ajustados por Holm (step-down), na ordem recebida."""
    p = np.asarray(pvalues, dtype=float)
    m = len(p)
    adjusted = np.empty(m)
    running = 0.0
    for rank, index in enumerate(np.argsort(p, kind="stable")):
        running = max(running, min(1.0, (m - rank) * p[index]))
        adjusted[index] = running
    return [float(v) for v in adjusted]


def mean_interval(values: Array, confidence: float) -> tuple[float, float, float]:
    """Média e intervalo t de Student (réplicas independentes, ex.: simulações)."""
    x = np.asarray(values, dtype=float)
    mean = float(x.mean())
    if len(x) < 2:
        return mean, float("nan"), float("nan")
    half = float(student_t.ppf((1 + confidence) / 2, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x)))
    return mean, mean - half, mean + half

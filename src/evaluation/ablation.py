"""Ablação de grupos de atributos (Fase 7; pré-registro em docs/decisoes.md, D52; exploratória).

Cada grupo de `ablation.groups` (config/model.yaml) sai do Isolation Forest, um de cada vez,
com os hiperparâmetros congelados do experimento. Mede, na validação de cada simulação:
F1 com o melhor limiar, PR-AUC e cenários detectados nesse limiar, e a variação em relação ao
conjunto completo. O grupo do `auto_explain` também é medido nas regras (sem as regras que usam
linhas) e na união IF ∪ regras.

O teste das simulações não é usado: as linhas de teste são descartadas logo depois da leitura,
e `ablate_run` recusa qualquer linha que não seja de treino ou de validação.
"""

from __future__ import annotations

import logging
from collections.abc import Hashable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluation.experiment import DETECTORS, load_split
from evaluation.metrics import (
    best_f1_threshold,
    binary_metrics,
    ranking_metrics,
    scenario_detection,
)
from evaluation.robustness import NAMES, frozen_setup
from evaluation.rules_baseline import Rule, apply_rules, fit_rule_stats, load_rules, local_hours
from evaluation.stats import load_stats_config, mean_interval
from evaluation.tuning import ModelConfig, load_model_config
from evaluation.validation_plots import ablation_figures
from sacla.config import PROJECT_ROOT, ConfigError, SaclaSettings
from sacla.features import FEATURE_COLUMNS
from sacla.model import ModelParams, SaclaModel

log = logging.getLogger(__name__)

FULL = "completo"  # variante com todos os atributos (e todas as regras)
MEASURES = ("f1", "pr_auc", "scenario_recall")
MEASURE_NAMES = {"f1": "F1", "pr_auc": "PR-AUC", "scenario_recall": "cenários detectados"}
GROUP_NAMES = {
    "relativos": "relativos ao histórico",
    "novidade": "novidade",
    "horario": "horário",
    "origem": "origem",
    "sem_auto_explain": "sem auto_explain",
}


@dataclass(frozen=True)
class AblationConfig:
    results_dir: str
    groups: dict[str, tuple[str, ...]]
    auto_explain_group: str
    auto_explain_rules: tuple[str, ...]


def load_ablation_config(raw: dict[str, Any], rules: list[Rule]) -> AblationConfig:
    try:
        section = raw["ablation"]
        config = AblationConfig(
            results_dir=str(section["results_dir"]),
            groups={str(k): tuple(str(f) for f in v) for k, v in section["groups"].items()},
            auto_explain_group=str(section["auto_explain_group"]),
            auto_explain_rules=tuple(str(r) for r in section["auto_explain_rules"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"model.yaml: seção ablation malformada ({exc!r})") from exc
    for name, group in config.groups.items():
        unknown = sorted(set(group) - set(FEATURE_COLUMNS))
        if unknown or not group:
            raise ConfigError(
                f"ablation: grupo {name} vazio ou com atributos desconhecidos {unknown}"
            )
        if name == FULL:
            raise ConfigError(f"ablation: '{FULL}' é reservado para o conjunto completo")
    if config.auto_explain_group not in config.groups:
        raise ConfigError("ablation: auto_explain_group precisa ser um dos grupos")
    unknown_rules = sorted(set(config.auto_explain_rules) - {r.name for r in rules})
    if unknown_rules:
        raise ConfigError(f"ablation: regras desconhecidas {unknown_rules}")
    return config


def variants(config: AblationConfig) -> list[tuple[str, tuple[str, ...]]]:
    """(nome, atributos usados): o conjunto completo e um sem cada grupo."""
    return [(FULL, FEATURE_COLUMNS)] + [
        (name, tuple(c for c in FEATURE_COLUMNS if c not in set(group)))
        for name, group in config.groups.items()
    ]


def ablate_run(
    run_id: str,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    manifest: pd.DataFrame,
    params: ModelParams,
    model_config: ModelConfig,
    ablation: AblationConfig,
    rules: list[Rule],
    timezone: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Medidas na validação: uma linha por variante × detector × semente de ajuste e, à parte,
    os cenários detectados por tipo."""
    if not train["split"].eq("train").all() or not validation["split"].eq("validation").all():
        raise ValueError("a ablação só usa janelas de treino e de validação")
    labels = validation["label"].to_numpy()
    train_hours = local_hours(train, timezone)
    validation_hours = local_hours(validation, timezone)
    reduced = [r for r in rules if r.name not in set(ablation.auto_explain_rules)]
    rule_flags = {
        variant: apply_rules(
            validation, chosen, fit_rule_stats(train, chosen, train_hours), validation_hours
        )["any"].to_numpy()
        for variant, chosen in ((FULL, rules), (ablation.auto_explain_group, reduced))
    }
    rows: list[dict[str, Any]] = []
    types: list[pd.DataFrame] = []

    def measure(
        variant: str, detector: str, seed: int, flags: np.ndarray[Any, Any], pr_auc: float
    ) -> None:
        key: dict[str, Any] = {
            "run_id": run_id,
            "variant": variant,
            "detector": detector,
            "seed": seed,
        }
        detection = scenario_detection(validation, flags, manifest)
        metrics = binary_metrics(labels, flags)
        rows.append(
            {
                **key,
                **{m: metrics[m] for m in ("f1", "precision", "recall", "alerts")},
                "pr_auc": pr_auc,
                "scenario_recall": float(detection["detected"].mean()),
            }
        )
        by_type = detection.groupby("type")["detected"].agg(scenarios="size", detected="sum")
        types.append(by_type.reset_index().assign(**key))

    for variant, features in variants(ablation):
        for seed in model_config.tuning_seeds:
            model = SaclaModel.fit(
                train,
                params,
                seed,
                model_config.log1p_features,
                model_config.n_jobs,
                features=features,
            )
            scores = model.score(validation)
            flags = scores >= best_f1_threshold(labels, scores)["threshold"]
            pr_auc = ranking_metrics(labels, scores)["pr_auc"]
            measure(variant, "isolation_forest", seed, flags, pr_auc)
            if variant in rule_flags:
                measure(variant, "if_mais_regras", seed, flags | rule_flags[variant], float("nan"))
    for variant, flags in rule_flags.items():
        measure(variant, "regras", 0, flags, float("nan"))
    return pd.DataFrame(rows), pd.concat(types, ignore_index=True)


def type_recall(types: pd.DataFrame) -> pd.DataFrame:
    """Cenários detectados por tipo: soma nas simulações, média nas sementes."""
    totals = types.groupby(["variant", "detector", "type", "seed"], sort=False)[
        ["scenarios", "detected"]
    ].sum()
    totals["recall"] = totals["detected"] / totals["scenarios"]
    table = totals.groupby(["variant", "detector", "type"], sort=False).agg(
        scenarios=("scenarios", "first"), recall=("recall", "mean")
    )
    return table.reset_index()


def summarize(per_seed: pd.DataFrame, confidence: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Média por simulação (nas sementes) e, entre simulações, a variação contra o completo."""
    by_run = per_seed.groupby(["run_id", "variant", "detector"], sort=False)[list(MEASURES)].mean()
    by_run = by_run.reset_index()
    full = by_run[by_run["variant"] == FULL].drop(columns="variant")
    by_run = by_run.merge(full, on=["run_id", "detector"], suffixes=("", "_completo"))
    for measure in MEASURES:
        by_run[f"delta_{measure}"] = by_run[measure] - by_run[f"{measure}_completo"]
    rows = []
    for (variant, detector), frame in by_run.groupby(["variant", "detector"], sort=False):
        row: dict[str, Any] = {"variant": variant, "detector": detector, "runs": len(frame)}
        for measure in MEASURES:
            row[f"{measure}_mean"] = float(frame[measure].mean())
            mean, low, high = mean_interval(frame[f"delta_{measure}"].to_numpy(), confidence)
            row[f"delta_{measure}_mean"] = mean
            row[f"delta_{measure}_low"] = low
            row[f"delta_{measure}_high"] = high
            row[f"delta_{measure}_worse_runs"] = int((frame[f"delta_{measure}"] < 0).sum())
        rows.append(row)
    return by_run, pd.DataFrame(rows)


def run_ablation(
    exp_dir: Path,
    run_ids: list[str],
    data_root: Path,
    features_root: Path,
    settings: SaclaSettings,
) -> Path:
    params, window, _ = frozen_setup(exp_dir)
    config = load_model_config(settings=settings)
    rules = load_rules()
    ablation = load_ablation_config(config.raw, rules)
    confidence = load_stats_config(config.raw, DETECTORS).confidence
    frames, type_frames = [], []
    for run_id in run_ids:
        run_dir = data_root / run_id
        data, _ = load_split(features_root / run_id, run_dir, settings, window)
        data = data[data["split"] != "test"]  # o teste não entra na ablação
        log.info("ablação: %s", run_id)
        rows, types = ablate_run(
            run_id,
            data[data["split"] == "train"],
            data[data["split"] == "validation"],
            pd.read_csv(run_dir / "manifest.csv"),
            params,
            config,
            ablation,
            rules,
            settings.features.timezone,
        )
        frames.append(rows)
        type_frames.append(types)
    per_seed = pd.concat(frames, ignore_index=True)
    by_run, summary = summarize(per_seed, confidence)
    by_type = type_recall(pd.concat(type_frames, ignore_index=True))
    out = PROJECT_ROOT / ablation.results_dir / exp_dir.name
    out.mkdir(parents=True, exist_ok=True)
    for name, table in (
        ("ablation_seeds", per_seed),
        ("ablation_runs", by_run),
        ("ablation_summary", summary),
        ("ablation_types", by_type),
    ):
        table.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    report = out / "ablacao.md"
    report.write_text(
        markdown(
            params.slug(), summary, by_type, ablation, run_ids, confidence, len(config.tuning_seeds)
        ),
        encoding="utf-8",
    )
    ablation_figures(out, by_run, summary, GROUP_NAMES, ablation.auto_explain_group, config)
    log.info("resultados em %s", out.relative_to(PROJECT_ROOT))
    return report


def _delta(row: dict[Hashable, Any], measure: str) -> str:
    scale = 1 if measure == "pr_auc" else 100
    digits = 3 if measure == "pr_auc" else 1
    mean, low, high = (row[f"delta_{measure}_{k}"] * scale for k in ("mean", "low", "high"))
    return f"{mean:+.{digits}f} [{low:+.{digits}f}; {high:+.{digits}f}]"


def markdown(
    slug: str,
    summary: pd.DataFrame,
    by_type: pd.DataFrame,
    ablation: AblationConfig,
    run_ids: list[str],
    confidence: float,
    seeds: int,
) -> str:
    records = summary.to_dict(orient="records")
    full = {r["detector"]: r for r in records if r["variant"] == FULL}
    lines = [
        f"# Ablação de atributos na validação (configuração congelada `{slug}`)",
        "",
        f"Exploratória (D52). {len(run_ids)} simulações ({', '.join(run_ids)}); em cada uma, "
        f"média de {seeds} sementes de ajuste. Δ = variante − completo, em pontos percentuais "
        f"(PR-AUC em unidades); intervalo t de {100 * confidence:.0f}% entre simulações. Só "
        "treino e validação; o teste não é usado.",
        "",
        "## Isolation Forest sem cada grupo",
        "",
        f"Completo: F1 {100 * full['isolation_forest']['f1_mean']:.1f}, PR-AUC "
        f"{full['isolation_forest']['pr_auc_mean']:.3f}, cenários detectados "
        f"{100 * full['isolation_forest']['scenario_recall_mean']:.1f}.",
        "",
        "| Grupo retirado | Atributos | Δ F1 | Δ PR-AUC | Δ cenários | Pior em (F1) |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in records:
        if row["detector"] != "isolation_forest" or row["variant"] == FULL:
            continue
        features = ", ".join(f"`{f}`" for f in ablation.groups[row["variant"]])
        lines.append(
            f"| {GROUP_NAMES.get(row['variant'], row['variant'])} | {features} | "
            f"{_delta(row, 'f1')} | {_delta(row, 'pr_auc')} | {_delta(row, 'scenario_recall')} | "
            f"{row['delta_f1_worse_runs']} de {row['runs']} |"
        )
    group = ablation.auto_explain_group
    lines += [
        "",
        "## Sem auto_explain: o sistema inteiro",
        "",
        f"Regras sem {', '.join(f'`{r}`' for r in ablation.auto_explain_rules)}; IF sem o grupo "
        f"`{group}`; união das duas versões reduzidas.",
        "",
        "| Detector | F1 completo | F1 sem auto_explain | Δ F1 | Cenários completo | "
        "Cenários sem auto_explain | Δ cenários |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for detector in ("isolation_forest", "regras", "if_mais_regras"):
        reduced = next(r for r in records if r["variant"] == group and r["detector"] == detector)
        lines.append(
            f"| {NAMES[detector]} | {100 * full[detector]['f1_mean']:.1f} | "
            f"{100 * reduced['f1_mean']:.1f} | {_delta(reduced, 'f1')} | "
            f"{100 * full[detector]['scenario_recall_mean']:.1f} | "
            f"{100 * reduced['scenario_recall_mean']:.1f} | {_delta(reduced, 'scenario_recall')} |"
        )
    detectors = ("isolation_forest", "regras", "if_mais_regras")
    recall = by_type.set_index(["variant", "detector", "type"])["recall"]
    lines += [
        "",
        "### Cenários detectados por tipo, completo → sem auto_explain (%)",
        "",
        "Todos os cenários de validação das simulações; média nas sementes de ajuste.",
        "",
        "| Tipo | Cenários | " + " | ".join(NAMES[d] for d in detectors) + " |",
        "|---|---:|" + "---:|" * len(detectors),
    ]
    full_types = by_type[(by_type["variant"] == FULL) & (by_type["detector"] == "regras")]
    for kind, scenarios in full_types[["type", "scenarios"]].itertuples(index=False):
        cells = [
            f"{100 * recall[(FULL, d, kind)]:.1f} → {100 * recall[(group, d, kind)]:.1f}"
            for d in detectors
        ]
        lines.append(f"| {kind} | {scenarios} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)

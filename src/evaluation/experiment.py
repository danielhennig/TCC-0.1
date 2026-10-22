"""Experimento completo da Fase 4 sobre uma execução da simulação.

1. Ajuste na validação (grade × sementes de ajuste) e escolha da configuração.
2. Avaliação única no teste: a configuração escolhida, repetida com as sementes de avaliação;
   o limiar de cada semente vem da validação.
3. Configuração sem rótulos (padrões do scikit-learn, limiar por quantil do treino).
4. Baseline de regras e combinação Isolation Forest ∪ regras.
5. Sensibilidade (validação): hiperparâmetros e tamanho da janela.
Saída: results/experimentos/<exp_id>/ (métricas, tabelas, scores, configuração e ambiente).
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import platform
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from evaluation.dataset import build_dataset, dataset_name, run_fingerprint
from evaluation.metrics import (
    best_f1_threshold,
    binary_metrics,
    ranking_metrics,
    recall_by_type,
    scenario_detection,
)
from evaluation.rules_baseline import Rule, apply_rules, fit_rule_stats, load_rules, local_hours
from evaluation.sensitivity import hyperparameter_sensitivity, window_sensitivity
from evaluation.tuning import ModelConfig, load_model_config, tune
from sacla.config import CONFIG_DIR, PROJECT_ROOT, SaclaSettings
from sacla.model import ModelParams, SaclaModel

log = logging.getLogger(__name__)

ID_COLUMNS = [
    "user",
    "window_start_ms",
    "window_end_ms",
    "window_start_local",
    "split",
    "label",
    "anomaly_type",
    "intensity",
    "novel",
    "scenario_ids",
]
PACKAGES = ("numpy", "pandas", "scikit-learn", "pyarrow", "psycopg", "PyYAML")
SUMMARY_METRICS = ("precision", "recall", "f1", "fp", "fp_rate", "alerts")


@dataclass
class Evaluation:
    """Resultado de um detector no teste, por semente (ou uma linha só, para as regras)."""

    per_seed: pd.DataFrame
    by_type: pd.DataFrame  # recall por janela, por semente
    scenarios: pd.DataFrame  # detecção por cenário, por semente
    scores: dict[int, np.ndarray[Any, Any]] = field(default_factory=dict)
    flags: dict[int, np.ndarray[Any, Any]] = field(default_factory=dict)
    first_model: SaclaModel | None = None  # modelo da primeira semente, com o limiar usado


def load_split(
    features_dir: Path,
    run_dir: Path,
    settings: SaclaSettings,
    window: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Conjunto rotulado da janela pedida; monta-o se ainda não existir."""
    name = dataset_name(window)
    path = features_dir / f"{name}.parquet"
    summary_path = features_dir / f"{name}_summary.json"
    current = run_fingerprint(run_dir)
    stale = (
        not path.exists()
        or not summary_path.exists()
        or json.loads(summary_path.read_text(encoding="utf-8")).get("run_fingerprint") != current
    )
    if stale:
        log.info("conjunto %s ausente ou de outra execução da simulação; montando", name)
        build_dataset(run_dir, features_dir, settings, window)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return pd.read_parquet(path), summary


def run_experiment(
    run_dir: Path,
    features_dir: Path,
    settings: SaclaSettings,
    window: int | None = None,
    config: ModelConfig | None = None,
    rules: list[Rule] | None = None,
) -> Path:
    config = config or load_model_config()
    rules = rules if rules is not None else load_rules()
    window = window or settings.features.window_minutes
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    manifest = pd.read_csv(run_dir / "manifest.csv")
    data, dataset_summary = load_split(features_dir, run_dir, settings, window)
    train, validation, test = (data[data["split"] == s] for s in ("train", "validation", "test"))
    log.info("janelas: treino %d, validação %d, teste %d", len(train), len(validation), len(test))

    started = time.perf_counter()
    tuning_runs, tuning_summary, best = tune(train, validation, config)
    tuning_s = time.perf_counter() - started
    log.info("configuração escolhida na validação: %s", best.slug())

    tuned = evaluate_detector(train, validation, test, best, config, manifest, "validation")
    label_free = evaluate_detector(
        train, validation, test, config.label_free, config, manifest, "train_quantile"
    )
    hours = local_hours(data, settings.features.timezone)
    rule_stats = fit_rule_stats(train, rules, hours)
    fired = apply_rules(test, rules, rule_stats, hours)
    rules_eval = evaluate_flags(test, fired["any"].to_numpy(), manifest)
    union = evaluate_union(test, tuned, fired["any"].to_numpy(), manifest)

    hyper = hyperparameter_sensitivity(tuning_summary, best)
    datasets = {
        w: data if w == window else load_split(features_dir, run_dir, settings, w)[0]
        for w in config.sensitivity_windows
    }
    windows_table, _ = window_sensitivity(
        datasets, manifest, config, already_tuned={window: (tuning_summary, best)}
    )

    exp_id = f"{run['run_id']}__{dataset_name(window)}__{best.slug()}"
    out = PROJECT_ROOT / config.results_dir / exp_id
    if out.exists():
        # Só a saída deste comando; a robustez fica em outra pasta (robustness.results_dir).
        shutil.rmtree(out)
    (out / "tables").mkdir(parents=True)
    (out / "scores").mkdir()

    comparison = comparison_table(tuned, label_free, rules_eval, union)
    by_type = by_type_table(tuned, label_free, rules_eval, union)
    scenarios = scenario_table(tuned, label_free, rules_eval, union)
    overlap = overlap_table(tuned, rules_eval)
    firing = rule_firing_table(test, fired, rules)
    timings = timing_summary(dataset_summary, tuned, tuning_s)

    tables = {
        "tuning_runs": tuning_runs,
        "tuning_grid": tuning_summary,
        "test_seeds": tuned.per_seed,
        "test_label_free_seeds": label_free.per_seed,
        "test_comparison": comparison,
        "test_recall_by_type": by_type,
        "test_scenarios_by_type": scenarios,
        "test_scenario_overlap": overlap,
        "test_rule_firing": firing,
        "sensitivity_hyperparameters": hyper,
        "sensitivity_window": windows_table,
    }
    for name, table in tables.items():
        table.to_csv(out / "tables" / f"{name}.csv", index=False, lineterminator="\n")
    _save_scores(out, data, test, tuned, label_free, fired, config)

    metrics = {
        "exp_id": exp_id,
        "run_id": run["run_id"],
        "window_minutes": window,
        "chosen_params": best.as_dict(),
        "selection": {"metric": config.selection_metric, "tuning_seeds": list(config.tuning_seeds)},
        "validation_best": tuning_summary[tuning_summary["config"] == best.slug()]
        .iloc[0][["f1_mean", "f1_std", "pr_auc_mean", "roc_auc_mean"]]
        .to_dict(),
        "contamination": dataset_summary["windows_by_split"],
        "test": comparison.to_dict(orient="records"),
        "timings": timings,
    }
    (out / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False, default=float) + "\n", encoding="utf-8"
    )
    _write_config(out, config, run, dataset_summary)
    _write_env(out)
    (out / "resumo.md").write_text(
        summary_markdown(metrics, comparison, by_type, scenarios, overlap, hyper, windows_table),
        encoding="utf-8",
    )
    log.info("resultados em %s", out.relative_to(PROJECT_ROOT))
    return out


# --- Avaliação dos detectores ----------------------------------------------------------------


def evaluate_detector(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    test: pd.DataFrame,
    params: ModelParams,
    config: ModelConfig,
    manifest: pd.DataFrame,
    threshold_from: str,
) -> Evaluation:
    """Treina com cada semente de avaliação e aplica ao teste com o limiar indicado.

    threshold_from: "validation" (melhor F1 na validação) ou "train_quantile" (quantil dos
    scores do próprio treino, sem rótulos).
    """
    rows, types, scenarios = [], [], []
    scores_by_seed, flags_by_seed = {}, {}
    first_model: SaclaModel | None = None
    labels = test["label"].to_numpy()
    for seed in config.evaluation_seeds:
        started = time.perf_counter()
        model = SaclaModel.fit(train, params, seed, config.log1p_features, config.n_jobs)
        fit_s = time.perf_counter() - started
        if threshold_from == "validation":
            threshold = best_f1_threshold(validation["label"].to_numpy(), model.score(validation))[
                "threshold"
            ]
        else:
            threshold = float(np.quantile(model.score(train), config.label_free_quantile))
        started = time.perf_counter()
        scores = model.score(test)
        score_s = time.perf_counter() - started
        flags = scores >= threshold
        rows.append(
            {
                "seed": seed,
                "threshold": threshold,
                **binary_metrics(labels, flags),
                **ranking_metrics(labels, scores),
                "fit_s": fit_s,
                "score_s": score_s,
                "scenario_recall": float(
                    scenario_detection(test, flags, manifest)["detected"].mean()
                ),
            }
        )
        types.append(recall_by_type(test, flags).assign(seed=seed))
        scenarios.append(scenario_detection(test, flags, manifest).assign(seed=seed))
        scores_by_seed[seed], flags_by_seed[seed] = scores, flags
        if first_model is None:
            first_model = model.with_threshold(threshold)
    return Evaluation(
        pd.DataFrame(rows),
        pd.concat(types),
        pd.concat(scenarios),
        scores_by_seed,
        flags_by_seed,
        first_model,
    )


def evaluate_flags(
    test: pd.DataFrame, flags: np.ndarray[Any, Any], manifest: pd.DataFrame
) -> Evaluation:
    """Detector determinístico (regras): uma "semente" só."""
    detection = scenario_detection(test, flags, manifest)
    row = {
        "seed": 0,
        **binary_metrics(test["label"].to_numpy(), flags),
        "scenario_recall": float(detection["detected"].mean()),
    }
    return Evaluation(
        pd.DataFrame([row]),
        recall_by_type(test, flags).assign(seed=0),
        detection.assign(seed=0),
        flags={0: flags},
    )


def evaluate_union(
    test: pd.DataFrame, tuned: Evaluation, rule_flags: np.ndarray[Any, Any], manifest: pd.DataFrame
) -> Evaluation:
    """Isolation Forest ∪ regras: o que o modelo acrescenta às regras óbvias."""
    parts = [evaluate_flags(test, flags | rule_flags, manifest) for flags in tuned.flags.values()]
    for seed, part in zip(tuned.flags, parts, strict=True):
        part.per_seed["seed"] = seed
        part.by_type["seed"] = seed
        part.scenarios["seed"] = seed
    return Evaluation(
        pd.concat([p.per_seed for p in parts]),
        pd.concat([p.by_type for p in parts]),
        pd.concat([p.scenarios for p in parts]),
    )


# --- Tabelas ---------------------------------------------------------------------------------

DETECTORS = ("isolation_forest", "if_sem_rotulos", "regras", "if_mais_regras")


def comparison_table(*evaluations: Evaluation) -> pd.DataFrame:
    rows = []
    for name, ev in zip(DETECTORS, evaluations, strict=True):
        row: dict[str, Any] = {"detector": name, "seeds": len(ev.per_seed)}
        for metric in (*SUMMARY_METRICS, "scenario_recall", "roc_auc", "pr_auc"):
            if metric in ev.per_seed:
                row[f"{metric}_mean"] = float(ev.per_seed[metric].mean())
                row[f"{metric}_std"] = (
                    float(ev.per_seed[metric].std(ddof=1)) if len(ev.per_seed) > 1 else 0.0
                )
        rows.append(row)
    return pd.DataFrame(rows)


def by_type_table(*evaluations: Evaluation) -> pd.DataFrame:
    merged = None
    for name, ev in zip(DETECTORS, evaluations, strict=True):
        stats = (
            ev.by_type.groupby(["anomaly_type", "intensity"])
            .agg(
                windows=("windows", "first"),
                **{f"{name}_mean": ("recall", "mean"), f"{name}_std": ("recall", "std")},
            )
            .reset_index()
        )
        merged = (
            stats
            if merged is None
            else merged.merge(stats.drop(columns="windows"), on=["anomaly_type", "intensity"])
        )
    assert merged is not None
    return _order(merged, "anomaly_type")


def scenario_table(*evaluations: Evaluation) -> pd.DataFrame:
    merged = None
    for name, ev in zip(DETECTORS, evaluations, strict=True):
        per_seed = (
            ev.scenarios.groupby(["type", "intensity", "seed"])["detected"].mean().reset_index()
        )
        stats = (
            per_seed.groupby(["type", "intensity"])["detected"]
            .agg(["mean", "std"])
            .rename(columns={"mean": f"{name}_mean", "std": f"{name}_std"})
            .reset_index()
        )
        counts = (
            ev.scenarios[ev.scenarios["seed"] == ev.scenarios["seed"].iloc[0]]
            .groupby(["type", "intensity"])
            .size()
            .rename("scenarios")
            .reset_index()
        )
        stats = counts.merge(stats, on=["type", "intensity"])
        merged = (
            stats
            if merged is None
            else merged.merge(stats.drop(columns="scenarios"), on=["type", "intensity"])
        )
    assert merged is not None
    return _order(merged, "type")


def overlap_table(tuned: Evaluation, rules: Evaluation) -> pd.DataFrame:
    """Por cenário: detectado pelo IF (maioria das sementes), pelas regras, por ambos ou nenhum."""
    rate = tuned.scenarios.groupby("scenario_id")["detected"].mean()
    by_rules = rules.scenarios.set_index("scenario_id")["detected"]
    info = rules.scenarios.set_index("scenario_id")[["type", "intensity"]]
    frame = info.assign(
        isolation_forest=rate.reindex(info.index) >= 0.5,
        regras=by_rules.reindex(info.index).astype(bool),
    )
    frame["categoria"] = np.select(
        [frame["isolation_forest"] & frame["regras"], frame["isolation_forest"], frame["regras"]],
        ["ambos", "so_isolation_forest", "so_regras"],
        default="nenhum",
    )
    table = frame.groupby(["type", "categoria"]).size().unstack(fill_value=0).reset_index()
    for column in ("ambos", "so_isolation_forest", "so_regras", "nenhum"):
        if column not in table:
            table[column] = 0
    return _order(table[["type", "ambos", "so_isolation_forest", "so_regras", "nenhum"]], "type")


def rule_firing_table(test: pd.DataFrame, fired: pd.DataFrame, rules: list[Rule]) -> pd.DataFrame:
    anomalous = test["label"].to_numpy() == 1
    rows = []
    for rule in [*rules, Rule(name="any", kind="fixed")]:
        flags = fired[rule.name].to_numpy()
        rows.append(
            {
                "rule": rule.name,
                "fires_normal": int((flags & ~anomalous).sum()),
                "fires_anomalous": int((flags & anomalous).sum()),
                "precision": float((flags & anomalous).sum() / max(flags.sum(), 1)),
            }
        )
    return pd.DataFrame(rows)


def timing_summary(
    dataset_summary: dict[str, Any], tuned: Evaluation, tuning_s: float
) -> dict[str, float]:
    read_s = float(dataset_summary["timings_s"]["leitura_e_eventos_s"])
    records = int(dataset_summary["read"]["records"])
    return {
        "leitura_e_eventos_s": read_s,
        "agregacao_s": float(dataset_summary["timings_s"]["agregacao_s"]),
        "linhas_por_segundo": round(records / read_s) if read_s else 0.0,
        "treino_modelo_s_medio": float(tuned.per_seed["fit_s"].mean()),
        "score_teste_s_medio": float(tuned.per_seed["score_s"].mean()),
        "busca_em_grade_s": tuning_s,
    }


def _order(table: pd.DataFrame, type_column: str) -> pd.DataFrame:
    intensity_order = {"baixa": 0, "media": 1, "alta": 2}
    if "intensity" in table:
        table = table.assign(_i=table["intensity"].map(intensity_order))
        return table.sort_values([type_column, "_i"]).drop(columns="_i").reset_index(drop=True)
    return table.sort_values(type_column).reset_index(drop=True)


# --- Saídas ----------------------------------------------------------------------------------


def _save_scores(
    out: Path,
    data: pd.DataFrame,
    test: pd.DataFrame,
    tuned: Evaluation,
    label_free: Evaluation,
    fired: pd.DataFrame,
    config: ModelConfig,
) -> None:
    """Scores do teste (todas as sementes) e de todos os splits (primeira semente) + modelo.

    score_seed<k>: Isolation Forest ajustado; score_lf_seed<k>: configuração sem rótulos.
    """
    test_scores = test[ID_COLUMNS].copy()
    for seed, scores in tuned.scores.items():
        test_scores[f"score_seed{seed}"] = scores
    for seed, scores in label_free.scores.items():
        test_scores[f"score_lf_seed{seed}"] = scores
    test_scores = test_scores.join(fired.add_prefix("regra_"))
    test_scores.to_parquet(out / "scores" / "test_scores.parquet", index=False)
    model = tuned.first_model
    assert model is not None and model.threshold is not None
    every = data[ID_COLUMNS].copy()
    every[f"score_seed{model.seed}"] = model.score(data)
    every["threshold"] = model.threshold
    every.to_parquet(out / "scores" / f"all_splits_seed{model.seed}.parquet", index=False)
    model.save(PROJECT_ROOT / config.models_dir / out.name / f"model_seed{model.seed}.joblib")


def _write_config(
    out: Path, config: ModelConfig, run: dict[str, Any], dataset_summary: dict[str, Any]
) -> None:
    resolved = {
        "model": config.raw,
        "rules": yaml.safe_load((CONFIG_DIR / "rules.yaml").read_text(encoding="utf-8")),
        "sacla": yaml.safe_load((CONFIG_DIR / "sacla.yaml").read_text(encoding="utf-8")),
        "run": run,
        "dataset": {k: v for k, v in dataset_summary.items() if k != "crosscheck"},
    }
    (out / "config.resolved.yaml").write_text(
        yaml.safe_dump(resolved, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )


def _write_env(out: Path) -> None:
    versions = {}
    for package in PACKAGES:
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "ausente"
    env = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": versions,
        "git_commit": os.environ.get("SACLA_GIT_COMMIT", "desconhecido"),
    }
    (out / "env.json").write_text(json.dumps(env, indent=2) + "\n", encoding="utf-8")


def _fmt(mean: float, std: float | None = None, pct: bool = True) -> str:
    scale = 100 if pct else 1
    if std is None or np.isnan(std) or std == 0:
        return f"{mean * scale:.1f}" if pct else f"{mean:.3f}"
    return f"{mean * scale:.1f} ± {std * scale:.1f}" if pct else f"{mean:.3f} ± {std:.3f}"


def summary_markdown(
    metrics: dict[str, Any],
    comparison: pd.DataFrame,
    by_type: pd.DataFrame,
    scenarios: pd.DataFrame,
    overlap: pd.DataFrame,
    hyper: pd.DataFrame,
    windows: pd.DataFrame,
) -> str:
    names = {
        "isolation_forest": "Isolation Forest (ajustado)",
        "if_sem_rotulos": "Isolation Forest (sem rótulos)",
        "regras": "Regras",
        "if_mais_regras": "Isolation Forest ∪ regras",
    }
    lines = [
        f"# Experimento {metrics['exp_id']}",
        "",
        f"Configuração escolhida na validação: `{metrics['chosen_params']}`.",
        "",
        "## Teste (média ± desvio nas sementes; valores em %)",
        "",
        "| Detector | Precisão | Recall | F1 | FP | Cenários detectados | ROC-AUC | PR-AUC |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison.to_dict(orient="records"):
        auc = (
            (
                _fmt(row["roc_auc_mean"], row.get("roc_auc_std"), pct=False),
                _fmt(row["pr_auc_mean"], row.get("pr_auc_std"), pct=False),
            )
            if "roc_auc_mean" in row and not pd.isna(row["roc_auc_mean"])
            else ("—", "—")
        )
        cells = [
            names[row["detector"]],
            _fmt(row["precision_mean"], row["precision_std"]),
            _fmt(row["recall_mean"], row["recall_std"]),
            _fmt(row["f1_mean"], row["f1_std"]),
            f"{row['fp_mean']:.0f}",
            _fmt(row["scenario_recall_mean"], row["scenario_recall_std"]),
            *auc,
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Recall por janela, por tipo × intensidade (%)",
        "",
        "| Tipo | Intensidade | Janelas | IF | IF sem rótulos | Regras | IF ∪ regras |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in by_type.to_dict(orient="records"):
        lines.append(
            f"| {row['anomaly_type']} | {row['intensity']} | {row['windows']} | "
            f"{_fmt(row['isolation_forest_mean'], row['isolation_forest_std'])} | "
            f"{_fmt(row['if_sem_rotulos_mean'], row['if_sem_rotulos_std'])} | "
            f"{_fmt(row['regras_mean'])} | "
            f"{_fmt(row['if_mais_regras_mean'], row['if_mais_regras_std'])} |"
        )
    lines += [
        "",
        "## Cenários detectados, por tipo × intensidade (%)",
        "",
        "| Tipo | Intensidade | Cenários | IF | IF sem rótulos | Regras | IF ∪ regras |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in scenarios.to_dict(orient="records"):
        lines.append(
            f"| {row['type']} | {row['intensity']} | {row['scenarios']} | "
            f"{_fmt(row['isolation_forest_mean'], row['isolation_forest_std'])} | "
            f"{_fmt(row['if_sem_rotulos_mean'], row['if_sem_rotulos_std'])} | "
            f"{_fmt(row['regras_mean'])} | "
            f"{_fmt(row['if_mais_regras_mean'], row['if_mais_regras_std'])} |"
        )
    lines += [
        "",
        "## Cenários: Isolation Forest (maioria das sementes) × regras",
        "",
        "| Tipo | Ambos | Só IF | Só regras | Nenhum |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in overlap.to_dict(orient="records"):
        lines.append(
            f"| {row['type']} | {row['ambos']} | {row['so_isolation_forest']} | "
            f"{row['so_regras']} | {row['nenhum']} |"
        )
    lines += [
        "",
        "## Sensibilidade na validação: hiperparâmetros (F1 %)",
        "",
        "| Hiperparâmetro | Valor | F1 | PR-AUC |",
        "|---|---|---:|---:|",
    ]
    for row in hyper.to_dict(orient="records"):
        mark = " (escolhido)" if row["chosen"] else ""
        lines.append(
            f"| {row['hyperparameter']} | {row['value']}{mark} | "
            f"{_fmt(row['f1_mean'], row['f1_std'])} | {row['pr_auc_mean']:.3f} |"
        )
    lines += [
        "",
        "## Sensibilidade na validação: tamanho da janela",
        "",
        "| Janela (min) | Melhor configuração | F1 (%) | PR-AUC | Cenários detectados (%) | "
        "Alertas |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in windows.to_dict(orient="records"):
        lines.append(
            f"| {row['window_minutes']} | `{row['best_config']}` | "
            f"{_fmt(row['f1_mean'], row['f1_std'])} | {row['pr_auc_mean']:.3f} | "
            f"{_fmt(row['scenario_recall_mean'])} | {row['alerts_mean']:.0f} |"
        )
    timings = metrics["timings"]
    lines += [
        "",
        "## Tempo de processamento",
        "",
        f"- leitura e eventos: {timings['leitura_e_eventos_s']:.1f} s "
        f"({timings['linhas_por_segundo']:.0f} linhas/s)",
        f"- agregação por janela: {timings['agregacao_s']:.1f} s",
        f"- treino do modelo: {timings['treino_modelo_s_medio']:.2f} s (média)",
        f"- score do teste: {timings['score_teste_s_medio']:.3f} s (média)",
        f"- busca em grade (validação): {timings['busca_em_grade_s']:.0f} s",
        "",
    ]
    return "\n".join(lines)

"""Robustez a outras simulações (sementes de dados diferentes).

A configuração escolhida no experimento principal fica CONGELADA: em cada nova simulação só o
limiar é escolhido na validação daquela simulação, como no protocolo original, e o teste é
avaliado uma vez com as sementes de avaliação. Mede se o resultado depende da semente da
simulação, e não só da semente do modelo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from evaluation.experiment import (
    DETECTORS,
    Evaluation,
    comparison_table,
    evaluate_detector,
    evaluate_flags,
    evaluate_union,
    load_split,
)
from evaluation.rules_baseline import (
    Rule,
    apply_rules,
    fit_rule_stats,
    load_rules,
    local_hours,
)
from evaluation.tuning import ModelConfig, flat_columns, load_model_config
from sacla.config import PROJECT_ROOT, SaclaSettings
from sacla.model import ModelParams

log = logging.getLogger(__name__)

NAMES = {
    "isolation_forest": "Isolation Forest (ajustado)",
    "if_sem_rotulos": "Isolation Forest (sem rótulos)",
    "regras": "Regras",
    "if_mais_regras": "Isolation Forest ∪ regras",
}


@dataclass
class RunEvaluation:
    """Os quatro detectores no teste de uma simulação, com a configuração congelada."""

    run_id: str
    test: pd.DataFrame
    manifest: pd.DataFrame
    contamination_pct: float
    tuned: Evaluation
    label_free: Evaluation
    rules: Evaluation
    union: Evaluation

    def comparison(self) -> pd.DataFrame:
        table = comparison_table(self.tuned, self.label_free, self.rules, self.union)
        table.insert(0, "run_id", self.run_id)
        table.insert(1, "test_contamination_pct", self.contamination_pct)
        return table


def frozen_setup(exp_dir: Path) -> tuple[ModelParams, int, str]:
    """Configuração escolhida, janela (min) e simulação de referência de um experimento."""
    metrics = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    return (
        ModelParams(**metrics["chosen_params"]),
        int(metrics["window_minutes"]),
        str(metrics["run_id"]),
    )


def evaluate_run(
    run_id: str,
    params: ModelParams,
    window: int,
    config: ModelConfig,
    rules: list[Rule],
    data_root: Path,
    features_root: Path,
    settings: SaclaSettings,
) -> RunEvaluation:
    """Protocolo da robustez (D36): só o limiar vem da validação desta simulação."""
    run_dir = data_root / run_id
    manifest = pd.read_csv(run_dir / "manifest.csv")
    data, summary = load_split(features_root / run_id, run_dir, settings, window)
    train, validation, test = (data[data["split"] == s] for s in ("train", "validation", "test"))
    log.info("avaliando %s (%s)", run_id, params.slug())
    tuned = evaluate_detector(train, validation, test, params, config, manifest, "validation")
    label_free = evaluate_detector(
        train, validation, test, config.label_free, config, manifest, "train_quantile"
    )
    hours = local_hours(data, settings.features.timezone)
    fired = apply_rules(test, rules, fit_rule_stats(train, rules, hours), hours)
    return RunEvaluation(
        run_id=run_id,
        test=test,
        manifest=manifest,
        contamination_pct=float(summary["windows_by_split"]["test"]["contamination_pct"]),
        tuned=tuned,
        label_free=label_free,
        rules=evaluate_flags(test, fired["any"].to_numpy(), manifest),
        union=evaluate_union(test, tuned, fired["any"].to_numpy(), manifest),
    )


def run_robustness(
    exp_dir: Path, run_ids: list[str], data_root: Path, features_root: Path, settings: SaclaSettings
) -> Path:
    params, window, reference = frozen_setup(exp_dir)
    config = load_model_config()
    rules = load_rules()
    rows = [
        evaluate_run(
            run_id, params, window, config, rules, data_root, features_root, settings
        ).comparison()
        for run_id in [reference, *run_ids]
    ]
    per_run = pd.concat(rows, ignore_index=True)
    across = per_run.groupby("detector", sort=False)[
        ["precision_mean", "recall_mean", "f1_mean", "scenario_recall_mean"]
    ].agg(["mean", "std"])
    across.columns = flat_columns(across.columns)
    across = across.reset_index()
    # Pasta própria: reavaliar o experimento (que recria a pasta dele) não apaga a robustez.
    out_dir = PROJECT_ROOT / config.robustness_dir / exp_dir.name
    out_dir.mkdir(parents=True, exist_ok=True)
    per_run.to_csv(out_dir / "robustness_runs.csv", index=False, lineterminator="\n")
    across.to_csv(out_dir / "robustness_summary.csv", index=False, lineterminator="\n")
    out = out_dir / "robustez.md"
    out.write_text(_markdown(per_run, across, params), encoding="utf-8")
    return out


def _markdown(per_run: pd.DataFrame, across: pd.DataFrame, params: ModelParams) -> str:
    lines = [
        f"# Robustez a outras simulações (configuração congelada `{params.slug()}`)",
        "",
        "Média das sementes de avaliação em cada simulação; valores em %.",
        "",
        "| Simulação | Contaminação do teste | Detector | Precisão | Recall | F1 | "
        "Cenários detectados |",
        "|---|---:|---|---:|---:|---:|---:|",
    ]
    for row in per_run.to_dict(orient="records"):
        lines.append(
            f"| {row['run_id']} | {row['test_contamination_pct']:.2f} | "
            f"{NAMES[row['detector']]} | {100 * row['precision_mean']:.1f} | "
            f"{100 * row['recall_mean']:.1f} | {100 * row['f1_mean']:.1f} | "
            f"{100 * row['scenario_recall_mean']:.1f} |"
        )
    lines += [
        "",
        "## Entre simulações (média ± desvio das médias por simulação)",
        "",
        "| Detector | Precisão | Recall | F1 | Cenários detectados |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in across.to_dict(orient="records"):
        cells = [
            f"{100 * row[f'{m}_mean']:.1f} ± {100 * row[f'{m}_std']:.1f}"
            for m in ("precision_mean", "recall_mean", "f1_mean", "scenario_recall_mean")
        ]
        lines.append(f"| {NAMES[row['detector']]} | " + " | ".join(cells) + " |")
    assert set(across["detector"]) <= set(DETECTORS)
    return "\n".join(lines) + "\n"

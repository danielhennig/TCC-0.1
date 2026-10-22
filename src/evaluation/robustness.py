"""Robustez a outras simulações (sementes de dados diferentes).

A configuração escolhida no experimento principal fica CONGELADA: em cada nova simulação só o
limiar é escolhido na validação daquela simulação, como no protocolo original, e o teste é
avaliado uma vez com as sementes de avaliação. Mede se o resultado depende da semente da
simulação, e não só da semente do modelo.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

from evaluation.experiment import (
    DETECTORS,
    comparison_table,
    evaluate_detector,
    evaluate_flags,
    evaluate_union,
    load_split,
)
from evaluation.rules_baseline import apply_rules, fit_rule_stats, load_rules, local_hours
from evaluation.tuning import flat_columns, load_model_config
from sacla.config import PROJECT_ROOT, SaclaSettings
from sacla.model import ModelParams

log = logging.getLogger(__name__)

NAMES = {
    "isolation_forest": "Isolation Forest (ajustado)",
    "if_sem_rotulos": "Isolation Forest (sem rótulos)",
    "regras": "Regras",
    "if_mais_regras": "Isolation Forest ∪ regras",
}


def run_robustness(
    exp_dir: Path, run_ids: list[str], data_root: Path, features_root: Path, settings: SaclaSettings
) -> Path:
    metrics = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    params = ModelParams(**metrics["chosen_params"])
    window = int(metrics["window_minutes"])
    config = load_model_config()
    rules = load_rules()
    rows = []
    for run_id in [metrics["run_id"], *run_ids]:
        run_dir = data_root / run_id
        manifest = pd.read_csv(run_dir / "manifest.csv")
        data, summary = load_split(features_root / run_id, run_dir, settings, window)
        train, validation, test = (
            data[data["split"] == s] for s in ("train", "validation", "test")
        )
        log.info("robustez: %s (%s)", run_id, params.slug())
        tuned = evaluate_detector(train, validation, test, params, config, manifest, "validation")
        label_free = evaluate_detector(
            train, validation, test, config.label_free, config, manifest, "train_quantile"
        )
        hours = local_hours(data, settings.features.timezone)
        fired = apply_rules(test, rules, fit_rule_stats(train, rules, hours), hours)
        rules_eval = evaluate_flags(test, fired["any"].to_numpy(), manifest)
        union = evaluate_union(test, tuned, fired["any"].to_numpy(), manifest)
        table = comparison_table(tuned, label_free, rules_eval, union)
        table.insert(0, "run_id", run_id)
        table.insert(
            1, "test_contamination_pct", summary["windows_by_split"]["test"]["contamination_pct"]
        )
        rows.append(table)
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

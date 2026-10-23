"""Validação confirmatória (Fase 7; pré-registro em docs/decisoes.md, D52).

A configuração do experimento fica congelada e é avaliada com o protocolo da robustez (D36):
- nas simulações de desenvolvimento (a de referência e as da robustez), só como referência;
- nas simulações confirmatórias, nunca vistas antes, onde são testadas as hipóteses
  pré-registradas.
Saída: results/confirmatorio/<exp_id>/ (tabelas, sinalizações por janela, relatório, figuras).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluation.experiment import DETECTORS, ID_COLUMNS
from evaluation.metrics import scenario_detection
from evaluation.robustness import NAMES, RunEvaluation, evaluate_run, frozen_setup
from evaluation.rules_baseline import load_rules
from evaluation.stats import (
    METRICS,
    WINDOW_METRICS,
    Comparison,
    StatsConfig,
    block_counts,
    holm,
    load_stats_config,
    mcnemar_exact,
    paired_permutation,
    percentile_interval,
    scenario_samples,
    stratified_weights,
    window_metric,
    window_samples,
)
from evaluation.tuning import load_model_config
from evaluation.validation_plots import confirmatory_figures
from sacla.config import PROJECT_ROOT, SaclaSettings

log = logging.getLogger(__name__)

Array = np.ndarray[Any, Any]

DEVELOPMENT = "desenvolvimento"
CONFIRMATORY = "confirmatorio"
GROUP_NAMES = {DEVELOPMENT: "desenvolvimento", CONFIRMATORY: "confirmatória"}
METRIC_NAMES = {
    "precision": "precisão",
    "recall": "recall",
    "f1": "F1",
    "scenarios": "cenários detectados",
}
SUBSET_NAMES = {"all": "todos", "novel": "tipo inédito"}
# Colunas comparadas com o experimento e a robustez já publicados (conferência de reprodução).
REPRODUCED = ("precision_mean", "recall_mean", "f1_mean", "scenario_recall_mean")
REPRODUCTION_TOLERANCE = 1e-9


@dataclass
class RunData:
    """O que a estatística usa de uma simulação: janelas de teste, sinalizações e cenários."""

    run_id: str
    group: str
    windows: pd.DataFrame
    flags: dict[str, Array]  # detector -> (janelas, sementes)
    scenarios: pd.DataFrame  # um cenário por linha, com a fração de sementes que o detecta


def run_flags(result: RunEvaluation) -> dict[str, Array]:
    """Sinalizações de cada detector no teste: (janelas, sementes); as regras têm uma coluna."""
    tuned = np.column_stack(list(result.tuned.flags.values())).astype(bool)
    label_free = np.column_stack(list(result.label_free.flags.values())).astype(bool)
    rules = np.asarray(result.rules.flags[0], dtype=bool)[:, None]
    return {
        "isolation_forest": tuned,
        "if_sem_rotulos": label_free,
        "regras": rules,
        "if_mais_regras": tuned | rules,  # a mesma união de evaluate_union
    }


def scenario_rates(
    windows: pd.DataFrame, flags: dict[str, Array], manifest: pd.DataFrame
) -> pd.DataFrame:
    """Por cenário de teste: tipo, intensidade, se é inédito e a fração de sementes que detecta."""
    table: pd.DataFrame | None = None
    for detector, matrix in flags.items():
        per_seed = [
            scenario_detection(windows, matrix[:, s], manifest) for s in range(matrix.shape[1])
        ]
        if table is None:
            table = per_seed[0][["scenario_id", "user", "type", "intensity", "novel"]].copy()
            table = table.reset_index(drop=True)
        detected = np.column_stack([p["detected"].to_numpy() for p in per_seed])
        table[detector] = detected.mean(axis=1)
    assert table is not None
    table["novel"] = table["novel"].astype(bool)
    return table


def run_data(result: RunEvaluation, group: str) -> RunData:
    windows = result.test[ID_COLUMNS].reset_index(drop=True)
    flags = run_flags(result)
    return RunData(
        run_id=result.run_id,
        group=group,
        windows=windows,
        flags=flags,
        scenarios=scenario_rates(windows, flags, result.manifest),
    )


def flags_frame(data: RunData) -> pd.DataFrame:
    """Janelas de teste com as sinalizações de cada detector e semente (para conferência)."""
    frame = data.windows.copy()
    for detector, matrix in data.flags.items():
        if detector == "if_mais_regras":
            continue  # derivada: isolation_forest | regras
        for s in range(matrix.shape[1]):
            name = detector if matrix.shape[1] == 1 else f"{detector}_s{s}"
            frame[name] = matrix[:, s]
    return frame


# --- Agregação e estatística -----------------------------------------------------------------


@dataclass
class Pooled:
    """Simulações agregadas: contagens por bloco (usuário × dia) e cenários, com o estrato."""

    counts: dict[str, Array]  # detector -> (blocos, sementes, 3)
    block_strata: Array
    scenarios: pd.DataFrame  # com run_id


def pool(runs: list[RunData]) -> Pooled:
    windows = pd.concat([r.windows.assign(run_id=r.run_id) for r in runs], ignore_index=True)
    day = windows["window_start_local"].astype(str).str[:10]  # data local (AAAA-MM-DD)
    keys = windows["run_id"] + "|" + windows["user"].astype(str) + "|" + day
    blocks, uniques = pd.factorize(keys)
    strata = np.array([key.split("|", 1)[0] for key in uniques])
    labels = windows["label"].to_numpy()
    counts = {
        d: block_counts(labels, blocks, len(uniques), np.vstack([r.flags[d] for r in runs]))
        for d in DETECTORS
    }
    scenarios = pd.concat([r.scenarios.assign(run_id=r.run_id) for r in runs], ignore_index=True)
    return Pooled(counts=counts, block_strata=strata, scenarios=scenarios)


class Resampler:
    """Bootstrap de um conjunto agregado. As mesmas reamostragens servem a todos os detectores,
    por isso a diferença entre dois deles é pareada."""

    def __init__(self, pooled: Pooled, config: StatsConfig) -> None:
        self.pooled = pooled
        self.config = config
        rng = np.random.default_rng(config.seed)
        resamples = config.bootstrap_resamples
        self.blocks = stratified_weights(pooled.block_strata, resamples, rng)
        self.scenario_masks = {
            "all": np.ones(len(pooled.scenarios), dtype=bool),
            "novel": pooled.scenarios["novel"].to_numpy(dtype=bool),
        }
        self.scenarios = {
            subset: stratified_weights(
                pooled.scenarios.loc[mask, "run_id"].to_numpy(), resamples, rng
            )
            for subset, mask in self.scenario_masks.items()
            if mask.any()
        }

    def value(self, detector: str, metric: str, subset: str) -> float:
        if metric in WINDOW_METRICS:
            return float(window_metric(self.pooled.counts[detector].sum(axis=0), metric))
        rates = self._rates(detector, subset)
        return float(rates.mean()) if len(rates) else float("nan")

    def samples(self, detector: str, metric: str, subset: str) -> Array:
        if metric in WINDOW_METRICS:
            return window_samples(self.blocks, self.pooled.counts[detector], metric)
        if subset not in self.scenarios:
            return np.full(self.config.bootstrap_resamples, np.nan)
        return scenario_samples(self.scenarios[subset], self._rates(detector, subset))

    def interval(self, detector: str, metric: str, subset: str) -> tuple[float, float]:
        samples = self.samples(detector, metric, subset)
        if np.isnan(samples).any():
            return float("nan"), float("nan")
        return percentile_interval(samples, self.config.confidence)

    def difference(self, comparison: Comparison) -> dict[str, float]:
        c = comparison
        a = self.value(c.a, c.metric, c.subset)
        b = self.value(c.b, c.metric, c.subset)
        gap = self.samples(c.a, c.metric, c.subset) - self.samples(c.b, c.metric, c.subset)
        low, high = (
            (float("nan"), float("nan"))
            if np.isnan(gap).any()
            else percentile_interval(gap, self.config.confidence)
        )
        return {"value_a": a, "value_b": b, "diff": a - b, "diff_low": low, "diff_high": high}

    def majority(self, detector: str, subset: str) -> Array:
        detected: Array = self._rates(detector, subset) >= self.config.majority
        return detected

    def _rates(self, detector: str, subset: str) -> Array:
        mask = self.scenario_masks[subset]
        return np.asarray(self.pooled.scenarios.loc[mask, detector], dtype=float)


def interval_table(scope: str, resampler: Resampler) -> pd.DataFrame:
    rows = []
    for detector in DETECTORS:
        for metric in METRICS:
            subsets = ("all", "novel") if metric == "scenarios" else ("all",)
            for subset in subsets:
                low, high = resampler.interval(detector, metric, subset)
                rows.append(
                    {
                        "scope": scope,
                        "detector": detector,
                        "metric": metric,
                        "subset": subset,
                        "value": resampler.value(detector, metric, subset),
                        "low": low,
                        "high": high,
                    }
                )
    return pd.DataFrame(rows)


def hypothesis_table(resampler: Resampler, config: StatsConfig) -> pd.DataFrame:
    """Testes das hipóteses pré-registradas, com Holm; a semente é a da configuração."""
    rng = np.random.default_rng(config.seed)
    rows = []
    for h in config.hypotheses:
        row: dict[str, Any] = {
            "id": h.id,
            "metric": h.metric,
            "subset": h.subset,
            "a": h.a,
            "b": h.b,
            **resampler.difference(h),
        }
        if h.metric == "scenarios":
            test = mcnemar_exact(
                resampler.majority(h.a, h.subset), resampler.majority(h.b, h.subset)
            )
            row.update(test="mcnemar_exato", only_a=test["only_a"], only_b=test["only_b"])
            row["p"] = test["p"]
        else:
            test = paired_permutation(
                resampler.pooled.counts[h.a],
                resampler.pooled.counts[h.b],
                h.metric,
                config.permutation_resamples,
                rng,
            )
            row.update(test="permutacao_pareada_blocos", only_a=np.nan, only_b=np.nan)
            row["p"] = test["p"]
        rows.append(row)
    table = pd.DataFrame(rows)
    table["p_holm"] = holm(table["p"].tolist())
    table["alpha"] = config.alpha
    table["rejects_h0"] = table["p_holm"] <= config.alpha
    return table


def descriptive_table(resampler: Resampler, config: StatsConfig) -> pd.DataFrame:
    rows = [
        {"metric": c.metric, "subset": c.subset, "a": c.a, "b": c.b, **resampler.difference(c)}
        for c in config.descriptive
    ]
    return pd.DataFrame(rows)


# --- Execução --------------------------------------------------------------------------------


def run_confirmatory(
    exp_dir: Path,
    development_runs: list[str],
    confirmatory_runs: list[str],
    data_root: Path,
    features_root: Path,
    settings: SaclaSettings,
) -> Path:
    params, window, reference = frozen_setup(exp_dir)
    config = load_model_config(settings=settings)
    stats_config = load_stats_config(config.raw, DETECTORS)
    rules = load_rules()
    out = PROJECT_ROOT / str(config.raw["confirmatory"]["results_dir"]) / exp_dir.name
    (out / "janelas").mkdir(parents=True, exist_ok=True)

    runs: list[RunData] = []
    tables = []
    groups = [(DEVELOPMENT, [reference, *development_runs]), (CONFIRMATORY, confirmatory_runs)]
    for group, run_ids in groups:
        for run_id in run_ids:
            result = evaluate_run(
                run_id, params, window, config, rules, data_root, features_root, settings
            )
            tables.append(result.comparison().assign(group=group))
            data = run_data(result, group)
            flags_frame(data).to_parquet(out / "janelas" / f"{run_id}.parquet", index=False)
            runs.append(data)
    per_run = pd.concat(tables, ignore_index=True)
    reproduction = reproduction_check(per_run, exp_dir, reference, config.robustness_dir)

    intervals = []
    for data in runs:
        intervals.append(interval_table(data.run_id, Resampler(pool([data]), stats_config)))
    by_group = {
        group: Resampler(pool([r for r in runs if r.group == group]), stats_config)
        for group, _ in groups
    }
    for group, resampler in by_group.items():
        intervals.append(interval_table(f"agregado_{group}", resampler))
    interval_frame = pd.concat(intervals, ignore_index=True)
    hypotheses = hypothesis_table(by_group[CONFIRMATORY], stats_config)
    descriptive = descriptive_table(by_group[CONFIRMATORY], stats_config)

    written = {
        "confirmatory_runs": per_run,
        "intervals": interval_frame,
        "hypotheses": hypotheses,
        "descriptive_differences": descriptive,
    }
    for name, table in written.items():
        table.to_csv(out / f"{name}.csv", index=False, lineterminator="\n")
    summary = {
        "exp_id": exp_dir.name,
        "params": params.as_dict(),
        "window_minutes": window,
        "development_runs": [reference, *development_runs],
        "confirmatory_runs": confirmatory_runs,
        "stats": config.raw["stats"],
        "reproduction": reproduction,
        "scenarios": {
            r.run_id: {"all": len(r.scenarios), "novel": int(r.scenarios["novel"].sum())}
            for r in runs
        },
    }
    (out / "estatistica.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=float) + "\n", encoding="utf-8"
    )
    report = out / "confirmatorio.md"
    report.write_text(
        markdown(params.slug(), per_run, interval_frame, hypotheses, descriptive, summary),
        encoding="utf-8",
    )
    confirmatory_figures(out, interval_frame, hypotheses, descriptive, confirmatory_runs, config)
    log.info("resultados em %s", out.relative_to(PROJECT_ROOT))
    return report


def reproduction_check(
    per_run: pd.DataFrame, exp_dir: Path, reference: str, robustness_dir: str
) -> dict[str, Any]:
    """As simulações de desenvolvimento devem reproduzir os números já publicados.

    Referência: metrics.json do experimento; demais: robustness_runs.csv, se existir.
    """
    metrics = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    published = [pd.DataFrame(metrics["test"]).assign(run_id=reference)]
    robustness = PROJECT_ROOT / robustness_dir / exp_dir.name / "robustness_runs.csv"
    if robustness.exists():
        published.append(pd.read_csv(robustness))
    expected = pd.concat(published, ignore_index=True).drop_duplicates(["run_id", "detector"])
    merged = per_run.merge(expected, on=["run_id", "detector"], suffixes=("", "_publicado"))
    gaps = [float((merged[c] - merged[f"{c}_publicado"]).abs().max()) for c in REPRODUCED]
    largest = max(gaps) if gaps else float("nan")
    result = {"compared_runs": sorted(set(merged["run_id"])), "max_abs_diff": largest}
    if not merged.empty and largest > REPRODUCTION_TOLERANCE:
        raise RuntimeError(f"as simulações de desenvolvimento não reproduzem o publicado: {result}")
    return result


# --- Relatório -------------------------------------------------------------------------------


def _pct(value: float) -> str:
    return "—" if np.isnan(value) else f"{100 * value:.1f}"


def _ci(value: float, low: float, high: float) -> str:
    if np.isnan(low):
        return _pct(value)
    return f"{_pct(value)} [{_pct(low)}; {_pct(high)}]"


def _p(value: float) -> str:
    return f"{value:.2g}" if value >= 1e-4 else "< 0.0001"


def markdown(
    slug: str,
    per_run: pd.DataFrame,
    intervals: pd.DataFrame,
    hypotheses: pd.DataFrame,
    descriptive: pd.DataFrame,
    summary: dict[str, Any],
) -> str:
    confirm = summary["confirmatory_runs"]
    n_all = sum(summary["scenarios"][r]["all"] for r in confirm)
    n_novel = sum(summary["scenarios"][r]["novel"] for r in confirm)
    stats = summary["stats"]
    lines = [
        f"# Validação confirmatória (configuração congelada `{slug}`)",
        "",
        "Pré-registro: `docs/decisoes.md`, D52. Simulações confirmatórias: "
        + ", ".join(f"`{r}`" for r in confirm)
        + f" ({n_all} cenários de teste, {n_novel} do tipo inédito). Valores em %; intervalos "
        f"de {100 * stats['confidence']:.0f}% por bootstrap ({stats['bootstrap_resamples']} "
        "reamostragens, estratificadas por simulação).",
        "",
        "## Hipóteses (agregado confirmatório)",
        "",
        "| | Comparação | Medida | A | B | A − B [IC] | Discordantes (só A / só B) | p | "
        "p (Holm) | Resultado |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in hypotheses.to_dict(orient="records"):
        measure = METRIC_NAMES[row["metric"]] + (
            f" ({SUBSET_NAMES[row['subset']]})" if row["subset"] != "all" else ""
        )
        discordant = (
            "—" if pd.isna(row["only_a"]) else f"{int(row['only_a'])} / {int(row['only_b'])}"
        )
        verdict = "rejeita H0" if row["rejects_h0"] else "não rejeita H0"
        lines.append(
            f"| {row['id']} | {NAMES[row['a']]} × {NAMES[row['b']]} | {measure} | "
            f"{_pct(row['value_a'])} | {_pct(row['value_b'])} | "
            f"{_ci(row['diff'], row['diff_low'], row['diff_high'])} | {discordant} | "
            f"{_p(row['p'])} | {_p(row['p_holm'])} | {verdict} |"
        )
    lines += [
        "",
        f"Testes bicaudais, α = {stats['alpha']}, correção de Holm sobre as "
        f"{len(hypotheses)} hipóteses. Cenários: McNemar exato com a detecção em ao menos "
        f"{100 * stats['majority']:.0f}% das sementes do modelo. F1: permutação pareada por "
        f"blocos usuário × dia ({stats['permutation_resamples']} permutações).",
        "",
        "## Diferenças descritivas (agregado confirmatório, sem teste)",
        "",
        "| Comparação | Medida | A | B | A − B [IC] |",
        "|---|---|---:|---:|---:|",
    ]
    for row in descriptive.to_dict(orient="records"):
        lines.append(
            f"| {NAMES[row['a']]} × {NAMES[row['b']]} | {METRIC_NAMES[row['metric']]} | "
            f"{_pct(row['value_a'])} | {_pct(row['value_b'])} | "
            f"{_ci(row['diff'], row['diff_low'], row['diff_high'])} |"
        )
    for group in (CONFIRMATORY, DEVELOPMENT):
        scope = f"agregado_{group}"
        title = (
            "Desempenho por detector (agregado confirmatório)"
            if group == CONFIRMATORY
            else "Referência: simulações de desenvolvimento (agregado, já usadas antes)"
        )
        lines += ["", f"## {title}", "", *_detector_rows(intervals[intervals["scope"] == scope])]
    lines += [
        "",
        "## Por simulação (F1 e cenários detectados, com IC)",
        "",
        "| Simulação | Grupo | Detector | F1 | Cenários detectados |",
        "|---|---|---|---:|---:|",
    ]
    for run_id, group in per_run[["run_id", "group"]].drop_duplicates().itertuples(index=False):
        scope = intervals[intervals["scope"] == run_id]
        for detector in DETECTORS:
            f1 = _row(scope, detector, "f1", "all")
            scen = _row(scope, detector, "scenarios", "all")
            lines.append(
                f"| {run_id} | {GROUP_NAMES[group]} | {NAMES[detector]} | "
                f"{_ci(f1['value'], f1['low'], f1['high'])} | "
                f"{_ci(scen['value'], scen['low'], scen['high'])} |"
            )
    check = summary["reproduction"]
    lines += [
        "",
        "## Conferência",
        "",
        f"- As simulações de desenvolvimento ({', '.join(check['compared_runs'])}) reproduzem os "
        f"números publicados (diferença máxima: {check['max_abs_diff']:.1e}).",
        "",
    ]
    return "\n".join(lines)


def _detector_rows(scope: pd.DataFrame) -> list[str]:
    lines = [
        "| Detector | Precisão | Recall | F1 | Cenários detectados | Tipo inédito detectado |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for detector in DETECTORS:
        cells = []
        for metric, subset in (
            ("precision", "all"),
            ("recall", "all"),
            ("f1", "all"),
            ("scenarios", "all"),
            ("scenarios", "novel"),
        ):
            row = _row(scope, detector, metric, subset)
            cells.append(_ci(row["value"], row["low"], row["high"]))
        lines.append(f"| {NAMES[detector]} | " + " | ".join(cells) + " |")
    return lines


def _row(scope: pd.DataFrame, detector: str, metric: str, subset: str) -> dict[str, float]:
    match = scope[
        (scope["detector"] == detector) & (scope["metric"] == metric) & (scope["subset"] == subset)
    ]
    return {k: float(match.iloc[0][k]) for k in ("value", "low", "high")}

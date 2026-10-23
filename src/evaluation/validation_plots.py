"""Figuras da Fase 7 (validação confirmatória e ablação), no estilo de `evaluation.plots`.

Mesma paleta e mesmos tokens das figuras do experimento (D42, D44): cada detector tem a sua
cor; o texto fica nas cores de texto. Pontos com intervalo, e não barras, porque o que importa
é a incerteza, e a escala acompanha os dados sem partir do zero. Cinza = cada simulação.
"""

from __future__ import annotations

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from evaluation.plots import (
    AXIS,
    DETECTOR_COLORS,
    DETECTOR_NAMES,
    GRID,
    INK,
    MUTED,
    SECONDARY,
    SURFACE,
    _figure_settings,
    _grid,
    _save,
    _style,
)
from evaluation.tuning import ModelConfig

PANELS = (("f1", "F1 (%)"), ("scenarios", "Cenários detectados (%)"))
SHORT_METRICS = {
    "precision": "precisão",
    "recall": "recall",
    "f1": "F1",
    "scenarios": "cenários detectados",
}
ROW_GAP = 0.22  # simulações logo abaixo do ponto principal, para não ficarem escondidas


def _comma(value: float, digits: int = 1) -> str:
    """Vírgula decimal e sinal de menos tipográfico (pt-BR)."""
    return f"{value:.{digits}f}".replace(".", ",").replace("-", "−")


def _write(
    out: Path, builders: list[tuple[str, Figure, str]], settings: dict[str, Any]
) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    captions = ["# Legendas sugeridas das figuras", ""]
    for name, figure, caption in builders:
        written += _save(figure, out, name, settings)
        captions.append(f"- `{name}`: {caption}")
    (out / "legendas.md").write_text("\n".join(captions) + "\n", encoding="utf-8")
    return written


# --- Validação confirmatória -----------------------------------------------------------------


def confirmatory_figures(
    out_dir: Path,
    intervals: pd.DataFrame,
    hypotheses: pd.DataFrame,
    descriptive: pd.DataFrame,
    confirmatory_runs: list[str],
    config: ModelConfig,
) -> list[Path]:
    settings = _figure_settings(config)
    _style(settings)
    pooled = intervals[intervals["scope"] == "agregado_confirmatorio"]
    per_run = intervals[intervals["scope"].isin(confirmatory_runs)]
    confidence = 100 * float(config.raw["stats"]["confidence"])
    builders = [
        (
            "confirmatorio_detectores",
            detectors_with_intervals(pooled, per_run, settings),
            f"Simulações confirmatórias ({len(confirmatory_runs)}, nunca vistas no "
            "desenvolvimento): cada ponto cinza é uma simulação; o ponto colorido e a barra são o "
            f"agregado e o seu intervalo de {confidence:.0f}% (bootstrap estratificado por "
            "simulação).",
        ),
        (
            "confirmatorio_diferencas",
            differences(hypotheses, descriptive, settings),
            f"Diferenças entre detectores no agregado confirmatório, em pontos percentuais, com "
            f"intervalo de {confidence:.0f}% (bootstrap pareado). Pontos cheios: hipóteses "
            "pré-registradas (D52), com o p ajustado por Holm; vazios: diferenças só descritivas.",
        ),
    ]
    return _write(out_dir / "figuras", builders, settings)


def detectors_with_intervals(
    pooled: pd.DataFrame, per_run: pd.DataFrame, settings: dict[str, Any]
) -> Figure:
    detectors = [d for d in DETECTOR_NAMES if d in set(pooled["detector"])]
    fig, axes = plt.subplots(1, 2, figsize=(settings["width_in"], 2.6), sharey=True)
    y = np.arange(len(detectors))[::-1]
    for ax, (metric, title) in zip(axes, PANELS, strict=True):
        rows = pooled[(pooled["metric"] == metric) & (pooled["subset"] == "all")]
        runs = per_run[(per_run["metric"] == metric) & (per_run["subset"] == "all")]
        every = 100 * pd.concat([rows[["low", "high"]].stack(), runs["value"]]).to_numpy(float)
        for yi, detector in zip(y, detectors, strict=True):
            values = 100 * runs.loc[runs["detector"] == detector, "value"].to_numpy(float)
            ax.scatter(values, np.full(len(values), yi - ROW_GAP), s=12, color=MUTED, zorder=2)
            row = rows[rows["detector"] == detector].iloc[0]
            value, low, high = (100 * float(row[k]) for k in ("value", "low", "high"))
            ax.errorbar(
                value,
                yi,
                xerr=[[value - low], [high - value]],
                fmt="none",
                ecolor=SECONDARY,
                elinewidth=0.8,
                capsize=2,
            )
            ax.scatter(
                [value],
                [yi],
                s=46,
                color=DETECTOR_COLORS[detector],
                edgecolors=SURFACE,
                linewidths=1.2,
                zorder=3,
            )
            ax.annotate(
                _comma(value),
                (high, yi),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                fontsize=7,
                color=SECONDARY,
            )
        ax.set_xlim(max(0.0, every.min() - 5), min(100.0, every.max() + 9))
        ax.set_ylim(-0.6, len(detectors) - 0.5)
        ax.set_title(title, fontsize=9, color=INK)
        _grid(ax, "x")
    axes[0].set_yticks(y, [DETECTOR_NAMES[d] for d in detectors])
    fig.tight_layout()
    return fig


def _comparison_label(row: dict[Hashable, Any], prefix: str) -> str:
    metric = SHORT_METRICS[row["metric"]]
    if row["subset"] == "novel":
        metric += " (inédito)"
    pair = f"{DETECTOR_NAMES[row['a']]} − {DETECTOR_NAMES[row['b']]}"
    return f"{prefix}{metric}\n{pair}"


def differences(
    hypotheses: pd.DataFrame, descriptive: pd.DataFrame, settings: dict[str, Any]
) -> Figure:
    rows: list[tuple[str, dict[Hashable, Any], bool]] = [
        (_comparison_label(r, f"{r['id']}: "), r, True) for r in hypotheses.to_dict("records")
    ] + [(_comparison_label(r, ""), r, False) for r in descriptive.to_dict("records")]
    fig, ax = plt.subplots(figsize=(settings["width_in"], 0.42 * len(rows) + 0.6))
    y = np.arange(len(rows))[::-1].astype(float)
    every = []
    for yi, (_, row, is_hypothesis) in zip(y, rows, strict=True):
        value, low, high = (100 * float(row[k]) for k in ("diff", "diff_low", "diff_high"))
        every += [low, high]
        color = DETECTOR_COLORS[row["a"]]
        ax.errorbar(
            value,
            yi,
            xerr=[[value - low], [high - value]],
            fmt="none",
            ecolor=SECONDARY,
            elinewidth=0.8,
            capsize=2,
        )
        ax.scatter(
            [value],
            [yi],
            s=46,
            color=color if is_hypothesis else SURFACE,
            edgecolors=color if not is_hypothesis else SURFACE,
            linewidths=1.4,
            zorder=3,
        )
        note = (
            f"{_comma(value)} p.p.; p (Holm) {_p_value(float(row['p_holm']))}"
            if is_hypothesis
            else f"{_comma(value)} p.p."
        )
        ax.annotate(
            note,
            (high, yi),
            xytext=(6, 0),
            textcoords="offset points",
            va="center",
            fontsize=7,
            color=SECONDARY,
        )
    ax.axvline(0, color=AXIS, linewidth=1.0, zorder=1)
    if len(hypotheses) and len(descriptive):
        # Linha entre as hipóteses pré-registradas e as diferenças só descritivas.
        ax.axhline(y[len(hypotheses) - 1] - 0.5, color=GRID, linewidth=0.8, zorder=1)
    span = max(every) - min(every)
    ax.set_xlim(min(0.0, min(every)) - 0.05 * span, max(0.0, max(every)) + 0.45 * span)
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_yticks(y, [label for label, _, _ in rows])
    ax.set_xlabel("Diferença (pontos percentuais)")
    _grid(ax, "x")
    fig.tight_layout()
    return fig


def _p_value(p: float) -> str:
    return "< 0,0001" if p < 1e-4 else ("= " + f"{p:.2g}".replace(".", ","))


# --- Ablação ---------------------------------------------------------------------------------

ABLATION_PANELS = (("f1", "Δ F1 (p.p.)"), ("scenario_recall", "Δ cenários detectados (p.p.)"))


def ablation_figures(
    out_dir: Path,
    by_run: pd.DataFrame,
    summary: pd.DataFrame,
    group_names: dict[str, str],
    auto_explain_group: str,
    config: ModelConfig,
) -> list[Path]:
    settings = _figure_settings(config)
    _style(settings)
    confidence = 100 * float(config.raw["stats"]["confidence"])
    runs = by_run["run_id"].nunique()
    builders = [
        (
            "ablacao_grupos",
            ablation_dots(by_run, summary, group_names, auto_explain_group, settings),
            f"Ablação na validação: variação em relação ao conjunto completo ao retirar cada "
            f"grupo de atributos do Isolation Forest (e, sem auto_explain, também das regras). "
            f"Pontos cinza: cada uma das {runs} simulações (média de "
            f"{len(config.tuning_seeds)} sementes); ponto colorido e barra: média e intervalo t "
            f"de {confidence:.0f}%. Melhor limiar da validação em cada caso.",
        )
    ]
    return _write(out_dir / "figuras", builders, settings)


def ablation_dots(
    by_run: pd.DataFrame,
    summary: pd.DataFrame,
    group_names: dict[str, str],
    auto_explain_group: str,
    settings: dict[str, Any],
) -> Figure:
    order = [
        (variant, "isolation_forest")
        for variant in summary.loc[summary["detector"] == "isolation_forest", "variant"]
        if variant != "completo"
    ] + [(auto_explain_group, "regras"), (auto_explain_group, "if_mais_regras")]
    labels = [
        f"{DETECTOR_NAMES[detector]}: sem {group_names.get(variant, variant).removeprefix('sem ')}"
        for variant, detector in order
    ]
    fig, axes = plt.subplots(
        1, 2, figsize=(settings["width_in"], 0.36 * len(order) + 0.9), sharey=True
    )
    y = np.arange(len(order))[::-1].astype(float)
    for ax, (measure, title) in zip(axes, ABLATION_PANELS, strict=True):
        every = [0.0]
        for yi, (variant, detector) in zip(y, order, strict=True):
            runs = by_run[(by_run["variant"] == variant) & (by_run["detector"] == detector)]
            values = 100 * runs[f"delta_{measure}"].to_numpy(float)
            row = summary[(summary["variant"] == variant) & (summary["detector"] == detector)]
            mean, low, high = (
                100 * float(row.iloc[0][f"delta_{measure}_{k}"]) for k in ("mean", "low", "high")
            )
            every += [*values, low, high]
            ax.scatter(values, np.full(len(values), yi - ROW_GAP), s=10, color=MUTED, zorder=2)
            ax.errorbar(
                mean,
                yi,
                xerr=[[mean - low], [high - mean]],
                fmt="none",
                ecolor=SECONDARY,
                elinewidth=0.8,
                capsize=2,
            )
            ax.scatter(
                [mean],
                [yi],
                s=40,
                color=DETECTOR_COLORS[detector],
                edgecolors=SURFACE,
                linewidths=1.2,
                zorder=3,
            )
        ax.axvline(0, color=AXIS, linewidth=1.0, zorder=1)
        span = max(every) - min(every)
        ax.set_xlim(min(every) - 0.08 * span, max(every) + 0.08 * span)
        ax.set_ylim(-0.6, len(order) - 0.4)
        ax.set_title(title, fontsize=9, color=INK)
        _grid(ax, "x")
    axes[0].set_yticks(y, labels)
    fig.tight_layout()
    return fig

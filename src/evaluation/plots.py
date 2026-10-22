"""Gráficos da avaliação para a monografia (pt-BR; PNG e PDF em <experimento>/figuras/).

Cores: paleta de referência da skill dataviz, validada com o script dela
(docs/decisoes.md, D42). Cada detector tem a mesma cor em todas as figuras; "ambos" usa a cor
da união IF ∪ regras. As curvas PR/ROC têm no máximo três séries, o limite em que a paleta
passa no teste de todos os pares. Magnitudes (mapa de calor) usam uma rampa de um só matiz.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, ScalarFormatter
from sklearn.metrics import precision_recall_curve, roc_curve

from evaluation.tuning import ModelConfig

log = logging.getLogger(__name__)
# O fontTools (usado ao embutir fontes no PDF) registra cada subconjunto em INFO.
logging.getLogger("fontTools").setLevel(logging.WARNING)

# Tokens da paleta de referência (modo claro: figuras impressas).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
DETECTOR_COLORS = {
    "isolation_forest": "#2a78d6",
    "if_sem_rotulos": "#eb6834",
    "regras": "#1baf7a",
    "if_mais_regras": "#eda100",
}
DETECTOR_NAMES = {
    "isolation_forest": "IF ajustado",
    "if_sem_rotulos": "IF sem rótulos",
    "regras": "Regras",
    "if_mais_regras": "IF ∪ regras",
}
SEQUENTIAL = [
    "#cde2fb",
    "#b7d3f6",
    "#9ec5f4",
    "#86b6ef",
    "#6da7ec",
    "#5598e7",
    "#3987e5",
    "#2a78d6",
    "#256abf",
    "#1c5cab",
    "#184f95",
    "#104281",
    "#0d366b",
]
TYPE_LABELS = {
    "dml_incomum": "DML incomum",
    "falha_auth": "falha de autenticação",
    "fora_horario": "fora do horário",
    "leitura_atipica": "leitura atípica (inédito)",
    "rajada": "rajada",
    "sem_permissao": "sem permissão",
    "volume_alto": "volume alto",
}
INTENSITIES = {"baixa": "baixa", "media": "média", "alta": "alta"}
PARAM_LABELS = {
    "n_estimators": "n_estimators",
    "max_samples": "max_samples",
    "max_features": "max_features",
    "log1p": "log(1 + x)",
}


def make_figures(exp_dir: Path, robustness_dir: Path | None, config: ModelConfig) -> list[Path]:
    """Todas as figuras do experimento; devolve os arquivos gravados.

    Cada figura é criada e gravada antes da seguinte (uma só na memória por vez).
    """
    settings = _figure_settings(config)
    out = exp_dir / "figuras"
    out.mkdir(parents=True, exist_ok=True)
    tables = exp_dir / "tables"
    scores = pd.read_parquet(exp_dir / "scores" / "test_scores.parquet")
    metrics = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    tuned_seeds = pd.read_csv(tables / "test_seeds.csv")
    label_free_seeds = pd.read_csv(tables / "test_label_free_seeds.csv")
    seed = int(tuned_seeds["seed"].iloc[0])  # a semente cujo modelo e scores foram salvos
    _style(settings)
    builders: list[tuple[str, Callable[[], Figure], str]] = [
        (
            "comparacao_detectores",
            lambda: detector_comparison(pd.read_csv(tables / "test_comparison.csv"), settings),
            f"Desempenho no teste (média ± desvio nas {len(tuned_seeds)} sementes do modelo; as "
            "regras são determinísticas). Rótulo: F1.",
        ),
        (
            "curvas_pr_roc",
            lambda: pr_roc_curves(scores, tuned_seeds, label_free_seeds, seed, settings),
            f"Curvas precisão–recall e ROC no teste (semente {seed}). Os pontos marcam o limiar "
            "usado por cada detector; as regras são um único ponto.",
        ),
        (
            "scores_por_tipo",
            lambda: scores_by_type(scores, tuned_seeds, seed, settings),
            f"Score de anomalia das janelas de teste por tipo de cenário (semente {seed}). "
            "Caixas: quartis; bigodes: percentis 1 e 99; pontos: janelas anômalas. Linha: limiar.",
        ),
        (
            "deteccao_tipo_intensidade",
            lambda: detection_heatmaps(
                pd.read_csv(tables / "test_scenarios_by_type.csv"), settings
            ),
            "Cenários detectados (%) no teste, por tipo e intensidade (média nas sementes). Um "
            "cenário é detectado se ao menos uma de suas janelas é sinalizada.",
        ),
        (
            "cenarios_if_regras",
            lambda: overlap_bars(pd.read_csv(tables / "test_scenario_overlap.csv"), settings),
            "Cenários de teste detectados pelo Isolation Forest (maioria das sementes), pelas "
            "regras, por ambos ou por nenhum.",
        ),
        (
            "sensibilidade_hiperparametros",
            lambda: hyperparameter_panels(
                pd.read_csv(tables / "sensitivity_hyperparameters.csv"), config, settings
            ),
            "F1 na validação variando um hiperparâmetro por vez (demais na configuração "
            f"escolhida, em azul); média ± desvio nas {len(config.tuning_seeds)} sementes de "
            "ajuste.",
        ),
        (
            "sensibilidade_janela",
            lambda: window_panels(
                pd.read_csv(tables / "sensitivity_window.csv"), metrics, settings
            ),
            "Tamanho da janela na validação: F1, cenários detectados e alertas (cada tamanho com "
            "a sua melhor configuração). O F1 não é comparável entre tamanhos: a unidade muda.",
        ),
        (
            "tempos_etapas",
            lambda: timing_bars(metrics["timings"], _report_seconds(exp_dir), settings),
            "Tempo de cada etapa do SACLA sobre todo o log da simulação (treino e score: média "
            "das sementes).",
        ),
    ]
    runs_path = robustness_dir / "robustness_runs.csv" if robustness_dir else None
    if runs_path is not None and runs_path.exists():
        builders.append(
            (
                "robustez_simulacoes",
                lambda: robustness_dots(pd.read_csv(runs_path), settings),
                "Robustez à semente da simulação: cada ponto cinza é uma simulação (média das "
                "sementes do modelo); o ponto colorido e a barra são a média ± desvio entre elas.",
            )
        )
    written: list[Path] = []
    captions = ["# Legendas sugeridas das figuras", ""]
    for name, build, caption in builders:
        written += _save(build(), out, name, settings)
        captions += [f"- `{name}`: {caption}"]
    (out / "legendas.md").write_text("\n".join(captions) + "\n", encoding="utf-8")
    log.info("%d figuras em %s", len(builders), out)
    return written


# --- Figuras -------------------------------------------------------------------------------


def detector_comparison(comparison: pd.DataFrame, settings: dict[str, Any]) -> Figure:
    metrics = [
        ("precision", "Precisão"),
        ("recall", "Recall"),
        ("f1", "F1"),
        ("scenario_recall", "Cenários\ndetectados"),
    ]
    fig, ax = plt.subplots(figsize=(settings["width_in"], 3.2))
    detectors = list(comparison["detector"])
    width = 0.8 / len(detectors)
    x = np.arange(len(metrics))
    for i, row in enumerate(comparison.to_dict(orient="records")):
        means = np.array([100 * row[f"{m}_mean"] for m, _ in metrics])
        stds = np.nan_to_num(np.array([100 * row[f"{m}_std"] for m, _ in metrics]))
        offset = (i - (len(detectors) - 1) / 2) * width
        ax.bar(
            x + offset,
            means,
            width,
            color=DETECTOR_COLORS[row["detector"]],
            edgecolor=SURFACE,
            linewidth=1.2,
            label=DETECTOR_NAMES[row["detector"]],
            zorder=2,
        )
        ax.errorbar(
            x + offset, means, yerr=stds, fmt="none", ecolor=SECONDARY, elinewidth=0.8, zorder=3
        )
        f1_index = 2  # rótulo só no F1, a métrica principal
        ax.annotate(
            f"{means[f1_index]:.0f}",
            (x[f1_index] + offset, means[f1_index] + stds[f1_index]),
            xytext=(0, 2),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=7,
            color=SECONDARY,
        )
    ax.set_xticks(x, [label for _, label in metrics])
    ax.set_ylim(0, 100)
    ax.set_ylabel("%")
    ax.legend(ncols=len(detectors), loc="upper center", bbox_to_anchor=(0.5, 1.14), frameon=False)
    _grid(ax, "y")
    return fig


def pr_roc_curves(
    scores: pd.DataFrame,
    tuned_seeds: pd.DataFrame,
    label_free_seeds: pd.DataFrame,
    seed: int,
    settings: dict[str, Any],
) -> Figure:
    labels = scores["label"].to_numpy()
    prevalence = float(labels.mean())
    fig, (pr, roc) = plt.subplots(1, 2, figsize=(settings["width_in"], 3.0))
    series = [
        (
            "isolation_forest",
            scores[f"score_seed{seed}"].to_numpy(),
            _threshold(tuned_seeds, seed),
        ),
        (
            "if_sem_rotulos",
            scores[f"score_lf_seed{seed}"].to_numpy(),
            _threshold(label_free_seeds, seed),
        ),
    ]
    for name, values, threshold in series:
        color = DETECTOR_COLORS[name]
        precision, recall, _ = precision_recall_curve(labels, values)
        fpr, tpr, _ = roc_curve(labels, values)
        pr.plot(recall, precision, color=color, lw=1.6, label=DETECTOR_NAMES[name])
        roc.plot(fpr, tpr, color=color, lw=1.6, label=DETECTOR_NAMES[name])
        point = _operating_point(labels, values >= threshold)
        _dot(pr, point["recall"], point["precision"], color)
        _dot(roc, point["fpr"], point["recall"], color)
    rules = _operating_point(labels, scores["regra_any"].to_numpy(dtype=bool))
    _dot(pr, rules["recall"], rules["precision"], DETECTOR_COLORS["regras"], marker="D")
    _dot(roc, rules["fpr"], rules["recall"], DETECTOR_COLORS["regras"], marker="D")
    pr.plot([], [], ls="none", marker="D", color=DETECTOR_COLORS["regras"], label="Regras")
    pr.axhline(prevalence, color=MUTED, lw=0.8)
    pr.annotate(
        f"prevalência ({100 * prevalence:.1f}%)".replace(".", ","),
        (0.02, prevalence),
        xytext=(0, 3),
        textcoords="offset points",
        ha="left",
        fontsize=7,
        color=SECONDARY,
    )
    roc.plot([0, 1], [0, 1], color=MUTED, lw=0.8)
    pr.set(xlabel="Recall", ylabel="Precisão", xlim=(0, 1), ylim=(0, 1.02))
    roc.set(xlabel="Taxa de falsos positivos", ylabel="Recall", xlim=(0, 1), ylim=(0, 1.02))
    pr.set_title("Precisão–recall", fontsize=9, color=INK)
    roc.set_title("ROC", fontsize=9, color=INK)
    handles, names = pr.get_legend_handles_labels()
    fig.legend(
        handles, names, ncols=3, loc="upper center", bbox_to_anchor=(0.5, 1.06), frameon=False
    )
    for ax in (pr, roc):
        _grid(ax, "both")
    fig.tight_layout()
    return fig


def scores_by_type(
    scores: pd.DataFrame, tuned_seeds: pd.DataFrame, seed: int, settings: dict[str, Any]
) -> Figure:
    threshold = _threshold(tuned_seeds, seed)
    values = scores[f"score_seed{seed}"].to_numpy()
    normal = values[scores["label"].to_numpy() == 0]
    groups = [("normal", normal)]
    for name in sorted(TYPE_LABELS, key=lambda t: TYPE_LABELS[t]):
        mask = (scores["anomaly_type"] == name).to_numpy()
        if mask.any():
            groups.append((TYPE_LABELS[name], values[mask]))
    fig, ax = plt.subplots(figsize=(settings["width_in"], 3.6))
    positions = np.arange(len(groups))[::-1]
    for (label, data), y in zip(groups, positions, strict=True):
        is_normal = label == "normal"
        color = AXIS if is_normal else DETECTOR_COLORS["isolation_forest"]
        ax.boxplot(
            data,
            positions=[y],
            orientation="horizontal",
            widths=0.5,
            whis=(1, 99),
            showfliers=False,
            patch_artist=True,
            boxprops={"facecolor": color, "edgecolor": SECONDARY, "linewidth": 0.8, "alpha": 0.35},
            medianprops={"color": INK, "linewidth": 1.2},
            whiskerprops={"color": SECONDARY, "linewidth": 0.8},
            capprops={"color": SECONDARY, "linewidth": 0.8},
        )
        if not is_normal:
            jitter = np.random.default_rng(0).uniform(-0.18, 0.18, size=len(data))
            ax.scatter(
                data, y + jitter, s=9, color=color, edgecolors=SURFACE, linewidths=0.4, zorder=3
            )
    above = int((normal >= threshold).sum())
    ax.axvline(threshold, color=INK, lw=0.9)
    ax.annotate(
        f"limiar · {above} janelas normais acima",
        (threshold, positions[0] + 0.45),
        xytext=(4, 0),
        textcoords="offset points",
        fontsize=7,
        color=SECONDARY,
        va="center",
    )
    ax.set_yticks(positions, [f"{label} ({len(data)})" for label, data in groups])
    ax.set_xlabel("Score de anomalia (maior = mais atípico)")
    _grid(ax, "x")
    return fig


def detection_heatmaps(table: pd.DataFrame, settings: dict[str, Any]) -> Figure:
    cmap = LinearSegmentedColormap.from_list("sequencial_azul", SEQUENTIAL)
    types = sorted(table["type"].unique(), key=lambda t: TYPE_LABELS.get(t, t))
    fig, axes = plt.subplots(2, 2, figsize=(settings["width_in"], 5.4), sharex=True, sharey=True)
    image = None
    for ax, name in zip(axes.flat, DETECTOR_NAMES, strict=True):
        grid = np.full((len(types), len(INTENSITIES)), np.nan)
        for i, t in enumerate(types):
            for j, intensity in enumerate(INTENSITIES):
                row = table[(table["type"] == t) & (table["intensity"] == intensity)]
                if not row.empty:
                    grid[i, j] = 100 * float(row.iloc[0][f"{name}_mean"])
        image = ax.imshow(grid, cmap=cmap, vmin=0, vmax=100, aspect="auto")
        for i in range(len(types)):
            for j in range(len(INTENSITIES)):
                value = grid[i, j]
                ax.text(
                    j,
                    i,
                    f"{value:.0f}",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if value >= 55 else INK,
                )
        ax.set_title(DETECTOR_NAMES[name], fontsize=9, color=INK)
        ax.set_xticks(range(len(INTENSITIES)), list(INTENSITIES.values()))
        ax.set_yticks(range(len(types)), [TYPE_LABELS.get(t, t) for t in types])
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
    assert image is not None
    fig.tight_layout(rect=(0, 0, 0.9, 1))
    bar = fig.colorbar(image, ax=axes, fraction=0.03, pad=0.03)
    bar.set_label("cenários detectados (%)")
    bar.outline.set_visible(False)
    return fig


def overlap_bars(overlap: pd.DataFrame, settings: dict[str, Any]) -> Figure:
    parts = [
        ("ambos", "ambos", DETECTOR_COLORS["if_mais_regras"], INK),
        ("so_isolation_forest", "só IF", DETECTOR_COLORS["isolation_forest"], "white"),
        ("so_regras", "só regras", DETECTOR_COLORS["regras"], INK),
        ("nenhum", "nenhum", AXIS, INK),
    ]
    table = overlap.assign(label=overlap["type"].map(lambda t: TYPE_LABELS.get(t, t)))
    table = table.sort_values("label", ascending=False)
    fig, ax = plt.subplots(figsize=(settings["width_in"], 3.0))
    left = np.zeros(len(table))
    y = np.arange(len(table))
    for column, label, color, text_color in parts:
        values = table[column].to_numpy(dtype=float)
        ax.barh(
            y,
            values,
            left=left,
            height=0.6,
            color=color,
            edgecolor=SURFACE,
            linewidth=1.5,
            label=label,
        )
        for yi, (start, value) in enumerate(zip(left, values, strict=True)):
            if value >= 2:  # cabe no segmento com folga; o resto fica na legenda e na tabela
                ax.text(
                    start + value / 2,
                    yi,
                    f"{value:.0f}",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color=text_color,
                )
        left += values
    ax.set_yticks(y, list(table["label"]))
    ax.set_xlabel("Cenários de teste")
    ax.legend(ncols=4, loc="upper center", bbox_to_anchor=(0.5, 1.16), frameon=False)
    _grid(ax, "x")
    return fig


def hyperparameter_panels(
    hyper: pd.DataFrame, config: ModelConfig, settings: dict[str, Any]
) -> Figure:
    names = list(PARAM_LABELS)
    fig, axes = plt.subplots(1, len(names), figsize=(settings["width_in"], 2.6), sharey=True)
    for ax, name in zip(axes, names, strict=True):
        rows = hyper[hyper["hyperparameter"] == name].set_index("value")
        order = [str(v) for v in config.grid[name]]
        rows = rows.reindex([v for v in order if v in rows.index])
        x = np.arange(len(rows))
        colors = [DETECTOR_COLORS["isolation_forest"] if c else MUTED for c in rows["chosen"]]
        ax.errorbar(
            x,
            100 * rows["f1_mean"],
            yerr=100 * rows["f1_std"],
            fmt="none",
            ecolor=SECONDARY,
            elinewidth=0.8,
            capsize=2,
        )
        ax.scatter(x, 100 * rows["f1_mean"], s=36, color=colors, edgecolors=SURFACE, zorder=3)
        ticks = [{"True": "sim", "False": "não"}.get(v, v) for v in rows.index]
        ax.set_xticks(x, ticks)
        ax.set_xlim(-0.5, len(rows) - 0.5)
        ax.set_title(PARAM_LABELS[name], fontsize=9, color=INK)
        _grid(ax, "y")
    axes[0].set_ylabel("F1 na validação (%)")
    fig.tight_layout()
    return fig


def window_panels(
    windows: pd.DataFrame, metrics: dict[str, Any], settings: dict[str, Any]
) -> Figure:
    chosen = int(metrics["window_minutes"])
    panels = [
        ("f1_mean", "F1 (%)", 100.0, "{:.0f}"),
        ("scenario_recall_mean", "Cenários detectados (%)", 100.0, "{:.0f}"),
        ("alerts_mean", "Alertas na validação", 1.0, "{:.0f}"),
    ]
    fig, axes = plt.subplots(1, len(panels), figsize=(settings["width_in"], 2.6))
    x = np.arange(len(windows))
    colors = [
        DETECTOR_COLORS["isolation_forest"] if w == chosen else AXIS
        for w in windows["window_minutes"]
    ]
    for ax, (column, title, scale, fmt) in zip(axes, panels, strict=True):
        values = scale * windows[column].to_numpy(dtype=float)
        ax.bar(x, values, width=0.55, color=colors, edgecolor=SURFACE, linewidth=1.2, zorder=2)
        for xi, value in zip(x, values, strict=True):
            ax.annotate(
                fmt.format(value),
                (xi, value),
                xytext=(0, 2),
                textcoords="offset points",
                ha="center",
                fontsize=7,
                color=SECONDARY,
            )
        ax.set_xticks(x, [f"{w} min" for w in windows["window_minutes"]])
        ax.set_title(title, fontsize=9, color=INK)
        ax.set_ylim(0, max(values) * 1.18)
        _grid(ax, "y")
    fig.tight_layout()
    return fig


def timing_bars(
    timings: dict[str, float], report_s: float | None, settings: dict[str, Any]
) -> Figure:
    stages = [
        ("leitura do log e eventos", timings["leitura_e_eventos_s"]),
        ("agregação por janela", timings["agregacao_s"]),
        ("treino do modelo", timings["treino_modelo_s_medio"]),
        ("score do teste", timings["score_teste_s_medio"]),
    ]
    if report_s is not None:
        stages.append(("relatório (explicações)", report_s))
    fig, ax = plt.subplots(figsize=(settings["width_in"], 2.2))
    y = np.arange(len(stages))[::-1]
    values = [v for _, v in stages]
    ax.barh(y, values, height=0.5, color=DETECTOR_COLORS["isolation_forest"], zorder=2)
    for yi, value in zip(y, values, strict=True):
        text = f"{value:.2f} s" if value < 1 else f"{value:.1f} s"
        ax.annotate(
            text.replace(".", ","),
            (value, yi),
            xytext=(3, 0),
            textcoords="offset points",
            va="center",
            fontsize=7,
            color=SECONDARY,
        )
    ax.set_yticks(y, [name for name, _ in stages])
    ax.set_xlabel("Segundos")
    ax.set_xlim(0, max(values) * 1.15)
    _grid(ax, "x")
    return fig


def robustness_dots(runs: pd.DataFrame, settings: dict[str, Any]) -> Figure:
    panels = [("f1_mean", "F1 (%)"), ("scenario_recall_mean", "Cenários detectados (%)")]
    detectors = [d for d in DETECTOR_NAMES if d in set(runs["detector"])]
    fig, axes = plt.subplots(1, 2, figsize=(settings["width_in"], 2.6), sharey=True)
    y = np.arange(len(detectors))[::-1]
    for ax, (column, title) in zip(axes, panels, strict=True):
        every = 100 * runs[column].to_numpy(dtype=float)
        for yi, name in zip(y, detectors, strict=True):
            values = 100 * runs.loc[runs["detector"] == name, column].to_numpy(dtype=float)
            # Simulações logo abaixo da média, para não ficarem escondidas atrás dela.
            ax.scatter(values, np.full(len(values), yi - 0.22), s=12, color=MUTED, zorder=2)
            mean, std = values.mean(), values.std(ddof=1)
            ax.errorbar(mean, yi, xerr=std, fmt="none", ecolor=SECONDARY, elinewidth=0.8, capsize=2)
            ax.scatter(
                [mean],
                [yi],
                s=46,
                color=DETECTOR_COLORS[name],
                edgecolors=SURFACE,
                linewidths=1.2,
                zorder=3,
            )
            ax.annotate(
                f"{mean:.1f} ± {std:.1f}".replace(".", ","),
                (mean + std, yi),
                xytext=(6, 0),
                textcoords="offset points",
                va="center",
                fontsize=7,
                color=SECONDARY,
            )
        # Pontos (não barras): a escala acompanha os dados, sem precisar partir do zero.
        ax.set_xlim(max(0.0, every.min() - 5), min(100.0, every.max() + 12))
        ax.set_ylim(-0.5, len(detectors) - 0.6)
        ax.set_title(title, fontsize=9, color=INK)
        _grid(ax, "x")
    axes[0].set_yticks(y, [DETECTOR_NAMES[d] for d in detectors])
    fig.tight_layout()
    return fig


# --- Auxiliares ----------------------------------------------------------------------------


def _figure_settings(config: ModelConfig) -> dict[str, Any]:
    raw = config.raw.get("figures") or {}
    try:
        return {
            "dpi": int(raw["dpi"]),
            "formats": [str(f) for f in raw["formats"]],
            "width_in": float(raw["width_in"]),
            "font_size": float(raw["font_size"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise KeyError(f"model.yaml: seção figures incompleta ({exc!r})") from exc


def _style(settings: dict[str, Any]) -> None:
    plt.rcParams.update(
        {
            "font.size": settings["font_size"],
            "font.family": "sans-serif",
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": SECONDARY,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.color": SECONDARY,
            "ytick.color": SECONDARY,
            "xtick.major.size": 0,
            "ytick.major.size": 0,
            "legend.fontsize": settings["font_size"] - 1,
            "text.color": INK,
            "pdf.fonttype": 42,  # fontes TrueType embutidas (texto selecionável no PDF)
        }
    )


def _grid(ax: Axes, axis: str) -> None:
    ax.grid(True, axis=axis, color=GRID, linewidth=0.6, linestyle="-")  # type: ignore[arg-type]
    ax.set_axisbelow(True)


def _dot(ax: Axes, x: float, y: float, color: str, marker: str = "o") -> None:
    ax.scatter(
        [x],
        [y],
        s=40,
        marker=marker,
        color=color,
        edgecolors=SURFACE,
        linewidths=1.2,
        zorder=4,
        clip_on=False,  # pontos na borda (taxa de FP ~0) não são cortados
    )


def _threshold(seeds: pd.DataFrame, seed: int) -> float:
    return float(seeds.loc[seeds["seed"] == seed, "threshold"].iloc[0])


def _operating_point(labels: np.ndarray[Any, Any], flags: np.ndarray[Any, Any]) -> dict[str, float]:
    tp = float((flags & (labels == 1)).sum())
    fp = float((flags & (labels == 0)).sum())
    positives = float((labels == 1).sum())
    negatives = float((labels == 0).sum())
    return {
        "precision": tp / max(tp + fp, 1.0),
        "recall": tp / max(positives, 1.0),
        "fpr": fp / max(negatives, 1.0),
    }


def _report_seconds(exp_dir: Path) -> float | None:
    path = exp_dir / "relatorio" / "tempo.json"
    if not path.exists():
        return None
    return float(json.loads(path.read_text(encoding="utf-8"))["relatorio_s"])


def _decimal_comma(fig: Figure) -> None:
    """Vírgula decimal (pt-BR) nos eixos numéricos; eixos com rótulos fixos ficam como estão."""
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            if isinstance(axis.get_major_formatter(), ScalarFormatter):
                axis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}".replace(".", ",")))


def _save(fig: Figure, out: Path, name: str, settings: dict[str, Any]) -> list[Path]:
    _decimal_comma(fig)
    paths = []
    for fmt in settings["formats"]:
        path = out / f"{name}.{fmt}"
        # Sem data de criação no PDF: refazer as figuras não muda os arquivos à toa.
        metadata = {"CreationDate": None} if fmt == "pdf" else {"Software": None}
        fig.savefig(path, dpi=settings["dpi"], bbox_inches="tight", metadata=metadata)
        paths.append(path)
    plt.close(fig)
    return paths

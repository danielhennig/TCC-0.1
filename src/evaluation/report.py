"""Relatório de anomalias do experimento: o relatório do SACLA sobre o período de teste.

Usa o modelo avaliado (semente 0, limiar escolhido na validação) e o mesmo código do uso real
(`sacla.report`): o HTML e os CSVs não têm rótulos. À parte, para a monografia, um anexo cruza
cada alerta e episódio com o ground truth (manifesto) — é a única saída que usa rótulos.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluation.experiment import load_split
from evaluation.tuning import load_model_config
from sacla.config import PROJECT_ROOT, ConfigError, SaclaSettings
from sacla.explain import fit_reference
from sacla.model import SaclaModel
from sacla.report import ReportContext, build_report, display_time, local_stamp, write_report

log = logging.getLogger(__name__)


def experiment_report(
    exp_dir: Path, data_root: Path, features_root: Path, settings: SaclaSettings
) -> dict[str, Any]:
    metrics = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
    run_id, window = metrics["run_id"], int(metrics["window_minutes"])
    config = load_model_config(settings=settings)
    data, _ = load_split(features_root / run_id, data_root / run_id, settings, window)
    train, test = data[data["split"] == "train"], data[data["split"] == "test"]
    # O modelo salvo pela avaliação é o da primeira semente de avaliação.
    seed = config.evaluation_seeds[0]
    model_path = PROJECT_ROOT / config.models_dir / exp_dir.name / f"model_seed{seed}.joblib"
    if not model_path.exists():
        raise FileNotFoundError(f"modelo não encontrado: {model_path} (rode `tasks.py evaluate`)")
    model = SaclaModel.load(model_path)
    if model.threshold is None:
        raise ConfigError(f"{model_path} sem limiar")

    started = time.perf_counter()
    scores = model.score(test)
    _check_same_scores(exp_dir, test, scores, seed)
    train_scores = np.sort(model.score(train))
    tz = settings.features.timezone
    context = ReportContext(
        title=f"SACLA — relatório de acessos atípicos ({run_id}, período de teste)",
        period=(
            f"{display_time(local_stamp(int(test['window_start_ms'].min()), tz))} a "
            f"{display_time(local_stamp(int(test['window_end_ms'].max()), tz))} ({tz})"
        ),
        window_minutes=window,
        windows=len(test),
        users=int(test["user"].nunique()),
        threshold=model.threshold,
        threshold_origin=f"escolhido na validação (melhor F1), semente {seed} do experimento",
        model=model.params.slug(),
        history=(
            f"período de treino da simulação: {len(train)} janelas, "
            f"{train['user'].nunique()} usuários"
        ),
    )
    report = build_report(
        test,
        scores,
        model.threshold,
        train_scores,
        fit_reference(train, settings.report.explain_features),
        settings,
        context,
    )
    out = exp_dir / "relatorio"
    paths = write_report(report, out, settings)
    report_s = time.perf_counter() - started
    (out / "tempo.json").write_text(
        json.dumps({"relatorio_s": round(report_s, 3)}) + "\n", encoding="utf-8"
    )

    annex = _ground_truth_annex(report.alerts, report.episodes, test)
    annex["alerts"].to_csv(out / "anexo_alertas_vs_manifesto.csv", index=False, lineterminator="\n")
    annex["episodes"].to_csv(
        out / "anexo_episodios_vs_manifesto.csv", index=False, lineterminator="\n"
    )
    summary = {
        "semente": seed,
        "janelas_sinalizadas": len(report.alerts),
        "episodios": len(report.episodes),
        "episodios_com_cenario": int(annex["episodes"]["tem_cenario"].sum()),
        "episodios_sem_cenario": int((~annex["episodes"]["tem_cenario"]).sum()),
        "falsos_positivos_por_usuario": annex["fp_by_user"],
        "relatorio_s": round(report_s, 3),
        "arquivos": {k: str(v.relative_to(PROJECT_ROOT)) for k, v in paths.items()},
        "uso_real": _check_detector_settings(metrics, settings, train_scores, model.threshold),
    }
    (out / "resumo_anexo.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log.info("relatório do teste em %s", out.relative_to(PROJECT_ROOT))
    return summary


def _check_same_scores(
    exp_dir: Path, test: pd.DataFrame, scores: np.ndarray[Any, Any], seed: int
) -> None:
    """O modelo salvo tem de reproduzir os scores gravados pela avaliação."""
    column = f"score_seed{seed}"
    saved = pd.read_parquet(exp_dir / "scores" / "test_scores.parquet")
    if column not in saved:
        raise ConfigError(f"{column} ausente nos scores da avaliação; rode `evaluate`")
    merged = (
        test[["user", "window_start_ms"]]
        .assign(now=scores)
        .merge(saved[["user", "window_start_ms", column]], on=["user", "window_start_ms"])
    )
    if len(merged) != len(test) or not np.allclose(merged["now"], merged[column]):
        raise ConfigError("modelo salvo não reproduz os scores da avaliação; rode `evaluate`")


def _check_detector_settings(
    metrics: dict[str, Any],
    settings: SaclaSettings,
    train_scores: np.ndarray[Any, Any],
    threshold: float,
) -> dict[str, Any]:
    """Confere se o detector do uso real (sacla.yaml) é o escolhido neste experimento.

    Os valores de `detector` em sacla.yaml são copiados do experimento (D39, D40); se a
    avaliação for refeita e escolher outra configuração, o aviso diz o que atualizar.
    """
    detector = settings.detector
    configured = {
        "n_estimators": detector.n_estimators,
        "max_samples": detector.max_samples,
        "max_features": detector.max_features,
        "log1p": detector.log1p,
    }
    chosen = metrics["chosen_params"]
    rate = float((train_scores >= threshold).mean())
    same_params = all(str(configured[k]) == str(chosen[k]) for k in configured)
    same_rate = round(rate, 4) == round(detector.train_alert_rate, 4)
    if not (same_params and same_rate):
        log.warning(
            "config/sacla.yaml (detector) difere deste experimento: escolhidos %s e taxa de "
            "alertas no treino %.4f; configurados %s e %.4f",
            chosen,
            rate,
            configured,
            detector.train_alert_rate,
        )
    return {
        "parametros_escolhidos": chosen,
        "parametros_sacla_yaml": configured,
        "taxa_alertas_treino_equivalente": round(rate, 4),
        "taxa_alertas_sacla_yaml": detector.train_alert_rate,
        "coerente": same_params and same_rate,
    }


def _ground_truth_annex(
    alerts: pd.DataFrame, episodes: pd.DataFrame, test: pd.DataFrame
) -> dict[str, Any]:
    truth = test[["user", "window_start_ms", "label", "anomaly_type", "intensity", "scenario_ids"]]
    joined = alerts.merge(
        truth.rename(columns={"user": "usuario"}), on=["usuario", "window_start_ms"], how="left"
    )
    joined["scenario_ids"] = joined["scenario_ids"].map(_ids_text)
    per_episode = joined.groupby("episodio").agg(
        tem_cenario=("label", "max"),
        tipos=("anomaly_type", lambda s: "|".join(sorted({str(t) for t in s.dropna()}))),
        cenarios=(
            "scenario_ids",
            lambda s: "|".join(sorted({i for v in s for i in v.split("|") if i})),
        ),
    )
    per_episode["tem_cenario"] = per_episode["tem_cenario"].astype(bool)
    episodes_annex = episodes[
        ["episodio", "usuario", "inicio", "fim", "janelas", "score_max"]
    ].merge(per_episode.reset_index(), on="episodio")
    false_alerts = joined[joined["label"] == 0]
    return {
        "alerts": joined[
            [
                "episodio",
                "usuario",
                "inicio",
                "score",
                "posicao",
                "label",
                "anomaly_type",
                "intensity",
                "scenario_ids",
                "explicacao",
            ]
        ],
        "episodes": episodes_annex,
        "fp_by_user": {str(k): int(v) for k, v in false_alerts["usuario"].value_counts().items()},
    }


def _ids_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if isinstance(value, (list, tuple, np.ndarray)):
        return "|".join(str(v) for v in value)
    return str(value)

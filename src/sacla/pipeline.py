"""Fluxo do SACLA no uso real: logs → eventos → janelas → Isolation Forest → relatório.

`fit`: lê o log de um período considerado normal (o histórico), monta as janelas, ajusta os
históricos por usuário e o Isolation Forest e define o limiar sem rótulos (a fração
`detector.train_alert_rate` das janelas do próprio histórico fica acima dele).
`score`: lê o log de um novo período, monta as janelas com os históricos já ajustados, pontua
e gera o relatório. Nada aqui usa rótulos.
"""

from __future__ import annotations

import logging
import pickle
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from sacla import __version__
from sacla.config import ConfigError, ReportSettings, SaclaSettings
from sacla.events import ParseReport, extract_events
from sacla.explain import UserReference, fit_reference
from sacla.features import UserBaseline, build_features, features_with_baselines
from sacla.model import ModelParams, SaclaModel
from sacla.reader import ReadReport, list_log_files, read_records
from sacla.report import Report, ReportContext, build_report, display_time, local_stamp

log = logging.getLogger(__name__)


@dataclass
class Detector:
    """Tudo o que `score` precisa, salvo por `fit` num único arquivo (joblib)."""

    settings: SaclaSettings  # configuração usada no ajuste (janela, fuso, aliases...)
    model: SaclaModel  # com o limiar definido
    baselines: dict[str, UserBaseline]  # atributos relativos ao histórico
    references: dict[str, UserReference]  # explicação dos alertas
    train_scores: np.ndarray[Any, Any] = field(repr=False)  # scores do histórico, ordenados
    history_start_ms: int = 0
    history_end_ms: int = 0
    history_windows: int = 0
    version: str = __version__

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> Detector:
        if not path.exists():
            raise FileNotFoundError(f"modelo não encontrado: {path} (rode `sacla fit` antes)")
        try:
            detector = joblib.load(path)
        except (pickle.UnpicklingError, EOFError, AttributeError, ImportError, ValueError) as exc:
            raise ConfigError(f"{path} ilegível ou de outra versão do SACLA ({exc!r})") from exc
        if not isinstance(detector, Detector):
            raise ConfigError(f"{path} não contém um detector do SACLA")
        if detector.version != __version__:
            raise ConfigError(
                f"{path} foi ajustado pela versão {detector.version} do SACLA (atual: "
                f"{__version__}); rode `sacla fit` de novo"
            )
        return detector

    def history_text(self) -> str:
        tz = self.settings.features.timezone
        return (
            f"{display_time(local_stamp(self.history_start_ms, tz))} a "
            f"{display_time(local_stamp(self.history_end_ms, tz))} ({tz}), "
            f"{self.history_windows} janelas, {len(self.baselines)} usuários"
        )


@dataclass
class LoadedEvents:
    events: pd.DataFrame
    read: ReadReport
    parse: ParseReport


def parse_instant(value: str | None) -> int | None:
    """Data e hora ISO 8601 com fuso (ex.: 2026-03-16T03:00:00Z) -> ms UTC."""
    if value is None:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"data e hora inválida: {value!r} (use ISO 8601)") from exc
    if stamp.tzinfo is None:
        raise ConfigError(f"informe o fuso em {value!r} (ex.: Z ou -03:00)")
    return int(stamp.timestamp() * 1000)


def load_events(
    logs: Path, settings: SaclaSettings, since_ms: int | None = None, until_ms: int | None = None
) -> LoadedEvents:
    """Eventos do log em [since, until) (módulos 1 e 2a)."""
    files = list_log_files(logs, settings.reader.patterns)
    if not files:
        raise FileNotFoundError(f"nenhum arquivo de log em {logs} ({settings.reader.patterns})")
    read, parse = ReadReport(), ParseReport()
    records = read_records(files, read, settings.reader.zone_offsets_min)
    events = extract_events(records, settings.events, parse)
    keep = np.ones(len(events), dtype=bool)
    if since_ms is not None:
        keep &= events["ts_ms"].to_numpy() >= since_ms
    if until_ms is not None:
        keep &= events["ts_ms"].to_numpy() < until_ms
    events = events[keep].reset_index(drop=True)
    log.info(
        "%d arquivo(s), %d linhas, %d eventos no período", len(files), read.records, len(events)
    )
    return LoadedEvents(events, read, parse)


def fit_detector(
    events: pd.DataFrame, settings: SaclaSettings, until_ms: int | None = None
) -> Detector:
    """Ajusta históricos, Isolation Forest e limiar com todas as janelas completas do período."""
    if events.empty:
        raise ConfigError("nenhum evento no período de histórico")
    window_ms = settings.features.window_minutes * 60_000
    last = int(events["ts_ms"].max())
    # Sem --until, o histórico vai até o fim da última janela com eventos.
    history_end = until_ms if until_ms is not None else last - last % window_ms + window_ms
    features, baselines = build_features(events, settings, history_end)
    train = features[features["window_end_ms"] <= history_end].reset_index(drop=True)
    if train.empty:
        raise ConfigError("nenhuma janela completa no período de histórico")
    detector_settings = settings.detector
    params = ModelParams(
        n_estimators=detector_settings.n_estimators,
        max_samples=detector_settings.max_samples,
        max_features=detector_settings.max_features,
        log1p=detector_settings.log1p,
    )
    model = SaclaModel.fit(
        train,
        params,
        detector_settings.seed,
        detector_settings.log1p_features,
        detector_settings.n_jobs,
    )
    train_scores = np.sort(model.score(train))
    threshold = float(np.quantile(train_scores, 1 - detector_settings.train_alert_rate))
    model.with_threshold(threshold)
    log.info(
        "modelo %s ajustado com %d janelas de %d usuários; limiar %.6f",
        params.slug(),
        len(train),
        len(baselines),
        threshold,
    )
    return Detector(
        settings=settings,
        model=model,
        baselines=baselines,
        references=fit_reference(train, settings.report.explain_features),
        train_scores=train_scores,
        history_start_ms=int(train["window_start_ms"].min()),
        history_end_ms=int(train["window_end_ms"].max()),
        history_windows=len(train),
    )


def score_events(
    detector: Detector,
    events: pd.DataFrame,
    title: str,
    report_settings: ReportSettings | None = None,
) -> Report:
    """Pontua um novo período e monta o relatório (módulos 2b, 3 e 4).

    As janelas são montadas com a configuração salva no ajuste (janela, fuso, aliases). A
    apresentação do relatório pode vir da configuração atual (`report_settings`), menos a lista
    de atributos explicados, que precisa ser a da referência ajustada.
    """
    settings = detector.settings
    if report_settings is not None:
        settings = replace(
            settings,
            report=replace(report_settings, explain_features=settings.report.explain_features),
        )
    windows = features_with_baselines(events, settings, detector.baselines)
    if windows.empty:
        raise ConfigError("nenhuma janela com eventos no período analisado")
    if detector.model.threshold is None:
        raise ConfigError("modelo sem limiar definido")
    scores = detector.model.score(windows)
    tz = settings.features.timezone
    rate = settings.detector.train_alert_rate
    context = ReportContext(
        title=title,
        period=(
            f"{display_time(local_stamp(int(windows['window_start_ms'].min()), tz))} a "
            f"{display_time(local_stamp(int(windows['window_end_ms'].max()), tz))} ({tz})"
        ),
        window_minutes=settings.features.window_minutes,
        windows=len(windows),
        users=int(windows["user"].nunique()),
        threshold=detector.model.threshold,
        threshold_origin=(
            f"{100 * rate:.2f}% das janelas do histórico ficam acima dele".replace(".", ",")
        ),
        model=detector.model.params.slug(),
        history=detector.history_text(),
    )
    return build_report(
        windows,
        scores,
        detector.model.threshold,
        detector.train_scores,
        detector.references,
        settings,
        context,
    )

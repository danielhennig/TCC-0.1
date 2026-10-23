"""Monitor contínuo: pontua as janelas de cada fonte assim que elas fecham (Fase 6).

Uma fonte é um servidor PostgreSQL com jsonlog numa pasta. A cada tick, para cada fonte:
1. calcula o ponto de corte: o relógio da fonte (ao vivo: agora menos a tolerância de
   gravação; reprodução: início + tempo decorrido × velocidade), alinhado ao início de uma
   janela;
2. se o corte passou da última janela pontuada, lê os eventos de [última menos a margem, corte),
   pulando arquivos que terminam antes do período;
3. pontua só as janelas novas e completas com o detector da fonte e grava janelas e alertas.

Não é tempo real: é processamento em lotes de uma janela. Nada aqui usa rótulos; o detector é
o mesmo do `sacla fit` (pipeline.Detector).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from sacla.config import CONFIG_DIR, PROJECT_ROOT, ConfigError, SaclaSettings, load_yaml
from sacla.features import MS_PER_MIN, features_with_baselines
from sacla.pipeline import Detector, fit_detector, load_events, parse_instant
from sacla.reader import list_log_files, read_records
from sacla.report import SUMMARY_COLUMNS, describe_windows
from sacla.store import WAITING_FIT, Store

log = logging.getLogger(__name__)

LIVE = "live"
REPLAY = "replay"
ACTIVE = "ativo"
ERROR = "erro"


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    logs: Path
    clock_mode: str
    replay_start_ms: int | None = None
    replay_speed: float = 1.0
    fit_since_ms: int | None = None
    fit_until_ms: int | None = None


@dataclass(frozen=True)
class MonitorSettings:
    state_dir: Path
    tick_seconds: float
    grace_seconds: float
    lookback_windows: int
    max_backfill_hours: float
    scan_lock_seconds: float
    refresh_seconds: float
    max_alerts: int
    timeline_hours_around: float
    hourly_bins_max_days: float
    sources: tuple[SourceConfig, ...]

    @property
    def store_path(self) -> Path:
        return self.state_dir / "sacla.sqlite"

    def detector_path(self, source_id: str) -> Path:
        return self.state_dir / "detectores" / f"{source_id}.joblib"

    def source(self, source_id: str) -> SourceConfig:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise ConfigError(f"fonte desconhecida: {source_id}")


@dataclass
class ScanResult:
    source_id: str
    since_ms: int
    until_ms: int
    windows: int
    alerts: int
    new_alerts: int


def load_monitor_settings(path: Path | None = None) -> MonitorSettings:
    """Lê config/monitor.yaml (ou SACLA_MONITOR_CONFIG); SACLA_STATE_DIR troca o state_dir."""
    path = path or Path(os.environ.get("SACLA_MONITOR_CONFIG", CONFIG_DIR / "monitor.yaml"))
    raw = load_yaml(path)
    try:
        sources = tuple(_source(item) for item in raw["sources"])
        dashboard = raw["dashboard"]
        settings = MonitorSettings(
            state_dir=_project_path(os.environ.get("SACLA_STATE_DIR", raw["state_dir"])),
            tick_seconds=float(raw["tick_seconds"]),
            grace_seconds=float(raw["grace_seconds"]),
            lookback_windows=int(raw["lookback_windows"]),
            max_backfill_hours=float(raw["max_backfill_hours"]),
            scan_lock_seconds=float(raw["scan_lock_seconds"]),
            refresh_seconds=float(dashboard["refresh_seconds"]),
            max_alerts=int(dashboard["max_alerts"]),
            timeline_hours_around=float(dashboard["timeline_hours_around"]),
            hourly_bins_max_days=float(dashboard["hourly_bins_max_days"]),
            sources=sources,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"monitor.yaml malformado: {exc!r}") from exc
    ids = [s.id for s in settings.sources]
    if len(ids) != len(set(ids)):
        raise ConfigError("monitor.yaml: ids de fonte repetidos")
    if settings.tick_seconds <= 0 or settings.lookback_windows < 0:
        raise ConfigError("monitor.yaml: tick_seconds > 0 e lookback_windows >= 0")
    return settings


def _source(item: dict[str, Any]) -> SourceConfig:
    clock = item.get("clock") or {"mode": LIVE}
    mode = str(clock["mode"])
    if mode not in (LIVE, REPLAY):
        raise ConfigError(f"fonte {item['id']}: clock.mode deve ser {LIVE} ou {REPLAY}")
    fit = item.get("fit") or {}
    start = parse_instant(clock.get("start")) if mode == REPLAY else None
    if mode == REPLAY and start is None:
        raise ConfigError(f"fonte {item['id']}: reprodução exige clock.start")
    return SourceConfig(
        id=str(item["id"]),
        name=str(item.get("name", item["id"])),
        logs=_project_path(str(item["logs"])),
        clock_mode=mode,
        replay_start_ms=start,
        replay_speed=float(clock.get("speed", 1.0)),
        fit_since_ms=parse_instant(fit.get("since")),
        fit_until_ms=parse_instant(fit.get("until")),
    )


def _project_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def clock_ms(source: SourceConfig, replay_started_at: float | None, now: float) -> int:
    """Relógio da fonte em ms UTC: o de agora (ao vivo) ou o da reprodução."""
    if source.clock_mode == LIVE:
        return int(now * 1000)
    assert source.replay_start_ms is not None
    started = now if replay_started_at is None else replay_started_at
    return source.replay_start_ms + int((now - started) * 1000 * source.replay_speed)


def cutoff_ms(clock: int, window_ms: int, grace_ms: int) -> int:
    """Fim da última janela completa (múltiplo do tamanho da janela)."""
    edge = clock - grace_ms
    return edge - edge % window_ms


def select_files(
    files: list[Path], starts: dict[str, int | None], since_ms: int, until_ms: int
) -> list[Path]:
    """Arquivos que podem ter registros em [since_ms, until_ms).

    Os arquivos, em ordem de nome, são cronológicos (rotação do PostgreSQL; um por dia no log
    remapeado). Um arquivo termina antes do início do seguinte: se o seguinte começa até
    `since_ms`, o arquivo inteiro é anterior ao período. Um arquivo que começa em `until_ms` ou
    depois é posterior (na reprodução, o log futuro já existe).
    """
    keep = []
    for i, path in enumerate(files):
        start = starts.get(str(path))
        following = starts.get(str(files[i + 1])) if i + 1 < len(files) else None
        if following is not None and following <= since_ms:
            continue
        if start is not None and start >= until_ms:
            continue
        keep.append(path)
    return keep


def first_timestamp(path: Path, zone_offsets_min: dict[str, int]) -> int | None:
    """Horário do primeiro registro válido do arquivo (lê só o começo)."""
    with closing(read_records([path], zone_offsets_min=zone_offsets_min)) as records:
        for record in records:
            return int(record.ts_ms)
    return None


class Monitor:
    """Análise das fontes; usado pelo laço (`sacla monitor`) e pelo botão do painel."""

    def __init__(
        self,
        settings: MonitorSettings,
        store: Store | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.store = store or Store(settings.store_path)
        self.now = now
        self._detectors: dict[str, tuple[float, Detector]] = {}

    def tick(self) -> list[ScanResult]:
        results = []
        for source in self.settings.sources:
            try:
                result = self.scan(source)
            except Exception as exc:  # uma fonte com problema não derruba as outras
                log.exception("fonte %s: falha na análise", source.id)
                self.store.update_state(source.id, status=ERROR, last_error=repr(exc))
                continue
            if result is not None:
                results.append(result)
        return results

    def detector(self, source: SourceConfig) -> Detector | None:
        """Detector da fonte, recarregado quando o arquivo muda."""
        path = self.settings.detector_path(source.id)
        if not path.exists():
            self._detectors.pop(source.id, None)
            return None
        mtime = path.stat().st_mtime
        cached = self._detectors.get(source.id)
        if cached is None or cached[0] != mtime:
            self._detectors[source.id] = (mtime, Detector.load(path))
        return self._detectors[source.id][1]

    def scan(self, source: SourceConfig) -> ScanResult | None:
        detector = self.detector(source)
        if detector is None:
            self.store.update_state(source.id, status=WAITING_FIT)
            return None
        now = self.now()
        if not self.store.try_lock(source.id, self.settings.scan_lock_seconds, now):
            return None  # outra análise (ou uma ação do painel) em curso
        try:
            # Estado lido SÓ depois de travar: uma limpeza ou um reprocessamento feitos pelo
            # painel um instante antes valem para esta análise.
            state = self.store.state(source.id)
            if source.clock_mode == REPLAY and state.replay_started_at is None:
                self.store.update_state(source.id, replay_started_at=now)
                state = self.store.state(source.id)
            window_ms = detector.settings.features.window_minutes * MS_PER_MIN
            grace_ms = int(self.settings.grace_seconds * 1000) if source.clock_mode == LIVE else 0
            cut = cutoff_ms(clock_ms(source, state.replay_started_at, now), window_ms, grace_ms)
            last = state.last_window_end_ms
            if last is None:
                last = self._first_start(source, detector, cut, window_ms)
            if cut <= last:
                self.store.update_state(source.id, status=ACTIVE, last_scan_at=now)
                return None
            return self._scan_period(
                source, detector, last, cut, window_ms, state.last_window_end_ms
            )
        finally:
            self.store.unlock(source.id)

    def _first_start(
        self, source: SourceConfig, detector: Detector, cut: int, window_ms: int
    ) -> int:
        if source.clock_mode == REPLAY:
            assert source.replay_start_ms is not None
            start = source.replay_start_ms
        else:
            backfill = int(self.settings.max_backfill_hours * 3_600_000)
            start = max(detector.history_end_ms, cut - backfill)
        return start - start % window_ms

    def _scan_period(
        self,
        source: SourceConfig,
        detector: Detector,
        last: int,
        cut: int,
        window_ms: int,
        pointer: int | None,
    ) -> ScanResult:
        """Pontua as janelas completas de [last, cut); `pointer` é o ponteiro lido sob a trava."""
        settings = detector.settings
        since = last - self.settings.lookback_windows * window_ms
        scan_id = self.store.start_scan(source.id, last, cut)
        count = alerts = new_alerts = 0
        flagged_keys: set[tuple[str, int]] = set()
        try:
            files = self._files(source, settings, since, cut)
            events = load_events(source.logs, settings, since, cut, files=files).events
            windows = features_with_baselines(events, settings, detector.baselines)
            if not windows.empty:
                complete = (windows["window_start_ms"] >= last) & (windows["window_end_ms"] <= cut)
                windows = windows[complete].reset_index(drop=True)
            if not windows.empty:
                assert detector.model.threshold is not None
                scores = detector.model.score(windows)
                flagged = np.asarray(scores >= detector.model.threshold, dtype=bool)
                described = describe_windows(
                    windows.assign(score=scores),
                    detector.train_scores,
                    detector.references,
                    settings,
                )
                count = self.store.save_windows(source.id, described, flagged.tolist())
                flagged_rows = described[flagged]
                alerts = len(flagged_rows)
                new_alerts = self.store.save_alerts(source.id, flagged_rows, list(SUMMARY_COLUMNS))
                flagged_keys = {
                    (str(u), int(w))
                    for u, w in zip(
                        flagged_rows["usuario"], flagged_rows["window_start_ms"], strict=True
                    )
                }
            # Reprocessamento com outro detector: some o alerta não triado que deixou de existir.
            self.store.prune_alerts(source.id, last, cut, flagged_keys)
        except Exception as exc:
            self.store.finish_scan(scan_id, 0, 0, repr(exc))
            raise
        self.store.finish_scan(scan_id, count, alerts)
        if not self.store.advance(source.id, pointer, cut, self.now()):
            log.warning("fonte %s: o ponteiro mudou durante a análise", source.id)
        if new_alerts:
            log.info("fonte %s: %d alerta(s) novo(s) até %d", source.id, new_alerts, cut)
        return ScanResult(source.id, last, cut, count, alerts, new_alerts)

    def _files(
        self, source: SourceConfig, settings: SaclaSettings, since: int, until: int
    ) -> list[Path]:
        files = list_log_files(source.logs, settings.reader.patterns)
        starts = self.store.file_starts(source.id)
        for path in files:
            if str(path) not in starts or starts[str(path)] is None:
                first = first_timestamp(path, settings.reader.zone_offsets_min)
                self.store.save_file_start(source.id, str(path), first)
                starts[str(path)] = first
        return select_files(files, starts, since, until)


@dataclass
class FitResult:
    detector: Detector
    info: dict[str, Any] = field(default_factory=dict)


def fit_source(
    monitor_settings: MonitorSettings,
    source: SourceConfig,
    settings: SaclaSettings,
    since_ms: int | None,
    until_ms: int | None,
    store: Store | None = None,
) -> FitResult:
    """Ajusta e salva o detector de uma fonte (botão do painel ou linha de comando)."""
    loaded = load_events(source.logs, settings, since_ms, until_ms)
    detector = fit_detector(loaded.events, settings, until_ms)
    detector.save(monitor_settings.detector_path(source.id))
    info = {
        "historico": detector.history_text(),
        "janelas": detector.history_windows,
        "usuarios": len(detector.baselines),
        "limiar": detector.model.threshold,
        "modelo": detector.model.params.slug(),
        "taxa_alertas_historico": settings.detector.train_alert_rate,
        "versao": detector.version,
        "ajustado_em": time.time(),
    }
    (store or Store(monitor_settings.store_path)).update_state(
        source.id, detector_info=info, status=ACTIVE, last_error=None
    )
    return FitResult(detector, info)


def run_forever(monitor: Monitor, should_stop: Callable[[], bool]) -> None:
    """Laço do serviço `monitor`: um tick a cada `tick_seconds`, até `should_stop()`."""
    log.info(
        "monitor iniciado: %d fonte(s), estado em %s",
        len(monitor.settings.sources),
        monitor.settings.store_path,
    )
    while not should_stop():
        started = time.monotonic()
        monitor.tick()
        remaining = monitor.settings.tick_seconds - (time.monotonic() - started)
        deadline = time.monotonic() + max(remaining, 0.0)
        while not should_stop() and time.monotonic() < deadline:
            time.sleep(min(0.5, max(deadline - time.monotonic(), 0.0)))

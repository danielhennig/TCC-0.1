"""Monitor contínuo: relógio, completude das janelas, idempotência e coerência com o lote."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from sacla.config import load_settings
from sacla.monitor import (
    ERROR,
    LIVE,
    REPLAY,
    Monitor,
    MonitorSettings,
    SourceConfig,
    clock_ms,
    cutoff_ms,
    fit_source,
    select_files,
)
from sacla.pipeline import load_events, parse_instant, score_events
from sacla.store import WAITING_FIT

BASE = load_settings()
SETTINGS = replace(BASE, detector=replace(BASE.detector, n_estimators=50, max_samples=256))
MIN = 60_000
DAY = 24 * 60 * MIN
DAY0 = parse_instant("2026-03-02T00:00:00Z")
assert DAY0 is not None
TEST_DAY = DAY0 + 4 * DAY  # 06/03: dia analisado pelo monitor


def stamp(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


def line(ts: int, user: str, message: str, sid: str, db: str = "pagila", **extra: Any) -> str:
    record = {
        "timestamp": stamp(ts),
        "user": user,
        "dbname": db,
        "pid": 100,
        "remote_host": "10.0.0.1",
        "session_id": sid,
        "line_num": 1,
        "error_severity": "LOG",
        "message": message,
        "backend_type": "client backend",
        "query_id": 1,
        **extra,
    }
    return json.dumps(record)


def write_day(folder: Path, day: int, burst: bool = False) -> None:
    """Um arquivo por dia: u1 e u2 com 2–4 SELECT por janela das 12h às 20h UTC."""
    rng = np.random.default_rng(day)
    start = DAY0 + day * DAY
    lines = []
    for user in ("u1", "u2"):
        sid = f"{user}-{day}"
        lines.append(line(start + 12 * 60 * MIN, user, "connection authorized: user=x", sid))
        for window in range(8 * 12):
            begin = start + 12 * 60 * MIN + window * 5 * MIN
            for k in range(int(rng.integers(2, 5))):
                lines.append(
                    line(begin + 1000 + k, user, "duration: 0.5 ms  statement: SELECT 1", sid)
                )
    if burst:  # 03h UTC: rajada de u1 e acesso de u2 a outro banco
        night = start + 3 * 60 * MIN
        for k in range(300):
            lines.append(line(night + k * 100, "u1", "duration: 0.5 ms  statement: SELECT 2", "n"))
        lines.append(
            line(
                start + 15 * 60 * MIN + 30_000,
                "u2",
                "duration: 0.5 ms  statement: SELECT 3",
                "rh",
                db="rh",
            )
        )
    lines.sort(key=lambda text: json.loads(text)["timestamp"])
    name = f"postgresql-{stamp(start)[:10].replace('-', '')}_000000.json"
    (folder / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def lab(tmp_path: Path) -> tuple[MonitorSettings, SourceConfig]:
    logs = tmp_path / "logs"
    logs.mkdir()
    for day in range(4):
        write_day(logs, day)
    write_day(logs, 4, burst=True)
    source = SourceConfig(
        id="sim",
        name="sim",
        logs=logs,
        clock_mode=REPLAY,
        replay_start_ms=TEST_DAY,
        replay_speed=60.0,  # 1 h simulada por minuto real
        fit_until_ms=TEST_DAY,
    )
    settings = MonitorSettings(
        state_dir=tmp_path / "state",
        tick_seconds=1,
        grace_seconds=60,
        lookback_windows=1,
        max_backfill_hours=24,
        scan_lock_seconds=60,
        refresh_seconds=20,
        max_alerts=100,
        timeline_hours_around=3,
        hourly_bins_max_days=3,
        sources=(source,),
    )
    return settings, source


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def test_clock_and_cutoff() -> None:
    replay = SourceConfig("r", "r", Path("."), REPLAY, replay_start_ms=0, replay_speed=60.0)
    assert clock_ms(replay, replay_started_at=100.0, now=110.0) == 600_000  # 10 s × 60
    live = SourceConfig("l", "l", Path("."), LIVE)
    assert clock_ms(live, None, now=1.5) == 1500
    assert cutoff_ms(14 * MIN + 59_999, 5 * MIN, grace_ms=0) == 10 * MIN
    assert cutoff_ms(16 * MIN, 5 * MIN, grace_ms=2 * MIN) == 10 * MIN


def test_select_files_keeps_only_files_that_overlap_the_period() -> None:
    files = [Path(name) for name in ("a", "b", "c")]
    starts = {"a": 0, "b": 100, "c": 200}
    assert select_files(files, starts, since_ms=150, until_ms=300) == [Path("b"), Path("c")]
    assert select_files(files, starts, since_ms=100, until_ms=300) == [Path("b"), Path("c")]
    assert select_files(files, starts, since_ms=99, until_ms=300) == files
    # Arquivos que começam no fim do período ou depois (log futuro da reprodução) ficam de fora.
    assert select_files(files, starts, since_ms=0, until_ms=200) == [Path("a"), Path("b")]


def test_source_without_detector_waits_for_a_fit(lab: tuple[MonitorSettings, SourceConfig]) -> None:
    settings, source = lab
    monitor = Monitor(settings)
    assert monitor.tick() == []
    assert monitor.store.state(source.id).status == WAITING_FIT


def test_replay_scores_complete_windows_like_the_batch_and_is_idempotent(
    lab: tuple[MonitorSettings, SourceConfig],
) -> None:
    settings, source = lab
    clock = FakeClock()
    monitor = Monitor(settings, now=clock)
    fit = fit_source(settings, source, SETTINGS, None, source.fit_until_ms, monitor.store)
    monitor.tick()  # inicia o relógio da reprodução
    clock.now += 24 * 60 + 2.5  # 24 h simuladas e 2,5 min de uma janela ainda aberta
    results = monitor.tick()
    (result,) = results
    cut = TEST_DAY + DAY + 2 * 60_000 - (2 * 60_000) % (5 * MIN)
    assert result.until_ms == cut
    windows = monitor.store.windows(source.id)
    assert (windows["window_end_ms"] <= cut).all() and (
        windows["window_start_ms"] >= TEST_DAY
    ).all()

    # Mesmo período, em lote: mesmas janelas, mesmos scores e mesmos alertas.
    events = load_events(source.logs, SETTINGS, TEST_DAY, cut).events
    batch = score_events(fit.detector, events, "lote")
    assert len(windows) == batch.context.windows
    alerts = monitor.store.alerts(source.id)
    assert sorted(alerts["window_start_ms"]) == sorted(batch.alerts["window_start_ms"])
    # O monitor vê a janela da rajada (03h UTC). Se ela vira alerta, é decisão do detector,
    # igual no lote: nestes dados sintéticos, o score satura e ela fica abaixo do limiar (D41).
    burst = windows[(windows["user"] == "u1") & windows["inicio"].str.endswith("T00:00-0300")]
    assert list(burst["n_commands"]) == [300]
    other_db = windows[windows["dbs"].str.contains("rh")]
    assert list(other_db["user"]) == ["u2"]

    # Nada novo para analisar: não refaz nem duplica.
    assert monitor.tick() == []
    monitor.store.rewind(source.id, TEST_DAY)
    monitor.tick()
    assert len(monitor.store.windows(source.id)) == len(windows)
    assert len(monitor.store.alerts(source.id)) == len(alerts)


def test_new_database_for_the_user_is_flagged_as_context(
    lab: tuple[MonitorSettings, SourceConfig],
) -> None:
    settings, source = lab
    clock = FakeClock()
    monitor = Monitor(settings, now=clock)
    fit_source(settings, source, SETTINGS, None, source.fit_until_ms, monitor.store)
    monitor.tick()
    clock.now += 24 * 60
    monitor.tick()
    # A janela de u2 com o banco `rh` (que u2 nunca usou) ganha o contexto, se for alerta.
    alerts = monitor.store.alerts(source.id)
    rh = alerts[alerts["dbs"].str.contains("rh")]
    assert all(row == "rh" for row in rh["dbs_new"])


def test_detector_is_reloaded_when_its_file_changes(
    lab: tuple[MonitorSettings, SourceConfig],
) -> None:
    settings, source = lab
    monitor = Monitor(settings, now=FakeClock())
    fit_source(settings, source, SETTINGS, None, source.fit_until_ms, monitor.store)
    first = monitor.detector(source)
    stricter = replace(SETTINGS, detector=replace(SETTINGS.detector, train_alert_rate=0.001))
    fit_source(settings, source, stricter, None, source.fit_until_ms, monitor.store)
    path = settings.detector_path(source.id)
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    second = monitor.detector(source)
    assert first is not None and second is not None
    assert second.model.threshold != first.model.threshold


def test_a_broken_source_does_not_stop_the_others(
    lab: tuple[MonitorSettings, SourceConfig],
) -> None:
    settings, source = lab
    broken = replace(source, id="quebrada", logs=source.logs.parent / "nao-existe")
    settings = replace(settings, sources=(broken, source))
    clock = FakeClock()
    monitor = Monitor(settings, now=clock)
    fit_source(settings, source, SETTINGS, None, source.fit_until_ms, monitor.store)
    settings.detector_path(broken.id).write_bytes(settings.detector_path(source.id).read_bytes())
    monitor.tick()
    clock.now += 60
    results = monitor.tick()
    assert [r.source_id for r in results] == [source.id]
    state = monitor.store.state(broken.id)
    assert state.status == ERROR and "nao-existe" in (state.last_error or "")

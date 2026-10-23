"""Painel: cada página abre sem erro sobre um estado real do monitor (AppTest do Streamlit)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
import yaml
from streamlit.testing.v1 import AppTest

from dashboard import state
from sacla.monitor import Monitor, fit_source, load_monitor_settings
from sacla.report import local_stamp
from test_monitor import SETTINGS, TEST_DAY, FakeClock, write_day
from test_store import MIN, described

PAGES = ("overview", "databases", "alerts", "users", "data")


@pytest.fixture(scope="module")
def dashboard_state(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    root = tmp_path_factory.mktemp("painel")
    logs = root / "logs"
    logs.mkdir()
    for day in range(4):
        write_day(logs, day)
    write_day(logs, 4, burst=True)
    config = {
        "state_dir": str(root / "state"),
        "tick_seconds": 1,
        "grace_seconds": 60,
        "lookback_windows": 1,
        "max_backfill_hours": 24,
        "scan_lock_seconds": 60,
        "dashboard": {
            "refresh_seconds": 30,
            "max_alerts": 500,
            "timeline_hours_around": 3,
            "hourly_bins_max_days": 3,
        },
        "sources": [
            {
                "id": "sim",
                "name": "Simulação de teste",
                "logs": str(logs),
                "clock": {
                    "mode": "replay",
                    "start": local_stamp(TEST_DAY, "UTC").replace("+0000", "Z"),
                    "speed": 60,
                },
                "fit": {"until": local_stamp(TEST_DAY, "UTC").replace("+0000", "Z")},
            },
            {"id": "vazia", "name": "Servidor sem detector", "logs": str(root / "nada")},
        ],
    }
    path = root / "monitor.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    previous = os.environ.get("SACLA_MONITOR_CONFIG")
    os.environ["SACLA_MONITOR_CONFIG"] = str(path)
    settings = load_monitor_settings()
    clock = FakeClock()
    monitor = Monitor(settings, now=clock)
    source = settings.source("sim")
    fit_source(settings, source, SETTINGS, None, source.fit_until_ms, monitor.store)
    monitor.tick()
    clock.now += 24 * 60
    monitor.tick()
    st.cache_resource.clear()
    yield path
    st.cache_resource.clear()
    if previous is None:
        os.environ.pop("SACLA_MONITOR_CONFIG", None)
    else:
        os.environ["SACLA_MONITOR_CONFIG"] = previous


@pytest.mark.parametrize("page", PAGES)
def test_each_page_renders_without_errors(dashboard_state: Path, page: str) -> None:
    app = AppTest.from_string(f"from dashboard.pages import {page}\n{page}.render()")
    app.run(timeout=60)
    assert not app.exception, app.exception


def test_main_app_shows_the_overview_and_the_new_alerts_counter(dashboard_state: Path) -> None:
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "src/dashboard/app.py"))
    app.run(timeout=60)
    assert not app.exception, app.exception
    # A visão geral resume a situação numa frase; o console lateral mostra quantos alertas
    # aguardam triagem.
    html = " ".join(element.proto.body for element in app.get("html"))
    assert "Supervisionando <strong>2 bancos</strong> em 2 servidores" in html  # pagila e rh
    counter = html.split('class="valor')[1].split(">")[1].split("<")[0]
    assert int(counter) > 0 and "aguardam triagem" in html


def test_alert_detail_and_triage_render_for_a_selected_episode(dashboard_state: Path) -> None:
    app = AppTest.from_string("from dashboard.pages import alerts\nalerts.render()")
    app.run(timeout=60)
    shown = app.session_state["episodios-exibidos"]
    # A seleção guarda a CHAVE do episódio (fonte|usuário|início), não a posição na tabela.
    app.session_state["episodio-selecionado"] = shown[-1]
    app.run(timeout=60)
    assert not app.exception, app.exception
    assert any("Triagem" in m.value for m in app.markdown)
    assert any(shown[-1].split("|")[1] in h.value for h in app.subheader)


def test_periods_are_relative_to_the_latest_window_of_each_source(
    dashboard_state: Path,
) -> None:
    from dashboard import common

    store = common.store()
    october = 2_000_000_000_000 // MIN  # uma fonte "ao vivo", com dados em outubro
    store.save_windows("vazia", described([october]), [False])
    periods = state.source_periods(store, ["sim", "vazia"], "Último dia")
    # Cada fonte conta da própria janela mais recente com atividade: a simulação (março) não
    # some por causa da fonte ao vivo, nem fica vazia se o relógio passar do fim do log.
    assert periods["sim"].until_ms == store.latest_window_end("sim")
    assert periods["vazia"].until_ms == (october + 5) * MIN
    store.reset_source("vazia")


def test_alerts_without_database_are_kept_under_their_own_label() -> None:
    assert state.databases_of("") == [state.NO_DATABASE]
    assert state.databases_of("pagila|rh") == ["pagila", "rh"]


def test_episodes_group_consecutive_alerts_and_keep_the_most_urgent_status() -> None:
    alerts = pd.DataFrame(
        {
            "id": [1, 2, 3],
            "source_id": ["s", "s", "s"],
            "user": ["u1", "u1", "u1"],
            "window_start_ms": [0, 300_000, 3_600_000],
            "window_end_ms": [300_000, 600_000, 3_900_000],
            "inicio": ["2026-03-23T00:00-0300", "2026-03-23T00:05-0300", "2026-03-23T01:00-0300"],
            "dbs": ["pagila", "pagila|rh", "pagila"],
            "score": [0.7, 0.8, 0.6],
            "status": ["falso positivo", "novo", "em análise"],
            "explanation": ["a | b", "c", "d"],
        }
    )
    prepared = state.prepare_alerts(alerts, SETTINGS)
    episodes = state.episodes_table(prepared).sort_values("window_start_ms")
    assert list(episodes["janelas"]) == [2, 1]
    first = episodes.iloc[0]
    assert (first["status"], first["bancos"], first["principal desvio"]) == (
        "novo",
        "pagila|rh",
        "c",
    )
    assert first["chave"] == "s|u1|0"  # estável: fonte, usuário e início da 1ª janela

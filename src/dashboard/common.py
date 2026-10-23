"""Peças comuns às páginas do painel: configuração, estado, período, status e aviso de alertas."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import state
from sacla.config import ConfigError, SaclaSettings, load_settings
from sacla.monitor import Monitor, MonitorSettings, load_monitor_settings
from sacla.pipeline import Detector
from sacla.store import Store

DISCLAIMER = (
    "Um alerta indica comportamento atípico para o usuário em relação ao próprio histórico, "
    "não um ataque. A decisão é de quem analisa."
)


@st.cache_resource
def monitor_settings() -> MonitorSettings:
    return load_monitor_settings()


@st.cache_resource
def sacla_settings() -> SaclaSettings:
    return load_settings()


@st.cache_resource
def store() -> Store:
    return Store(monitor_settings().store_path)


@st.cache_resource
def monitor() -> Monitor:
    """O mesmo `Monitor` do serviço, para o botão "Analisar agora" (a fonte fica travada)."""
    return Monitor(monitor_settings(), store())


@st.cache_resource(max_entries=8)
def _load_detector(path: str, mtime: float) -> Detector | None:
    try:
        return Detector.load(Path(path))
    except ConfigError:
        return None


def detector(source_id: str) -> Detector | None:
    """Detector da fonte, lido uma vez por versão do arquivo (chave: caminho e data)."""
    path = monitor_settings().detector_path(source_id)
    if not path.exists():
        return None
    return _load_detector(str(path), path.stat().st_mtime)


def timezone() -> str:
    return sacla_settings().features.timezone


def theme_mode() -> str | None:
    try:
        return st.context.theme.type
    except AttributeError:
        return None


def source_names() -> dict[str, str]:
    return {s.id: s.name for s in monitor_settings().sources}


def period_preset(key: str) -> str:
    """Filtro de período; o intervalo de cada fonte é calculado na hora de consultar."""
    preset = st.segmented_control(
        "Período",
        list(state.PERIODS),
        default="Últimos 7 dias",
        key=f"{key}-periodo",
        help="Contado a partir da janela mais recente de cada fonte (na reprodução, o relógio "
        "é o da simulação).",
    )
    return preset or "Tudo"


def status_badge(status: str) -> None:
    st.badge(
        status,
        icon=state.STATUS_ICONS.get(status),
        color=state.STATUS_COLORS.get(status, "gray"),  # type: ignore[arg-type]
    )


def source_status_badge(status: str) -> None:
    st.badge(status, color=state.SOURCE_STATUS_COLORS.get(status, "gray"))  # type: ignore[arg-type]


def data_view(frame: pd.DataFrame, label: str = "Ver os dados do gráfico") -> None:
    """Tabela gêmea de cada gráfico: os valores sem depender de cor nem de passar o mouse."""
    with st.expander(label):
        st.dataframe(frame, hide_index=True)


def new_alerts_notice() -> None:
    """Contador de alertas novos na barra lateral, com aviso quando o número sobe."""

    @st.fragment(run_every=monitor_settings().refresh_seconds)
    def notice() -> None:
        new = state.count_new(store().alerts(limit=monitor_settings().max_alerts))
        previous = st.session_state.get("alertas_novos_vistos")
        if previous is not None and new > previous:
            st.toast(
                f"{new - previous} alerta(s) novo(s) para triagem",
                icon=":material/notification_important:",
            )
        st.session_state["alertas_novos_vistos"] = new
        st.metric("Alertas novos", new, border=True)
        st.caption(f"Atualiza a cada {int(monitor_settings().refresh_seconds)} s")

    with st.sidebar:
        notice()

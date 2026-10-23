"""Peças comuns às páginas do painel: configuração, estado, período, status e o console lateral."""

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

# O tema (cores, fontes, raio) está em .streamlit/config.toml. Aqui fica só o que o tema não
# cobre: a marca e o contador do console lateral e a frase de situação da visão geral.
_STYLE = """
<style>
.sacla-marca { margin: 0.25rem 0 1.5rem 0; }
.sacla-marca .nome {
  font-size: 1.75rem; font-weight: 750; letter-spacing: -0.01em; line-height: 1;
}
.sacla-marca .linha { font-size: 0.875rem; opacity: 0.72; margin-top: 0.35rem; }
.sacla-plantao { padding: 0.25rem 0 0.75rem 0; }
.sacla-plantao .valor {
  display: block; font-size: 3.5rem; font-weight: 750; line-height: 1;
  font-variant-numeric: lining-nums; color: #F0A93B;
}
.sacla-plantao .valor.zero { color: inherit; opacity: 0.55; }
.sacla-plantao .rotulo { display: block; font-size: 0.95rem; margin-top: 0.4rem; }
.sacla-plantao .atualiza { display: block; font-size: 0.8rem; opacity: 0.6; margin-top: 0.6rem; }
.sacla-resumo {
  font-size: 1.375rem; line-height: 1.45; max-width: 46rem; margin: 0.25rem 0 0.5rem 0;
}
.sacla-resumo strong { font-weight: 700; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
</style>
"""


def style() -> None:
    st.html(_STYLE)


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


def console() -> None:
    """Barra lateral: a marca e o contador de alertas que aguardam triagem.

    O contador é o único elemento em destaque do painel. Ele se atualiza sozinho e avisa na
    tela quando o número sobe.
    """
    settings = monitor_settings()

    @st.fragment(run_every=settings.refresh_seconds)
    def counter() -> None:
        new = state.count_new(store().alerts(limit=settings.max_alerts))
        previous = st.session_state.get("alertas_novos_vistos")
        if previous is not None and new > previous:
            st.toast(
                f"{new - previous} alerta(s) novo(s) para triagem",
                icon=":material/notification_important:",
            )
        st.session_state["alertas_novos_vistos"] = new
        label = "alerta aguarda triagem" if new == 1 else "alertas aguardam triagem"
        zero = " zero" if new == 0 else ""
        st.html(
            f'<div class="sacla-plantao"><span class="valor{zero}">{int(new)}</span>'
            f'<span class="rotulo">{label}</span>'
            f'<span class="atualiza">Atualiza a cada {int(settings.refresh_seconds)} s</span>'
            "</div>"
        )

    with st.sidebar:
        st.html(
            '<div class="sacla-marca"><div class="nome">SACLA</div>'
            '<div class="linha">Acessos atípicos ao PostgreSQL</div></div>'
        )
        counter()

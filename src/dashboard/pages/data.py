"""Dados: configuração das fontes, detectores, últimas análises, exportação e limpeza."""

from __future__ import annotations

import os

import streamlit as st

from dashboard import common, state
from sacla.config import CONFIG_DIR, ConfigError


def render() -> None:
    st.title("Dados")
    settings = common.monitor_settings()
    store = common.store()
    tz = common.timezone()

    st.subheader("Fontes configuradas")
    config = os.environ.get("SACLA_MONITOR_CONFIG", str(CONFIG_DIR / "monitor.yaml"))
    st.caption(
        f"Definidas em `{config}` (para adicionar um servidor, edite o arquivo e reinicie o "
        "painel). Estado em " + f"`{settings.store_path}`."
    )
    sources = state.sources_table(settings, store, tz)
    st.dataframe(
        sources[
            [
                "id",
                "fonte",
                "logs",
                "relógio",
                "status",
                "detector",
                "histórico do detector",
                "limiar",
            ]
        ],
        hide_index=True,
    )

    st.subheader("Últimas análises")
    scans = store.scans(limit=100)
    if scans.empty:
        st.caption("Nenhuma análise ainda.")
    else:
        scans = scans.assign(
            início=state.local_datetimes((scans["started_at"] * 1000).astype("int64"), tz),
            duração_s=(scans["finished_at"] - scans["started_at"]).round(2),
        )
        st.dataframe(
            scans.assign(error=scans["error"].fillna(""))[
                ["source_id", "início", "duração_s", "windows", "alerts", "error"]
            ],
            hide_index=True,
            column_config={
                "source_id": "fonte",
                "windows": "janelas",
                "alerts": "alertas",
                "error": "erro",
            },
        )

    st.subheader("Exportar")
    names = common.source_names()
    source_id = st.selectbox(
        "Fonte", list(names), format_func=lambda key: names[key], key="exportar-fonte"
    )
    if source_id is not None:
        cols = st.columns(2)
        cols[0].download_button(
            "Janelas analisadas (CSV)",
            store.windows(source_id).to_csv(index=False).encode("utf-8"),
            file_name=f"janelas_{source_id}.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        cols[1].download_button(
            "Alertas e triagem (CSV)",
            store.alerts(source_id).to_csv(index=False).encode("utf-8"),
            file_name=f"alertas_{source_id}.csv",
            mime="text/csv",
            icon=":material/download:",
        )

        st.subheader("Limpar o estado de uma fonte")
        st.caption(
            "Apaga janelas, alertas (com a triagem) e análises da fonte. O detector é mantido; "
            "o monitor reanalisa do começo."
        )
        confirm = st.checkbox(f"Confirmo apagar o estado de `{source_id}`", key="limpar-confirma")
        if st.button(
            "Limpar estado", disabled=not confirm, icon=":material/delete:", type="primary"
        ):
            try:
                with store.locked(source_id, settings.scan_lock_seconds):
                    store.reset_source(source_id)
            except ConfigError as exc:
                st.error(str(exc))
            else:
                st.success("Estado apagado.")

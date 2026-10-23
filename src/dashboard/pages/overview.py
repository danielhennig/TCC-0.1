"""Visão geral: o que está sendo supervisionado e como está a situação agora."""

from __future__ import annotations

import streamlit as st

from dashboard import charts, common, state
from sacla.explain import format_number


def render() -> None:
    st.title("Visão geral")
    st.caption(common.DISCLAIMER)
    settings = common.monitor_settings()
    names = common.source_names()
    preset = common.period_preset("visao")
    tz = common.timezone()

    @st.fragment(run_every=settings.refresh_seconds)
    def body() -> None:
        # Recalculado a cada atualização: o fim do período acompanha o monitor.
        store = common.store()
        periods = state.source_periods(store, list(names), preset)
        sources = state.sources_table(settings, store, tz)
        windows = state.load_windows(store, periods)
        alerts = state.prepare_alerts(
            state.load_alerts(store, periods, settings.max_alerts), common.sacla_settings()
        )
        banks = {b for v in windows.get("dbs", []) for b in state.databases_of(v)}

        cols = st.columns(5)
        cols[0].metric(
            "Fontes supervisionadas",
            len(sources),
            help="Servidores PostgreSQL configurados em config/monitor.yaml",
        )
        cols[1].metric("Bancos com atividade", len(banks))
        cols[2].metric("Usuários ativos", windows["user"].nunique() if not windows.empty else 0)
        cols[3].metric("Janelas analisadas", format_number(len(windows)))
        cols[4].metric("Alertas novos", state.count_new(alerts))
        st.caption(f"Período: {state.periods_label(periods, names, tz)}")

        st.subheader("Fontes")
        for row in sources.to_dict(orient="records"):
            with st.container(border=True):
                head, badge = st.columns([5, 1])
                head.markdown(f"**{row['fonte']}**  \n`{row['logs']}`")
                with badge:
                    common.source_status_badge(row["status"])
                info = st.columns(4)
                info[0].caption(f"Relógio: {row['relógio']}")
                info[1].caption(f"Analisado até: {row['analisado até']}")
                info[2].caption(f"Última análise: {row['última análise']}")
                info[3].caption(f"Detector: {row['detector']} (limiar {row['limiar']})")
                if row["último erro"]:
                    st.error(f"Último erro: {row['último erro']}", icon=":material/error:")
                elif row["status"] == "aguardando ajuste":
                    st.info(
                        "Fonte sem detector: ajuste-o em **Bancos supervisionados**.",
                        icon=":material/tune:",
                    )

        if alerts.empty:
            st.success("Nenhum alerta no período.", icon=":material/check_circle:")
            return
        left, right = st.columns([3, 2])
        with left:
            st.subheader("Alertas ao longo do tempo, por banco")
            over_time = state.alerts_over_time(alerts, settings.hourly_bins_max_days)
            hourly = state.hourly_bins(alerts, settings.hourly_bins_max_days)
            st.altair_chart(
                charts.alerts_over_time(over_time, common.theme_mode(), hourly), width="stretch"
            )
            common.data_view(over_time)
        with right:
            st.subheader("Usuários com mais alertas")
            by_user = (
                alerts.groupby("user")
                .size()
                .rename("alertas")
                .reset_index()
                .rename(columns={"user": "usuário"})
                .sort_values("alertas", ascending=False)
                .head(10)
            )
            st.altair_chart(charts.users_with_alerts(by_user, common.theme_mode()), width="stretch")
            common.data_view(by_user)

    body()

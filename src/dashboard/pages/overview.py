"""Visão geral: o que está sendo supervisionado e como está a situação agora."""

from __future__ import annotations

import streamlit as st

from dashboard import charts, common, state
from sacla.explain import format_number


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{format_number(count)} {singular if count == 1 else plural}"


def render() -> None:
    st.title("Visão geral")
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
        banks = state.known_databases(store)
        users = windows["user"].nunique() if not windows.empty else 0
        pending = state.count_new(alerts)

        # Situação em uma frase, em vez de uma fileira de números soltos.
        st.html(
            '<p class="sacla-resumo">'
            f"Supervisionando <strong>{_plural(len(banks), 'banco', 'bancos')}</strong> em "
            f"{_plural(len(sources), 'servidor', 'servidores')}. No período, "
            f"{_plural(users, 'usuário teve', 'usuários tiveram')} atividade em "
            f"{_plural(len(windows), 'janela', 'janelas')} de "
            f"{int(state.window_ms(common.sacla_settings()) / 60_000)} minutos, e "
            f"<strong>{_plural(pending, 'alerta aguarda', 'alertas aguardam')}</strong> "
            "triagem.</p>"
        )
        st.caption(f"{common.DISCLAIMER} Período: {state.periods_label(periods, names, tz)}.")

        st.subheader("Servidores supervisionados")
        with st.container(border=True):
            for i, row in enumerate(sources.to_dict(orient="records")):
                if i:
                    st.divider()
                name, situation, coverage, model = st.columns([4, 2, 3, 3])
                name.markdown(f"**{row['fonte']}**")
                name.caption(f"Logs em `{row['logs']}`")
                with situation:
                    common.source_status_badge(row["status"])
                    st.caption(str(row["relógio"]))
                if row["analisado até"] == "—":
                    coverage.markdown("Ainda não analisado")
                else:
                    coverage.markdown(f"Analisado até {row['analisado até']}")
                    coverage.caption(f"Última análise {row['última análise']}")
                if row["detector"] == "—":
                    model.markdown("Sem detector")
                    model.caption("Ajuste em Bancos supervisionados")
                else:
                    model.markdown(row["detector legível"])
                    model.caption(f"Limiar {row['limiar']}")
                if row["último erro"]:
                    st.error(f"Último erro: {row['último erro']}", icon=":material/error:")

        if alerts.empty:
            st.success("Nenhum alerta no período.", icon=":material/check_circle:")
            return
        left, right = st.columns([3, 2], gap="large")
        with left:
            st.subheader("Alertas por banco")
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

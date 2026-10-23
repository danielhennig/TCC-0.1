"""Usuários: atividade por janela, horários comparados ao histórico, bancos e tabelas."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import charts, common, state
from sacla.explain import format_number


def render() -> None:
    st.title("Usuários")
    names = common.source_names()
    store = common.store()
    tz = common.timezone()
    cols = st.columns([2, 2])
    # Abre na primeira fonte que já tem janelas analisadas.
    with_data = [s for s in names if store.has_windows(s)]
    first = list(names).index(with_data[0]) if with_data else 0
    source_id = cols[0].selectbox(
        "Fonte", list(names), index=first, format_func=lambda key: names[key]
    )
    if source_id is None:
        return
    preset = common.period_preset("usuarios")
    period = state.source_periods(store, [source_id], preset)[source_id]
    windows = store.windows(source_id, period.since_ms, period.until_ms)
    if windows.empty:
        st.info("Nenhuma janela analisada nesta fonte e período.")
        return
    user = cols[1].selectbox("Usuário", sorted(windows["user"].unique()))
    mine = windows[windows["user"] == user]
    alerts = store.alerts(source_id, period.since_ms, period.until_ms)
    alerts = alerts[alerts["user"] == user]

    kpi = st.columns(4)
    kpi[0].metric("Janelas com atividade", format_number(len(mine)))
    kpi[1].metric("Janelas sinalizadas", format_number(int(mine["flagged"].sum())))
    kpi[2].metric("Alertas novos", state.count_new(alerts))
    kpi[3].metric("Comandos", format_number(int(mine["n_commands"].sum())))
    st.caption(f"Período: {period.label(tz)}")

    threshold = (store.state(source_id).detector_info or {}).get("limiar")
    timeline = state.timeline_frame(mine, tz, state.window_ms(common.sacla_settings()))
    st.subheader("Score por janela")
    st.altair_chart(
        charts.user_timeline(timeline, threshold, common.theme_mode(), None), width="stretch"
    )
    common.data_view(timeline)

    st.subheader("Horários de atividade")
    profile = _hours(mine, source_id, user, tz)
    st.altair_chart(charts.hours_profile(profile, common.theme_mode()), width="stretch")
    common.data_view(profile)

    left, right = st.columns(2)
    with left:
        st.subheader("Bancos")
        st.dataframe(_frequency(mine["dbs"].map(state.databases_of), "banco"), hide_index=True)
    with right:
        st.subheader("Tabelas")
        tables = mine["tables"].map(lambda v: [p for p in str(v).split("|") if p])
        st.dataframe(_frequency(tables, "tabela"), hide_index=True)


def _hours(windows: pd.DataFrame, source_id: str, user: str, tz: str) -> pd.DataFrame:
    hours = state.local_datetimes(windows["window_start_ms"], tz).dt.hour
    current = hours.value_counts(normalize=True).reindex(range(24), fill_value=0.0)
    rows = [
        {"hora": h, "série": "Período monitorado", "fração": float(v)} for h, v in current.items()
    ]
    detector = common.detector(source_id)
    reference = detector.references.get(user) if detector is not None else None
    if reference is not None and reference.windows:
        rows += [
            {"hora": h, "série": "Histórico (treino)", "fração": c / reference.windows}
            for h, c in enumerate(reference.hour_counts)
        ]
    return pd.DataFrame(rows)


def _frequency(values: pd.Series, label: str) -> pd.DataFrame:
    """Em quantas janelas cada item (lista por janela) aparece."""
    items = values.explode().dropna()
    counts = items.value_counts().rename("janelas").reset_index()
    return counts.rename(columns={counts.columns[0]: label})

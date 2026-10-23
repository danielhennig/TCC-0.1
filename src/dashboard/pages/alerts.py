"""Alertas: filtros, episódios, detalhe explicado e triagem."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import charts, common, state
from sacla.explain import format_number
from sacla.store import TRIAGE_STATUSES

TABLE_KEY = "tabela-episodios"
SHOWN_KEYS = "episodios-exibidos"  # chaves na ordem em que a tabela foi mostrada
SELECTED = "episodio-selecionado"


def _remember_selection() -> None:
    """Guarda a CHAVE do episódio clicado (a posição na tabela muda quando chegam alertas)."""
    event = st.session_state.get(TABLE_KEY)
    rows = event.selection.rows if event is not None else []
    shown = st.session_state.get(SHOWN_KEYS, [])
    st.session_state[SELECTED] = shown[rows[0]] if rows and rows[0] < len(shown) else None


def render() -> None:
    st.title("Alertas")
    st.caption(common.DISCLAIMER)
    names = common.source_names()
    store = common.store()
    settings = common.monitor_settings()

    # Filtros: primeiro quando e em que situação (período e status); depois o quê.
    when = st.columns([5, 6])
    what = st.columns([3, 2, 2, 2])
    with when[0]:
        preset = common.period_preset("alertas")
    chosen_status = when[1].pills(
        "Status",
        list(TRIAGE_STATUSES),
        selection_mode="multi",
        default=["novo", "em análise"],
    )
    chosen_sources = what[0].multiselect(
        "Fonte", list(names), default=list(names), format_func=lambda key: names[key]
    )
    periods = state.source_periods(store, chosen_sources, preset)
    alerts = state.prepare_alerts(
        state.load_alerts(store, periods, settings.max_alerts), common.sacla_settings()
    )
    banks = sorted({b for v in alerts.get("bancos", []) for b in v})
    chosen_banks = what[1].multiselect("Banco", banks, default=banks)
    users = sorted(alerts["user"].unique()) if not alerts.empty else []
    chosen_users = what[2].multiselect("Usuário", users, placeholder="Todos")
    min_score = what[3].slider("Score mínimo", 0.0, 1.0, 0.0, 0.01)

    if not alerts.empty:
        keep = alerts["status"].isin(chosen_status or list(TRIAGE_STATUSES))
        keep &= alerts["score"] >= min_score
        keep &= alerts["bancos"].map(lambda v: bool(set(v) & set(chosen_banks)))
        if chosen_users:
            keep &= alerts["user"].isin(chosen_users)
        alerts = alerts[keep]

    episodes = state.episodes_table(alerts)
    st.caption(
        f"{len(episodes)} episódio(s) com {len(alerts)} janela(s) sinalizada(s). Período: "
        f"{state.periods_label(periods, names, common.timezone())}."
    )
    if episodes.empty:
        st.success(
            "Nenhum alerta com esses filtros. Para ver os já triados, inclua outros status.",
            icon=":material/check_circle:",
        )
        return

    st.session_state[SHOWN_KEYS] = list(episodes["chave"])
    shown = episodes.assign(
        fonte=episodes["source_id"].map(names),
        score=episodes["score_max"].round(3),
    )[["status", "usuário", "bancos", "início", "janelas", "score", "principal desvio", "fonte"]]
    st.dataframe(
        shown,
        hide_index=True,
        on_select=_remember_selection,
        selection_mode="single-row",
        key=TABLE_KEY,
        column_config={
            "score": st.column_config.ProgressColumn(
                "Score máx.", min_value=0.0, max_value=1.0, format="%.3f"
            ),
            "principal desvio": st.column_config.TextColumn(width="large"),
        },
    )
    st.download_button(
        "Exportar alertas filtrados (CSV)",
        alerts.drop(columns=["momento"]).to_csv(index=False).encode("utf-8"),
        file_name="alertas_sacla.csv",
        mime="text/csv",
        icon=":material/download:",
    )
    selected = st.session_state.get(SELECTED)
    match = episodes[episodes["chave"] == selected] if selected else episodes.iloc[0:0]
    if match.empty:
        st.info("Marque um episódio na tabela para ver por que foi sinalizado e fazer a triagem.")
        return
    episode = match.iloc[0]
    _detail(episode, alerts[alerts["chave"] == episode["chave"]])


def _detail(episode: pd.Series, alerts: pd.DataFrame) -> None:
    store = common.store()
    settings = common.monitor_settings()
    tz = common.timezone()
    st.divider()
    head, badge = st.columns([5, 1])
    head.subheader(f"{episode['usuário']}, {episode['início']}")
    head.caption(
        f"Episódio de {episode['janelas']} janela(s) sinalizada(s) seguidas, "
        f"no servidor {common.source_names().get(str(episode['source_id']), episode['source_id'])}."
    )
    with badge:
        common.status_badge(str(episode["status"]))

    first = alerts.sort_values("score", ascending=False).iloc[0]
    summary = pd.DataFrame([state.summary_of(row) for _, row in alerts.iterrows()]).sum()
    cols = st.columns(6)
    cols[0].metric("Comandos", format_number(summary["comandos"]))
    cols[1].metric("Conexões", format_number(summary["conexoes"]))
    cols[2].metric("Linhas retornadas", format_number(summary["linhas_retornadas"]))
    cols[3].metric("Linhas afetadas", format_number(summary["linhas_afetadas"]))
    cols[4].metric("Erros (permissão)", f"{summary['erros']} ({summary['erros_permissao']})")
    cols[5].metric("Falhas de login", format_number(summary["falhas_autenticacao"]))

    st.markdown("**Por que foi sinalizado** (o que mais foge do normal deste usuário)")
    for item in str(first["explanation"]).split(" | "):
        st.markdown(f"- {_plain(item)}")
    new_dbs = sorted({b for v in alerts["dbs_new"] for b in str(v).split("|") if b})
    if new_dbs:
        st.warning(
            "Banco(s) nunca acessado(s) por este usuário no histórico: " + ", ".join(new_dbs),
            icon=":material/database:",
        )
    info = st.columns(3)
    info[0].caption("Bancos: " + ", ".join(sorted({b for v in alerts["bancos"] for b in v})))
    info[1].caption("Tabelas: " + _join(alerts["tables"]))
    info[2].caption("Origem (IPs): " + _join(alerts["ips"]))

    hours = settings.timeline_hours_around
    margin = int(hours * 3_600_000)
    windows = store.windows(
        str(episode["source_id"]),
        int(episode["window_start_ms"]) - margin,
        int(episode["window_end_ms"]) + margin,
        user=str(episode["usuário"]),
    )
    if not windows.empty:
        timeline = state.timeline_frame(windows, tz, state.window_ms(common.sacla_settings()))
        band = (
            state.local_datetimes(pd.Series([int(episode["window_start_ms"])]), tz).iloc[0],
            state.local_datetimes(pd.Series([int(episode["window_end_ms"])]), tz).iloc[0],
        )
        threshold = (store.state(str(episode["source_id"])).detector_info or {}).get("limiar")
        st.markdown(
            f"**Atividade do usuário {format_number(hours)} h antes e depois** (faixa: episódio)"
        )
        st.altair_chart(
            charts.user_timeline(
                timeline,
                threshold,
                common.theme_mode(),
                band,
                domain=(band[0] - pd.Timedelta(hours=hours), band[1] + pd.Timedelta(hours=hours)),
            ),
            width="stretch",
        )
        common.data_view(timeline)

    # Chave do formulário = chave estável do episódio: a triagem nunca vai para outro episódio.
    with st.form(f"triagem-{episode['chave']}"):
        st.markdown("**Triagem**")
        current = str(episode["status"])
        status = st.radio(
            "Status",
            list(TRIAGE_STATUSES),
            index=list(TRIAGE_STATUSES).index(current) if current in TRIAGE_STATUSES else 0,
            horizontal=True,
        )
        note = st.text_area("Anotação", value=str(first["note"] or ""), max_chars=2000)
        if st.form_submit_button("Salvar triagem", icon=":material/save:"):
            ids = [int(i) for i in episode["ids"]]
            store.set_triage(ids, status, note)
            st.success(f"Triagem salva para {len(ids)} janela(s).")
            st.rerun()


def _join(values: pd.Series) -> str:
    items = sorted({p for v in values for p in str(v).split("|") if p})
    return ", ".join(items) if items else "—"


def _plain(text: str) -> str:
    """Texto do log mostrado como texto: escapa a marcação do Markdown."""
    for char in "\\`*_{}[]<>()#+-.!|~":
        text = text.replace(char, "\\" + char)
    return text

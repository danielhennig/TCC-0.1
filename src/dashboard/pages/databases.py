"""Bancos supervisionados: fontes, bancos de cada fonte e ações (analisar, ajustar, reprocessar)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import streamlit as st

from dashboard import common, state
from sacla.config import ConfigError
from sacla.monitor import REPLAY, SourceConfig, fit_source
from sacla.report import display_time, local_stamp


def render() -> None:
    st.title("Bancos supervisionados")
    st.caption(
        "Cada fonte é um servidor PostgreSQL com jsonlog numa pasta; os bancos aparecem a "
        "partir do próprio log (campo dbname)."
    )
    settings = common.monitor_settings()
    store = common.store()
    tz = common.timezone()
    sources = state.sources_table(settings, store, tz).set_index("id")
    banks = state.databases_table(store, settings)

    for source in settings.sources:
        row = sources.loc[source.id]
        with st.container(border=True):
            head, badge = st.columns([5, 1])
            head.subheader(source.name)
            with badge:
                common.source_status_badge(str(row["status"]))
            facts = st.columns(4)
            facts[0].caption(f"Identificador `{source.id}`")
            facts[1].caption(f"Logs em `{source.logs}`")
            facts[2].caption(str(row["relógio"]))
            if row["analisado até"] == "—":
                facts[3].caption("Ainda não analisado")
            else:
                facts[3].caption(
                    f"Analisado até {row['analisado até']}, última análise {row['última análise']}"
                )
            if row["último erro"]:
                st.error(f"Último erro: {row['último erro']}", icon=":material/error:")
            mine = banks[banks["fonte"] == source.name].drop(columns="fonte")
            if mine.empty:
                st.caption(
                    "Ainda não há janelas analisadas. Ajuste o detector abaixo para começar."
                )
            else:
                mine = mine.assign(
                    **{
                        "última atividade": mine.pop("ultima_ms").map(
                            lambda ms: display_time(local_stamp(int(ms), tz))
                        )
                    }
                )
                st.dataframe(mine, hide_index=True)
            _actions(source, str(row["detector"]), str(row["histórico do detector"]))


def _actions(source: SourceConfig, detector: str, history: str) -> None:
    store = common.store()
    settings = common.monitor_settings()
    analyse, fit, rewind = st.tabs(["Analisar agora", "Ajustar detector", "Reprocessar"])
    with analyse:
        st.caption("Roda uma análise já, sem esperar o próximo ciclo do monitor.")
        if st.button("Analisar agora", key=f"{source.id}-analisar", icon=":material/play_arrow:"):
            with st.spinner("Analisando..."):
                result = common.monitor().scan(source)
            if result is None:
                st.info("Nada novo para analisar (ou a fonte está sem detector ou ocupada).")
            else:
                st.success(
                    f"{result.windows} janela(s) analisada(s), "
                    f"{result.new_alerts} alerta(s) novo(s)."
                )
    with fit:
        st.caption(f"Detector atual: {detector} · histórico: {history}")
        st.markdown(
            "O detector aprende o comportamento normal de cada usuário num período de log "
            "considerado **normal** (o histórico)."
        )
        since_default = _date(source.fit_since_ms)
        until_default = _date(source.fit_until_ms)
        with st.form(f"{source.id}-ajuste"):
            cols = st.columns(2)
            use_since = cols[0].checkbox("Limitar o início", value=since_default is not None)
            since = cols[0].date_input("Início (00:00 UTC)", value=since_default or "today")
            use_until = cols[1].checkbox("Limitar o fim", value=until_default is not None)
            until = cols[1].date_input("Fim, exclusive (00:00 UTC)", value=until_default or "today")
            restart = st.checkbox(
                "Reprocessar a fonte do zero depois do ajuste (apaga janelas e alertas da fonte)",
                value=source.clock_mode == REPLAY,
            )
            if st.form_submit_button("Ajustar detector", icon=":material/tune:"):
                since_ms = _ms(since) if use_since else None
                until_ms = _ms(until) if use_until else None
                with st.spinner("Lendo o histórico e ajustando o detector..."):
                    try:
                        fitted = fit_source(
                            settings, source, common.sacla_settings(), since_ms, until_ms, store
                        )
                        if restart:
                            with store.locked(source.id, settings.scan_lock_seconds):
                                store.reset_source(source.id)
                    except (ConfigError, FileNotFoundError) as exc:
                        st.error(str(exc))
                        return
                st.success(
                    f"Detector ajustado: {fitted.info['historico']}; "
                    f"limiar {fitted.info['limiar']:.4f}."
                )
    with rewind:
        st.caption(
            "Volta a análise para uma data anterior. **Manter** reaproveita a triagem dos "
            "alertas; **apagar** remove janelas e alertas a partir da data."
        )
        with st.form(f"{source.id}-reprocessar"):
            day = st.date_input(
                "Reprocessar desde (00:00 UTC)", value=_date(source.replay_start_ms) or "today"
            )
            mode = st.radio(
                "Alertas existentes", ["Manter (com triagem)", "Apagar"], horizontal=True
            )
            confirm = st.checkbox("Confirmo o reprocessamento")
            if st.form_submit_button("Reprocessar", icon=":material/history:") and confirm:
                start = _ms(day)
                try:
                    with store.locked(source.id, settings.scan_lock_seconds):
                        if mode == "Apagar":
                            store.reset_source(source.id, from_ms=start)
                            moved = True
                        else:
                            moved = store.rewind(source.id, start)
                except ConfigError as exc:
                    st.error(str(exc))
                    return
                if moved:
                    st.success("Feito: o monitor reprocessa no próximo ciclo.")
                else:
                    st.warning("A data não é anterior ao ponto já analisado: nada a reprocessar.")
        if source.clock_mode == REPLAY and st.button(
            "Reiniciar a reprodução do começo",
            key=f"{source.id}-reiniciar",
            icon=":material/replay:",
        ):
            try:
                with store.locked(source.id, settings.scan_lock_seconds):
                    store.reset_source(source.id)
            except ConfigError as exc:
                st.error(str(exc))
                return
            st.success("Reprodução reiniciada: começa de novo no próximo ciclo.")


def _date(ms: int | None) -> datetime | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC)


def _ms(day: date | object) -> int:
    value = datetime.combine(day, time(0, 0), tzinfo=UTC)  # type: ignore[arg-type]
    return int(value.timestamp() * 1000)

"""Executa a agenda no PostgreSQL, um evento por vez, na ordem do tempo simulado.

Cada comando leva um comentário `/* simlab:e=<id> */` com o id opaco do evento (mesmo
formato para tráfego normal e anômalo). O `retime` usa a marca para dar a cada linha de log o
horário simulado do evento e depois a remove; o SACLA nunca a vê.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Iterable
from typing import Any

import pandas as pd
import psycopg

from simlab.lab import LabConfig, connect
from simlab.schedule import SimulationSettings
from simlab.timeline import CONNECT, DISCONNECT, FAILED_LOGIN, STATEMENT

log = logging.getLogger(__name__)

TAG = "/* simlab:e={event_id} */ "
LOGIN_FAILED = "falha_login"
LOGIN_UNEXPECTED = "login_inesperado"


class ExecutionError(Exception):
    """Falha que impede continuar a execução (conexão perdida, evento inconsistente)."""


def tag(event_id: int) -> str:
    return TAG.format(event_id=event_id)


def execute_schedule(
    lab: LabConfig, events: pd.DataFrame, settings: SimulationSettings
) -> pd.DataFrame:
    """Executa os eventos e devolve, por evento: horários reais, resultado e linhas."""
    options = f"-c lock_timeout={settings.lock_timeout_ms}"
    connections: dict[int, psycopg.Connection[Any]] = {}
    captures: dict[int, dict[str, Any]] = {}
    n = len(events)
    real_start = [0] * n
    real_end = [0] * n
    outcome = ["ok"] * n
    rows: list[int | None] = [None] * n
    pids: list[int | None] = [None] * n
    started = time.monotonic()
    try:
        rows_iter: Iterable[Any] = events.itertuples(index=False)
        for i, ev in enumerate(rows_iter):
            real_start[i] = time.time_ns() // 1_000_000
            if ev.kind == CONNECT:
                conn = connect(
                    lab,
                    ev.user,
                    lab.sim_password,
                    ev.network,
                    ev.application_name,
                    options=options,
                )
                connections[ev.session] = conn
                captures[ev.session] = {}
                pids[i] = conn.info.backend_pid
            elif ev.kind == STATEMENT:
                outcome[i], rows[i] = _statement(ev, connections, captures)
            elif ev.kind == DISCONNECT:
                connections.pop(ev.session).close()
            elif ev.kind == FAILED_LOGIN:
                outcome[i] = _failed_login(lab, ev, settings.wrong_password)
            else:
                raise ExecutionError(f"tipo de evento desconhecido: {ev.kind}")
            real_end[i] = time.time_ns() // 1_000_000
            if (i + 1) % settings.progress_every == 0:
                elapsed = time.monotonic() - started
                log.info("%d/%d eventos (%.0f eventos/s)", i + 1, n, (i + 1) / elapsed)
    finally:
        for conn in connections.values():
            conn.close()
    log.info("execução concluída em %.1f s", time.monotonic() - started)
    return pd.DataFrame(
        {
            "event_id": events["event_id"].to_numpy(),
            "real_start_ms": real_start,
            "real_end_ms": real_end,
            "outcome": outcome,
            "rows": pd.array(rows, dtype="Int64"),
            "backend_pid": pd.array(pids, dtype="Int64"),
        }
    )


def _statement(
    ev: Any,
    connections: dict[int, psycopg.Connection[Any]],
    captures: dict[int, dict[str, Any]],
) -> tuple[str, int | None]:
    conn = connections.get(ev.session)
    if conn is None:
        raise ExecutionError(f"evento {ev.event_id}: sessão {ev.session} não está aberta")
    params = _resolve(_text(ev.params_json), captures[ev.session])
    capture = _text(ev.capture)
    try:
        with conn.cursor() as cur:
            cur.execute(tag(ev.event_id) + ev.sql, params)
            if cur.description is None:
                # -1 = contagem desconhecida (comandos utilitários, como REFRESH MATERIALIZED VIEW).
                return "ok", cur.rowcount if cur.rowcount >= 0 else None
            data = cur.fetchall()
            if capture:
                captures[ev.session][capture] = data[0][0] if data else None
            return "ok", len(data)
    except psycopg.Error as exc:
        if conn.closed or conn.broken:
            raise ExecutionError(f"evento {ev.event_id}: conexão perdida ({exc})") from exc
        return exc.sqlstate or type(exc).__name__, None


def _text(value: Any) -> str | None:
    """Campos de texto opcionais: no pandas 3 o valor ausente vem como NaN, não como None."""
    return value if isinstance(value, str) else None


def _resolve(params_json: str | None, captured: dict[str, Any]) -> dict[str, Any] | None:
    if params_json is None:
        return None
    params: dict[str, Any] = json.loads(params_json)
    for key, value in params.items():
        if isinstance(value, dict) and "$capture" in value:
            params[key] = captured.get(value["$capture"])
    return params


def _failed_login(lab: LabConfig, ev: Any, wrong_password: str) -> str:
    try:
        conn = connect(lab, ev.user, wrong_password, ev.network, ev.application_name)
    except psycopg.OperationalError as exc:
        # Outras falhas (servidor fora do ar, timeout, rede) não são o evento simulado.
        if "password authentication failed" in str(exc):
            return LOGIN_FAILED
        raise ExecutionError(f"evento {ev.event_id}: falha de conexão inesperada: {exc}") from exc
    conn.close()
    return LOGIN_UNEXPECTED


def execution_summary(
    events: pd.DataFrame, executed: pd.DataFrame, expected_sqlstates: tuple[str, ...]
) -> dict[str, Any]:
    merged = events[["event_id", "kind"]].merge(executed, on="event_id")
    statements = merged[merged["kind"] == STATEMENT]
    errors = Counter(statements.loc[statements["outcome"] != "ok", "outcome"])
    unexpected = {k: v for k, v in errors.items() if k not in expected_sqlstates}
    logins = merged[merged["kind"] == FAILED_LOGIN]
    duration_s = (executed["real_end_ms"].max() - executed["real_start_ms"].min()) / 1000
    return {
        "events": len(merged),
        "statements": len(statements),
        "statement_errors": dict(sorted(errors.items())),
        "unexpected_errors": dict(sorted(unexpected.items())),
        "failed_logins": len(logins),
        "unexpected_logins": int((logins["outcome"] == LOGIN_UNEXPECTED).sum()),
        "real_duration_s": round(float(duration_s), 1),
        "events_per_s": round(len(merged) / max(float(duration_s), 1e-9), 1),
    }

"""Módulo 2a — tratamento: linhas do jsonlog viram eventos de acesso.

Tipos de evento:
- query        comando concluído (linha "duration: ... statement:" ou "... execute <nome>:");
- query_error  comando que falhou (linha ERROR com o campo statement);
- auth_failure falha de autenticação (FATAL com SQLSTATE configurado, ex.: 28P01);
- connection   conexão autorizada ("connection authorized");
- disconnection.

Linhas e tabelas vêm do plano do auto_explain, pareado com o comando pela sessão e pelo texto
("Query Text" = texto do comando). Fatos observados no ambiente (docs/decisoes.md):
- no protocolo simples o plano vem ANTES da linha de duração; no estendido, DEPOIS da linha
  `execute` — os dois casos são tratados;
- as linhas `parse` e `bind` do protocolo estendido não são comandos e são ignoradas;
- comandos utilitários como REFRESH MATERIALIZED VIEW também geram plano, mas as linhas dele
  não são "linhas retornadas"; só SELECT/INSERT/UPDATE/DELETE recebem linhas e tabelas.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sacla.config import EventSettings
from sacla.sqlclass import DATA_CLASSES, SELECT, TCL, classify

QUERY = "query"
QUERY_ERROR = "query_error"
AUTH_FAILURE = "auth_failure"
CONNECTION = "connection"
DISCONNECTION = "disconnection"
COMMAND_KINDS = (QUERY, QUERY_ERROR)

_DURATION = re.compile(
    r"^duration: (?P<ms>\d+(?:\.\d+)?) ms  "
    r"(?P<kind>statement|execute [^:]*|parse [^:]*|bind [^:]*): (?P<sql>.*)$",
    re.DOTALL,
)
_PLAN = re.compile(r"^duration: \d+(?:\.\d+)? ms  plan:\n")
_PERMISSION = re.compile(r"^permission denied for (?:table|view|materialized view|sequence) (\S+)")

EVENT_COLUMNS = (
    "ts_ms",
    "user",
    "dbname",
    "session_id",
    "remote_host",
    "application_name",
    "kind",
    "sql_class",
    "duration_ms",
    "rows_returned",
    "rows_affected",
    "tables",
    "state_code",
    "query_id",
)


@dataclass
class _Event:
    ts_ms: int
    user: str
    dbname: str | None
    session_id: str | None
    remote_host: str | None
    application_name: str | None
    kind: str
    sql_class: str | None = None
    duration_ms: float | None = None
    rows_returned: int | None = None
    rows_affected: int | None = None
    tables: tuple[str, ...] = ()
    state_code: str | None = None
    query_id: int | None = None
    sql: str | None = field(default=None, repr=False)  # só para parear com o plano
    has_plan: bool = field(default=False, repr=False)


@dataclass
class _Plan:
    text: str
    rows_returned: int | None
    rows_affected: int | None
    tables: tuple[str, ...]


@dataclass
class ParseReport:
    events: int = 0
    unpaired_plans: int = 0
    unparseable_plans: int = 0
    ignored_lines: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(vars(self))


def extract_events(
    records: Iterable[Any], settings: EventSettings, report: ParseReport | None = None
) -> pd.DataFrame:
    """Converte LogRecords (em ordem de gravação) em um DataFrame de eventos ordenado."""
    report = report if report is not None else ParseReport()
    events: list[_Event] = []
    users: dict[str, str] = {}  # session_id -> usuário (linhas sem o campo user)
    databases: dict[str, str] = {}  # session_id -> banco (linhas sem o campo dbname)
    pending_plan: dict[str, _Plan] = {}  # protocolo simples: plano à espera do comando
    last_query: dict[str, _Event] = {}  # protocolo estendido: comando à espera do plano

    for rec in records:
        if rec.backend_type not in settings.client_backend_types:
            report.ignored_lines += 1
            continue
        sid = rec.session_id or ""
        if rec.user:
            users[sid] = rec.user
        user = rec.user or users.get(sid)
        if rec.dbname:
            databases[sid] = rec.dbname
        dbname = rec.dbname or databases.get(sid)
        if user is None or user in settings.ignore_users:
            report.ignored_lines += 1
            continue
        message = rec.message

        def new(kind: str, rec: Any = rec, user: str = user, dbname: str | None = dbname) -> _Event:
            return _Event(
                rec.ts_ms, user, dbname, rec.session_id, rec.remote_host, rec.application_name, kind
            )

        if _PLAN.match(message):
            plan = _parse_plan(message, settings)
            if plan is None:
                report.unparseable_plans += 1
                continue
            waiting = last_query.pop(sid, None)
            if waiting is not None and waiting.sql == plan.text and not waiting.has_plan:
                _attach(waiting, plan)  # protocolo estendido: o comando já foi registrado
            else:
                if sid in pending_plan:
                    report.unpaired_plans += 1
                pending_plan[sid] = plan
            continue

        duration = _DURATION.match(message)
        if duration:
            kind = duration.group("kind")
            if kind.startswith(("parse", "bind")):
                continue
            sql = duration.group("sql")
            event = new(QUERY)
            event.sql = sql
            event.sql_class = classify(sql)
            event.duration_ms = float(duration.group("ms"))
            event.query_id = rec.query_id or None
            plan = pending_plan.pop(sid, None)
            if plan is not None and plan.text == sql:
                _attach(event, plan)
            else:
                if plan is not None:
                    report.unpaired_plans += 1
                if kind.startswith("execute"):
                    last_query[sid] = event  # o plano do protocolo estendido vem depois
            events.append(event)
            continue

        if rec.severity == "ERROR" and rec.statement is not None:
            event = new(QUERY_ERROR)
            event.sql_class = classify(rec.statement)
            event.state_code = rec.state_code
            event.query_id = rec.query_id or None
            if rec.state_code in settings.permission_denied_states:
                denied = _PERMISSION.match(message)
                if denied:
                    event.tables = (_alias(denied.group(1), settings),)
            pending_plan.pop(sid, None)
            events.append(event)
        elif rec.severity == "FATAL" and rec.state_code in settings.auth_failure_states:
            event = new(AUTH_FAILURE)
            event.state_code = rec.state_code
            events.append(event)
        elif message.startswith("connection authorized"):
            events.append(new(CONNECTION))
        elif message.startswith("disconnection"):
            events.append(new(DISCONNECTION))
        else:
            report.ignored_lines += 1

    report.unpaired_plans += len(pending_plan)
    report.events = len(events)
    return _frame(events)


def _attach(event: _Event, plan: _Plan) -> None:
    event.has_plan = True
    if event.sql_class not in DATA_CLASSES:
        return  # ex.: REFRESH MATERIALIZED VIEW: as linhas do plano não vão para o cliente
    event.tables = plan.tables
    if event.sql_class == SELECT:
        event.rows_returned = plan.rows_returned
    else:
        event.rows_affected = plan.rows_affected
        event.rows_returned = plan.rows_returned  # RETURNING devolve linhas ao cliente


def _parse_plan(message: str, settings: EventSettings) -> _Plan | None:
    try:
        data = json.loads(message[message.index("\n") + 1 :])
        root = data["Plan"]
        text = str(data["Query Text"])
    except (ValueError, KeyError, TypeError):
        return None
    tables: set[str] = set()
    _collect_relations(root, tables, settings)
    returned = _rows(root)
    affected = None
    if root.get("Node Type") == "ModifyTable":
        children = root.get("Plans") or []
        affected = _rows(children[0]) if children else None
    return _Plan(
        text=text, rows_returned=returned, rows_affected=affected, tables=tuple(sorted(tables))
    )


def _collect_relations(node: dict[str, Any], tables: set[str], settings: EventSettings) -> None:
    name = node.get("Relation Name")
    if isinstance(name, str):
        tables.add(_alias(name, settings))
    for child in node.get("Plans") or []:
        if isinstance(child, dict):
            _collect_relations(child, tables, settings)


def _rows(node: dict[str, Any]) -> int | None:
    rows = node.get("Actual Rows")
    return round(rows) if isinstance(rows, (int, float)) else None


def _alias(name: str, settings: EventSettings) -> str:
    bare = name.rsplit(".", 1)[-1].strip('"')
    for pattern, table in settings.partition_aliases:
        if pattern.match(bare):
            return table
    return bare


def _frame(events: list[_Event]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ts_ms": [e.ts_ms for e in events],
            "user": [e.user for e in events],
            "dbname": [e.dbname for e in events],
            "session_id": [e.session_id for e in events],
            "remote_host": [e.remote_host for e in events],
            "application_name": [e.application_name for e in events],
            "kind": [e.kind for e in events],
            "sql_class": [e.sql_class for e in events],
            "duration_ms": pd.array([e.duration_ms for e in events], dtype="Float64"),
            "rows_returned": pd.array([e.rows_returned for e in events], dtype="Int64"),
            "rows_affected": pd.array([e.rows_affected for e in events], dtype="Int64"),
            "tables": [e.tables for e in events],
            "state_code": [e.state_code for e in events],
            "query_id": pd.array([e.query_id for e in events], dtype="Int64"),
        },
        columns=list(EVENT_COLUMNS),
    )
    return frame.sort_values("ts_ms", kind="stable").reset_index(drop=True)


def command_mask(events: pd.DataFrame) -> pd.Series:
    """Comandos contados nos atributos: concluídos ou com erro, exceto controle de transação."""
    return events["kind"].isin(COMMAND_KINDS) & (events["sql_class"] != TCL)

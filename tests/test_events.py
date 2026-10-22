"""Extração de eventos: sobre as linhas reais da Fase 1 e sobre casos sintéticos."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd

from sacla.config import load_settings
from sacla.events import ParseReport, extract_events
from sacla.reader import LogRecord, read_records

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SETTINGS = load_settings().events


@cache
def _smoke() -> tuple[pd.DataFrame, ParseReport, dict[str, Any]]:
    report = ParseReport()
    events = extract_events(read_records([FIXTURES / "jsonlog_smoke.jsonl"]), SETTINGS, report)
    steps = json.loads((FIXTURES / "jsonlog_smoke_steps.json").read_text(encoding="utf-8"))
    return events, report, steps


def _step(name: str) -> pd.DataFrame:
    events, _, steps = _smoke()
    pids_by_session = steps[name]["pids"]
    return events[events["session_id"].str.endswith(tuple(f".{p:x}" for p in pids_by_session))]


def test_real_log_event_counts_and_no_unpaired_plans() -> None:
    events, report, _ = _smoke()
    counts = events["kind"].value_counts().to_dict()
    assert counts == {
        "query": 8,  # 3 + transação (BEGIN, 2 INSERT, COMMIT) + protocolo estendido
        "connection": 7,
        "disconnection": 7,
        "query_error": 2,
        "auth_failure": 1,
    }
    assert report.unpaired_plans == 0 and report.unparseable_plans == 0


def test_simple_protocol_plan_gives_rows_and_tables() -> None:
    _, _, steps = _smoke()
    normal = _step("consulta_normal").query("kind == 'query'").iloc[0]
    big = _step("leitura_grande").query("kind == 'query'").iloc[0]
    assert (normal.sql_class, normal.rows_returned, normal.tables) == ("SELECT", 1, ("customer",))
    assert big.rows_returned == steps["leitura_grande"]["client_rows"]


def test_update_counts_affected_rows() -> None:
    update = _step("update").query("kind == 'query'").iloc[0]
    assert (update.sql_class, update.rows_affected, update.tables) == ("UPDATE", 1, ("film",))
    assert update.user == "gerente_01" and update.remote_host == "172.30.30.50"


def test_transaction_tcl_returning_and_partition_alias() -> None:
    tx = _step("transacao_app").query("kind == 'query'")
    assert list(tx["sql_class"]) == ["TCL", "INSERT", "INSERT", "TCL"]
    rental, payment = tx.iloc[1], tx.iloc[2]
    assert (rental.rows_affected, rental.rows_returned) == (1, 1)  # RETURNING rental_id
    assert payment.tables == ("payment",)  # plano cita a partição payment_p2026_10
    assert pd.isna(tx.iloc[0].rows_returned) and tx.iloc[0].tables == ()


def test_extended_protocol_plan_after_execute_is_attached() -> None:
    query = _step("protocolo_estendido").query("kind == 'query'")
    assert len(query) == 1  # parse e bind não contam como comandos
    assert (query.iloc[0].rows_returned, query.iloc[0].tables) == (1, ("film",))


def test_errors_and_authentication_failure() -> None:
    denied = _step("sem_permissao").query("kind == 'query_error'").iloc[0]
    syntax = _step("erro_sintaxe").query("kind == 'query_error'").iloc[0]
    events, _, _ = _smoke()
    auth = events.query("kind == 'auth_failure'").iloc[0]
    assert (denied.state_code, denied.tables, denied.sql_class) == ("42501", ("staff",), "SELECT")
    assert (syntax.state_code, syntax.sql_class) == ("42601", "OTHER")
    assert (auth.user, auth.remote_host, auth.state_code) == (
        "atendente_01",
        "172.30.30.50",
        "28P01",
    )


# --- Casos sintéticos ------------------------------------------------------------------------


def _rec(ts: int, sid: str, message: str, **fields: Any) -> LogRecord:
    return LogRecord(
        ts_ms=ts,
        session_id=sid,
        message=message,
        user=fields.pop("user", "u1"),
        backend_type=fields.pop("backend_type", "client backend"),
        **fields,
    )


def _plan(text: str, rows: int, relation: str = "film") -> str:
    plan = {
        "Query Text": text,
        "Plan": {"Node Type": "Seq Scan", "Relation Name": relation, "Actual Rows": float(rows)},
    }
    return "duration: 0.010 ms  plan:\n" + json.dumps(plan)


def test_interleaved_sessions_pair_plans_by_session() -> None:
    a, b = "SELECT * FROM film", "SELECT * FROM actor"
    records = [
        _rec(1, "s1", _plan(a, 10)),
        _rec(2, "s2", _plan(b, 20, "actor"), user="u2"),
        _rec(3, "s2", f"duration: 1.0 ms  statement: {b}", user="u2"),
        _rec(4, "s1", f"duration: 1.0 ms  statement: {a}"),
    ]
    events = extract_events(records, SETTINGS)
    by_user = events.set_index("user")
    assert by_user.loc["u1", "rows_returned"] == 10 and by_user.loc["u1", "tables"] == ("film",)
    assert by_user.loc["u2", "rows_returned"] == 20 and by_user.loc["u2", "tables"] == ("actor",)


def test_refresh_plan_rows_are_not_returned_rows() -> None:
    sql = "REFRESH MATERIALIZED VIEW rental_by_category"
    records = [_rec(1, "s1", _plan(sql, 16)), _rec(2, "s1", f"duration: 30.0 ms  statement: {sql}")]
    event = extract_events(records, SETTINGS).iloc[0]
    assert event.sql_class == "OTHER" and pd.isna(event.rows_returned) and event.tables == ()


def test_admin_users_background_processes_and_user_from_session() -> None:
    records = [
        _rec(
            1, "s1", "connection received: host=x port=1", user=None, backend_type="not initialized"
        ),
        _rec(2, "s1", "connection authorized: user=u1 database=pagila"),
        _rec(3, "s1", "duration: 1.0 ms  statement: SELECT 1", user=None),  # sem o campo user
        _rec(4, "s9", "duration: 1.0 ms  statement: SELECT 1", user="postgres"),
        _rec(5, "s8", "checkpoint starting: time", user=None, backend_type="checkpointer"),
    ]
    report = ParseReport()
    events = extract_events(records, SETTINGS, report)
    assert list(events["kind"]) == ["connection", "query"]
    assert list(events["user"]) == ["u1", "u1"]
    assert report.ignored_lines == 3


def test_plan_with_different_text_is_not_attached() -> None:
    records = [
        _rec(1, "s1", _plan("SELECT 1", 5)),
        _rec(2, "s1", "duration: 1.0 ms  statement: SELECT 2"),
    ]
    report = ParseReport()
    event = extract_events(records, SETTINGS, report).iloc[0]
    assert pd.isna(event.rows_returned) and report.unpaired_plans == 1


def test_execute_after_stale_plan_still_receives_its_own_plan() -> None:
    sql = "SELECT title FROM film WHERE film_id = $1"
    records = [
        _rec(1, "s1", _plan("SELECT 1; SELECT 2", 1)),  # plano sem par (texto diferente)
        _rec(2, "s1", f"duration: 0.1 ms  execute <unnamed>: {sql}"),
        _rec(3, "s1", _plan(sql, 1)),
    ]
    report = ParseReport()
    event = extract_events(records, SETTINGS, report).iloc[0]
    assert event.rows_returned == 1 and event.tables == ("film",)
    assert report.unpaired_plans == 1

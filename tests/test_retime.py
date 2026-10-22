"""Remapeamento de horários sobre um log sintético no formato real do jsonlog.

Cenário: uma sessão (conexão, um SELECT, desconexão), uma falha de login e uma linha de
processo de fundo. No log, a desconexão foi gravada depois da falha de login (gravação
assíncrona); no tempo simulado ela vem antes, e a saída precisa sair reordenada.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from simlab.retime import RetimeError, format_log_ts, parse_log_ts, retime_run

T0 = parse_log_ts("2026-03-02 11:00:00.000 UTC")  # tempo simulado
R0 = parse_log_ts("2026-10-22 02:00:00.000 UTC")  # tempo real da execução
PLAN = {
    "Query Text": "/* simlab:e=2 */ SELECT 1",
    "Plan": {"Node Type": "Result", "Actual Rows": 1.00},
}


def _line(real_ms: int, sid: str, pid: int, message: str, **extra: Any) -> dict[str, Any]:
    return {
        "timestamp": format_log_ts(real_ms),
        "pid": pid,
        "session_id": sid,
        "session_start": "2026-10-22 02:00:00 UTC",
        "error_severity": "LOG",
        "message": message,
        "backend_type": "client backend",
        **extra,
    }


def _write_run(
    tmp_path: Path,
    lines: list[dict[str, Any]],
    failed_logins: int = 1,
    outcome: str = "ok",
    rows: int | None = 1,
) -> Path:
    run = tmp_path / "run"
    (run / "raw").mkdir(parents=True)
    kinds = ["connect", "statement", "disconnect"] + ["failed_login"] * failed_logins
    ts = [T0, T0 + 60_000, T0 + 120_000] + [T0 + 130_000 + i for i in range(failed_logins)]
    pd.DataFrame(
        {
            "event_id": range(1, len(kinds) + 1),
            "ts_ms": ts,
            "kind": kinds,
            "session": [1, 1, 1, *range(2, 2 + failed_logins)],
            "user": ["atendente_01"] * len(kinds),
            "network": ["lan"] * len(kinds),
            "application_name": ["app"] * len(kinds),
            "sql": [None, "SELECT 1", None] + [None] * failed_logins,
            "params_json": [None] * len(kinds),
            "capture": [None] * len(kinds),
        }
    ).to_parquet(run / "events.parquet", index=False)
    real = [R0, R0 + 9, R0 + 19] + [R0 + 20 + i for i in range(failed_logins)]
    pd.DataFrame(
        {
            "event_id": range(1, len(kinds) + 1),
            "real_start_ms": real,
            "real_end_ms": [r + 1 for r in real],
            "outcome": ["ok", outcome, "ok"] + ["falha_login"] * failed_logins,
            "rows": pd.array([None, rows, None] + [None] * failed_logins, dtype="Int64"),
            "backend_pid": pd.array([100, None, None] + [None] * failed_logins, dtype="Int64"),
        }
    ).to_parquet(run / "exec.parquet", index=False)
    with gzip.open(run / "raw" / "postgresql-1.json.gz", "wt", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")
    return run


def _standard_lines() -> list[dict[str, Any]]:
    return [
        _line(
            R0,
            "a.64",
            100,
            "connection received: host=172.30.10.50 port=1",
            backend_type="not initialized",
        ),
        _line(
            R0 + 3,
            "a.64",
            100,
            "connection authorized: user=atendente_01 database=pagila",
            user="atendente_01",
        ),
        _line(
            R0 + 10,
            "a.64",
            100,
            "duration: 0.050 ms  plan:\n" + json.dumps(PLAN),
            user="atendente_01",
        ),
        _line(
            R0 + 11,
            "a.64",
            100,
            "duration: 0.100 ms  statement: /* simlab:e=2 */ SELECT 1",
            user="atendente_01",
        ),
        _line(R0 + 15, "c.1", 30, "checkpoint starting: time", backend_type="checkpointer"),
        _line(
            R0 + 20,
            "b.65",
            101,
            "connection received: host=172.30.30.50 port=2",
            backend_type="not initialized",
        ),
        _line(
            R0 + 21,
            "b.65",
            101,
            'password authentication failed for user "atendente_01"',
            user="atendente_01",
            error_severity="FATAL",
            state_code="28P01",
        ),
        _line(
            R0 + 25,
            "a.64",
            100,
            "disconnection: session time: 0:00:00.025 user=atendente_01 database=pagila",
            user="atendente_01",
        ),
    ]


def _output(run: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted((run / "retimed").glob("*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            records += [json.loads(line) for line in fh]
    return records


def test_lines_get_simulated_times_tags_removed_and_are_reordered(tmp_path: Path) -> None:
    run = _write_run(tmp_path, _standard_lines())

    report = retime_run(run, reorder_window=100)
    out = _output(run)

    assert report["passed"] and report["unmapped_client_lines"] == 0
    assert report["sessions_mapped"] == {"marca": 1, "pid": 0, "ordem_falha_login": 1}
    assert report["rows_check"] == {"compared": 1, "mismatches": 0, "examples": []}
    times = [parse_log_ts(r["timestamp"]) for r in out]
    assert times == sorted(times)
    by_message: dict[str, dict[str, Any]] = {}
    for r in out:  # primeira ocorrência de cada tipo de mensagem, em ordem simulada
        by_message.setdefault(r["message"].split(":")[0], r)
    assert parse_log_ts(by_message["connection received"]["timestamp"]) == T0
    assert parse_log_ts(by_message["connection authorized"]["timestamp"]) == T0 + 3
    assert parse_log_ts(by_message["disconnection"]["timestamp"]) == T0 + 120_000
    assert "session time: 0:02:00.000" in by_message["disconnection"]["message"]
    assert (
        parse_log_ts(
            by_message['password authentication failed for user "atendente_01"']["timestamp"]
        )
        == T0 + 130_000 + 1
    )
    statement = next(r for r in out if "statement:" in r["message"])
    assert statement["message"] == "duration: 0.100 ms  statement: SELECT 1"
    assert parse_log_ts(statement["timestamp"]) == T0 + 60_000 + 1
    assert all("simlab" not in json.dumps(r) for r in out)
    assert by_message["connection received"]["session_start"] == "2026-03-02 11:00:00 UTC"


def test_unmapped_client_session_fails_the_run(tmp_path: Path) -> None:
    lines = _standard_lines()
    lines.append(
        _line(
            R0 + 30,
            "z.99",
            999,
            "connection received: host=x port=3",
            backend_type="not initialized",
        )
    )
    run = _write_run(tmp_path, lines)

    with pytest.raises(RetimeError):
        retime_run(run, reorder_window=100)
    assert json.loads((run / "retime_report.json").read_text())["unmapped_client_lines"] == 1


def test_failed_login_count_mismatch_fails_the_run(tmp_path: Path) -> None:
    run = _write_run(tmp_path, _standard_lines(), failed_logins=2)

    with pytest.raises(RetimeError):
        retime_run(run, reorder_window=100)


def test_background_lines_do_not_leak_the_real_session_start(tmp_path: Path) -> None:
    run = _write_run(tmp_path, _standard_lines())
    retime_run(run, reorder_window=100)

    checkpoint = next(r for r in _output(run) if r["message"].startswith("checkpoint"))
    assert parse_log_ts(checkpoint["timestamp"]) == T0 + 60_000 + 6
    assert checkpoint["session_start"] == "2026-03-02 11:01:00 UTC"


def test_syntax_error_cursor_position_follows_tag_removal(tmp_path: Path) -> None:
    lines = _standard_lines()
    error = _line(
        R0 + 11,
        "a.64",
        100,
        'syntax error at or near "SELCT"',
        user="atendente_01",
        error_severity="ERROR",
        state_code="42601",
        statement="/* simlab:e=2 */ SELCT 1",
        cursor_position=18,
    )
    lines = [line for line in lines if "plan:" not in line["message"]]
    lines = [error if "statement:" in line["message"] else line for line in lines]
    run = _write_run(tmp_path, lines, outcome="42601", rows=None)

    retime_run(run, reorder_window=100)
    out = next(r for r in _output(run) if r.get("state_code") == "42601")

    assert out["statement"] == "SELCT 1"
    assert out["cursor_position"] == 1


def test_unparseable_plan_fails_the_run(tmp_path: Path) -> None:
    lines = _standard_lines()
    lines[2]["message"] = "duration: 0.050 ms  plan:\n{truncado /* simlab:e=2 */"
    run = _write_run(tmp_path, lines)

    with pytest.raises(RetimeError):
        retime_run(run, reorder_window=100)
    assert json.loads((run / "retime_report.json").read_text())["unparseable_plans"] == 1

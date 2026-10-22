"""Leitura incremental e extração do plano do auto_explain, sobre linhas reais da Fase 1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simlab.logtail import LogSnapshot, parse_auto_explain, read_new_records

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SMOKE_LINES = FIXTURES / "jsonlog_smoke.jsonl"
SMOKE_STEPS = FIXTURES / "jsonlog_smoke_steps.json"


def _smoke_records() -> list[dict[str, Any]]:
    with SMOKE_LINES.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _smoke_steps() -> dict[str, Any]:
    steps: dict[str, Any] = json.loads(SMOKE_STEPS.read_text(encoding="utf-8"))
    return steps


def _root_rows_of_step(step: str) -> list[float]:
    pids = set(_smoke_steps()[step]["pids"])
    plans = [parse_auto_explain(r["message"]) for r in _smoke_records() if r.get("pid") in pids]
    return [p["Plan"]["Actual Rows"] for p in plans if p is not None]


def test_parse_auto_explain_root_rows_match_rows_received_by_client() -> None:
    steps = _smoke_steps()
    for step in ("consulta_normal", "leitura_grande"):
        assert _root_rows_of_step(step) == [steps[step]["client_rows"]]


def test_parse_auto_explain_ignores_other_messages() -> None:
    assert parse_auto_explain("duration: 1.2 ms  statement: SELECT 1") is None
    assert parse_auto_explain("connection received: host=10.0.0.1 port=5000") is None
    assert parse_auto_explain("duration: 1.2 ms  plan:\n{truncado") is None


def test_parse_auto_explain_ignores_plan_text_inside_a_statement() -> None:
    message = 'duration: 0.1 ms  statement: SELECT 1 -- plan:\n{"Plan": {}}'
    assert parse_auto_explain(message) is None


def test_read_new_records_starts_after_snapshot_and_skips_malformed(tmp_path: Path) -> None:
    log_file = tmp_path / "postgresql-1.json"
    log_file.write_text('{"message": "antiga"}\n', encoding="utf-8")
    snapshot = LogSnapshot.take(tmp_path, "*.json")
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write('{"message": "nova"}\n{quebrada\n')
    (tmp_path / "postgresql-2.json").write_text('{"message": "rotacionada"}\n', encoding="utf-8")

    records = read_new_records(tmp_path, "*.json", snapshot)

    assert [r["message"] for r in records] == ["nova", "rotacionada"]

"""Remapeamento dos horários do log para o tempo simulado (decisão D13 em docs/decisoes.md).

Cada linha recebe o horário simulado do evento que a produziu, mais o deslocamento real dentro
do evento, limitado para nunca alcançar o evento seguinte. A ligação linha → evento é feita:
1. pela marca `/* simlab:e=<id> */` (comandos, planos do auto_explain e erros), que é removida;
2. pela sessão (`session_id`): linhas de conexão vão para o horário da conexão simulada e a de
   desconexão para o da desconexão;
3. pela ordem de execução, nas falhas de login (conexões que nunca executam comandos);
4. pelo horário real mais próximo, nas linhas de processos de fundo (o SACLA as ignora).
O remapeamento usa só events.parquet e exec.parquet — nunca o manifesto de rótulos.
Toda linha de cliente precisa ser ligada a um evento; caso contrário o retime falha.
"""

from __future__ import annotations

import bisect
import gzip
import heapq
import json
import logging
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from simlab.logtail import AUTO_EXPLAIN_HEADER, parse_auto_explain
from simlab.timeline import CONNECT, DISCONNECT, FAILED_LOGIN, STATEMENT

log = logging.getLogger(__name__)

TAG_RE = re.compile(r"/\* simlab:e=(\d+) \*/ ?")
SESSION_TIME_RE = re.compile(r"session time: \d+:\d{2}:\d{2}\.\d{3}")
CLIENT_BACKENDS = frozenset({"client backend", "not initialized"})
PID_MATCH_TOLERANCE_MS = 10_000
LAST_EVENT_CAP_MS = 3_600_000
MAX_EXAMPLES = 10


class RetimeError(Exception):
    """O log não pôde ser remapeado integralmente."""


class _Line(NamedTuple):
    sid: str | None
    pid: int | None
    real_ms: int
    client: bool
    event: int | None  # id do evento na marca, se houver
    disconnection: bool
    auth_failure_user: str | None  # usuário da linha FATAL 28P01


@dataclass
class _Events:
    ts: list[int]  # índice = event_id - 1
    kind: list[str]
    session: list[int]
    user: list[str]
    outcome: list[str]
    rows: list[int | None]
    real_start: list[int]
    connect_of: dict[int, int]  # sessão simulada -> índice do evento de conexão
    disconnect_of: dict[int, int]
    pid_of: dict[int, int]  # sessão simulada -> PID do backend
    failed_logins: list[int]  # índices, em ordem de execução

    def cap(self, i: int) -> int:
        """Deslocamento máximo após o evento i sem alcançar o evento seguinte."""
        if i + 1 >= len(self.ts):
            return LAST_EVENT_CAP_MS
        return max(0, self.ts[i + 1] - self.ts[i] - 1)


def retime_run(run_dir: Path, reorder_window: int) -> dict[str, Any]:
    events = _load_events(run_dir)
    raw_files = sorted((run_dir / "raw").glob("*.json.gz"))
    if not raw_files:
        raise RetimeError(f"nenhum log bruto em {run_dir / 'raw'}")

    lines, checks = _index(raw_files, events)
    mapping = _map_sessions(lines, events)
    sim, session_start = _assign_times(lines, events, mapping)
    report = _verify(lines, events, mapping, checks)
    outputs, order_violations = _rewrite(
        raw_files, run_dir / "retimed", sim, session_start, reorder_window
    )
    report["order_violations"] = order_violations
    report["output_files"] = outputs
    report["passed"] = report["passed"] and order_violations == 0
    (run_dir / "retime_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if not report["passed"]:
        raise RetimeError(f"verificação do retime falhou; veja {run_dir / 'retime_report.json'}")
    return report


# --- Entrada --------------------------------------------------------------------------------


def _load_events(run_dir: Path) -> _Events:
    ev = pd.read_parquet(run_dir / "events.parquet")
    ex = pd.read_parquet(run_dir / "exec.parquet")
    if len(ev) != len(ex) or not (ev["event_id"].to_numpy() == ex["event_id"].to_numpy()).all():
        raise RetimeError("events.parquet e exec.parquet não estão alinhados")
    kinds = ev["kind"].tolist()
    sessions = ev["session"].tolist()
    pids = ex["backend_pid"].tolist()
    connect_of, disconnect_of, pid_of = {}, {}, {}
    for i, (kind, session) in enumerate(zip(kinds, sessions, strict=True)):
        if kind == CONNECT:
            connect_of[session] = i
            pid_of[session] = int(pids[i])
        elif kind == DISCONNECT:
            disconnect_of[session] = i
    return _Events(
        ts=ev["ts_ms"].tolist(),
        kind=kinds,
        session=sessions,
        user=ev["user"].tolist(),
        outcome=ex["outcome"].tolist(),
        rows=[None if pd.isna(r) else int(r) for r in ex["rows"]],
        real_start=ex["real_start_ms"].tolist(),
        connect_of=connect_of,
        disconnect_of=disconnect_of,
        pid_of=pid_of,
        failed_logins=[i for i, k in enumerate(kinds) if k == FAILED_LOGIN],
    )


def iter_records(raw_files: list[Path]) -> Iterator[dict[str, Any] | None]:
    """Registros JSON em ordem de gravação; None para linhas malformadas."""
    for path in raw_files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    yield None
                    continue
                yield record if isinstance(record, dict) else None


def parse_log_ts(value: str) -> int:
    """'2026-10-22 01:55:55.742 UTC' -> milissegundos UTC."""
    moment = datetime.fromisoformat(value.removesuffix(" UTC")).replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


def format_log_ts(ms: int) -> str:
    moment = datetime.fromtimestamp(ms // 1000, tz=UTC)
    return f"{moment:%Y-%m-%d %H:%M:%S}.{ms % 1000:03d} UTC"


def format_session_start(ms: int) -> str:
    return f"{datetime.fromtimestamp(ms // 1000, tz=UTC):%Y-%m-%d %H:%M:%S} UTC"


# --- Passagem 1: índice ----------------------------------------------------------------------


@dataclass
class _Checks:
    malformed: int = 0
    unparseable_plans: int = 0
    duration_seen: set[int] | None = None
    error_seen: set[int] | None = None
    plan_rows: dict[int, int] | None = None


def _index(raw_files: list[Path], events: _Events) -> tuple[list[_Line | None], _Checks]:
    lines: list[_Line | None] = []
    checks = _Checks(duration_seen=set(), error_seen=set(), plan_rows={})
    assert checks.duration_seen is not None and checks.error_seen is not None
    assert checks.plan_rows is not None
    for rec in iter_records(raw_files):
        if rec is None or "timestamp" not in rec:
            checks.malformed += 1
            lines.append(None)
            continue
        message = rec.get("message", "")
        statement = rec.get("statement") or ""
        match = TAG_RE.search(message) or TAG_RE.search(statement)
        event = int(match.group(1)) if match else None
        if event is not None:
            if AUTO_EXPLAIN_HEADER.match(message):
                plan = parse_auto_explain(message)
                rows = plan_rows(plan) if plan is not None else None
                if plan is None:
                    checks.unparseable_plans += 1
                elif rows is not None:
                    checks.plan_rows[event] = rows
            elif message.startswith("duration:") and "  statement: " in message:
                checks.duration_seen.add(event)
            if rec.get("error_severity") == "ERROR" and TAG_RE.search(statement):
                checks.error_seen.add(event)
        lines.append(
            _Line(
                sid=rec.get("session_id"),
                pid=rec.get("pid"),
                real_ms=parse_log_ts(rec["timestamp"]),
                client=rec.get("backend_type") in CLIENT_BACKENDS,
                event=event,
                disconnection=message.startswith("disconnection:"),
                auth_failure_user=rec.get("user") if rec.get("state_code") == "28P01" else None,
            )
        )
    return lines, checks


def plan_rows(plan: dict[str, Any]) -> int | None:
    """Linhas retornadas (nó raiz) ou afetadas (filho do ModifyTable) segundo o plano."""
    root = plan.get("Plan", {})
    node = root
    if root.get("Node Type") == "ModifyTable":
        children = root.get("Plans") or []
        if not children:
            return None
        node = children[0]
    rows = node.get("Actual Rows")
    return None if rows is None else round(rows)


# --- Ligação sessão do log -> sessão simulada ----------------------------------------------


@dataclass
class _Mapping:
    session_of: dict[str, int]  # session_id do log -> sessão simulada
    failed_login_of: dict[str, int]  # session_id do log -> índice do evento de falha de login
    how: dict[str, int]
    conflicts: list[str]
    unmapped_client_lines: int
    user_mismatches: list[str]
    auth_failure_sessions: int


def _map_sessions(lines: list[_Line | None], events: _Events) -> _Mapping:
    by_sid: dict[str, list[int]] = defaultdict(list)
    for idx, line in enumerate(lines):
        if line is not None and line.client and line.sid:
            by_sid[line.sid].append(idx)

    session_of: dict[str, int] = {}
    conflicts: list[str] = []
    how = {"marca": 0, "pid": 0, "ordem_falha_login": 0}
    for sid, idxs in by_sid.items():
        sims = {events.session[ev - 1] for i in idxs if (ev := _event(lines[i])) is not None}
        if len(sims) > 1:
            conflicts.append(f"{sid}: sessões simuladas {sorted(sims)}")
        elif sims:
            session_of[sid] = sims.pop()
            how["marca"] += 1

    used = set(session_of.values())
    by_pid: dict[int, list[int]] = defaultdict(list)
    for session, pid in events.pid_of.items():
        if session not in used:
            by_pid[pid].append(session)
    auth_failures = []
    for sid, idxs in by_sid.items():
        if sid in session_of:
            continue
        first = lines[idxs[0]]
        assert first is not None
        if any(_auth_user(lines[i]) is not None for i in idxs):
            auth_failures.append((first.real_ms, idxs[0], sid))
            continue
        candidates = [
            s
            for s in by_pid.get(first.pid or -1, [])
            if abs(events.real_start[events.connect_of[s]] - first.real_ms)
            <= PID_MATCH_TOLERANCE_MS
        ]
        if len(candidates) == 1:
            session_of[sid] = candidates[0]
            by_pid[first.pid or -1].remove(candidates[0])
            how["pid"] += 1

    failed_login_of: dict[str, int] = {}
    mismatches: list[str] = []
    auth_failures.sort()
    for (_, _, sid), event_idx in zip(auth_failures, events.failed_logins, strict=False):
        failed_login_of[sid] = event_idx
        how["ordem_falha_login"] += 1
        log_user = next(u for i in by_sid[sid] if (u := _auth_user(lines[i])) is not None)
        if log_user != events.user[event_idx]:
            mismatches.append(f"{sid}: log={log_user} evento={events.user[event_idx]}")
    if len(auth_failures) != len(events.failed_logins):
        mismatches.append(
            f"{len(auth_failures)} falhas de login no log, {len(events.failed_logins)} na agenda"
        )

    unmapped = sum(
        len(idxs)
        for sid, idxs in by_sid.items()
        if sid not in session_of and sid not in failed_login_of
    )
    return _Mapping(
        session_of, failed_login_of, how, conflicts, unmapped, mismatches, len(auth_failures)
    )


def _event(line: _Line | None) -> int | None:
    return line.event if line is not None else None


def _auth_user(line: _Line | None) -> str | None:
    return line.auth_failure_user if line is not None else None


# --- Horários simulados ---------------------------------------------------------------------


def _assign_times(
    lines: list[_Line | None], events: _Events, mapping: _Mapping
) -> tuple[list[int | None], dict[str, int]]:
    sim: list[int | None] = [None] * len(lines)
    session_start: dict[str, int] = {}
    by_sid: dict[str, list[int]] = defaultdict(list)
    background: list[int] = []
    for idx, line in enumerate(lines):
        if line is None:
            continue
        if (line.client and line.sid in mapping.session_of) or line.sid in mapping.failed_login_of:
            by_sid[line.sid or ""].append(idx)
        elif not line.client:
            background.append(idx)

    for sid, idxs in by_sid.items():
        first = lines[idxs[0]]
        assert first is not None
        if sid in mapping.failed_login_of:
            event = mapping.failed_login_of[sid]
            session_start[sid] = events.ts[event]
            for idx in idxs:
                line = lines[idx]
                assert line is not None
                sim[idx] = _shift(events, event, line.real_ms - first.real_ms)
            continue
        sim_session = mapping.session_of[sid]
        connect = events.connect_of[sim_session]
        session_start[sid] = events.ts[connect]
        anchor, anchor_real = connect, first.real_ms
        anchor_real_of: dict[int, int] = {}
        for idx in idxs:
            line = lines[idx]
            assert line is not None
            if line.event is not None:
                event = line.event - 1
                anchor_real_of.setdefault(event, line.real_ms)
                anchor, anchor_real = event, anchor_real_of[event]
                sim[idx] = _shift(events, event, line.real_ms - anchor_real)
            elif line.disconnection and sim_session in events.disconnect_of:
                sim[idx] = events.ts[events.disconnect_of[sim_session]]
            else:
                sim[idx] = _shift(events, anchor, line.real_ms - anchor_real)

    for idx in background:
        line = lines[idx]
        assert line is not None
        i = max(0, bisect.bisect_right(events.real_start, line.real_ms) - 1)
        sim[idx] = _shift(events, i, line.real_ms - events.real_start[i])
    return sim, session_start


def _shift(events: _Events, event: int, delta_ms: int) -> int:
    return events.ts[event] + min(max(delta_ms, 0), events.cap(event))


# --- Verificações ---------------------------------------------------------------------------


def _verify(
    lines: list[_Line | None], events: _Events, mapping: _Mapping, checks: _Checks
) -> dict[str, Any]:
    assert checks.duration_seen is not None and checks.error_seen is not None
    assert checks.plan_rows is not None
    missing_duration, missing_error = [], []
    for i, kind in enumerate(events.kind):
        if kind != STATEMENT:
            continue
        event_id = i + 1
        if events.outcome[i] == "ok" and event_id not in checks.duration_seen:
            missing_duration.append(event_id)
        if events.outcome[i] != "ok" and event_id not in checks.error_seen:
            missing_error.append(event_id)
    mismatches = [
        {"event_id": ev, "plano": rows, "cliente": events.rows[ev - 1]}
        for ev, rows in checks.plan_rows.items()
        if events.rows[ev - 1] is not None and rows != events.rows[ev - 1]
    ]
    client = sum(1 for line in lines if line is not None and line.client)
    report: dict[str, Any] = {
        "lines": len(lines),
        "malformed_lines": checks.malformed,
        "unparseable_plans": checks.unparseable_plans,
        "client_lines": client,
        "background_lines": sum(1 for line in lines if line is not None and not line.client),
        "sessions_mapped": mapping.how,
        "session_conflicts": mapping.conflicts[:MAX_EXAMPLES],
        "unmapped_client_lines": mapping.unmapped_client_lines,
        "failed_logins": {
            "scheduled": len(events.failed_logins),
            "in_log": mapping.auth_failure_sessions,
            "problems": mapping.user_mismatches[:MAX_EXAMPLES],
        },
        "statements": {
            "executed": sum(1 for k in events.kind if k == STATEMENT),
            "missing_duration_line": len(missing_duration),
            "missing_error_line": len(missing_error),
            "examples": (missing_duration + missing_error)[:MAX_EXAMPLES],
        },
        "rows_check": {
            "compared": sum(1 for ev in checks.plan_rows if events.rows[ev - 1] is not None),
            "mismatches": len(mismatches),
            "examples": mismatches[:MAX_EXAMPLES],
        },
    }
    report["passed"] = (
        checks.malformed == 0
        and checks.unparseable_plans == 0
        and not mapping.conflicts
        and mapping.unmapped_client_lines == 0
        and not mapping.user_mismatches
        and not missing_duration
        and not missing_error
        and not mismatches
    )
    return report


# --- Passagem 2: reescrita ------------------------------------------------------------------


def _rewrite(
    raw_files: list[Path],
    out_dir: Path,
    sim: list[int | None],
    session_start: dict[str, int],
    reorder_window: int,
) -> tuple[list[str], int]:
    """Reescreve as linhas e as grava em ordem de horário simulado, um arquivo por dia (UTC).

    Linhas assíncronas (como a desconexão) podem ser gravadas depois de eventos posteriores;
    uma janela de `reorder_window` linhas reordena esses casos. Uma linha que ainda saia fora
    de ordem conta como violação.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.json.gz"):
        old.unlink()
    new_sid: dict[str, str] = {}
    heap: list[tuple[int, int, str]] = []
    writer = _DayWriter(out_dir)
    violations = 0
    last = -1

    def emit(item: tuple[int, int, str]) -> None:
        nonlocal violations, last
        ts, _, text = item
        if ts < last:
            violations += 1
        last = max(last, ts)
        writer.write(ts, text)

    for idx, rec in enumerate(iter_records(raw_files)):
        ts = sim[idx]
        if rec is None or ts is None:
            continue
        rec["timestamp"] = format_log_ts(ts)
        sid = rec.get("session_id")
        if sid is not None and sid not in session_start:
            # Processo de fundo: o início real não pode vazar; vale a primeira linha simulada.
            session_start[sid] = ts
        if sid in session_start:
            start = session_start[sid]
            rec["session_start"] = format_session_start(start)
            rec["session_id"] = new_sid.setdefault(sid, f"{start // 1000:x}.{rec.get('pid', 0):x}")
            message = rec.get("message", "")
            if message.startswith("disconnection:"):
                elapsed = ts - start
                rec["message"] = SESSION_TIME_RE.sub(f"session time: {_elapsed(elapsed)}", message)
        statement = rec.get("statement")
        if isinstance(statement, str):
            match = TAG_RE.search(statement)
            position = rec.get("cursor_position")
            # cursor_position conta caracteres do comando; sem a marca, recua o tamanho dela.
            if match and isinstance(position, int) and position > match.start():
                rec["cursor_position"] = max(1, position - (match.end() - match.start()))
            rec["statement"] = TAG_RE.sub("", statement)
        if isinstance(rec.get("message"), str):
            rec["message"] = TAG_RE.sub("", rec["message"])
        heapq.heappush(heap, (ts, idx, json.dumps(rec, ensure_ascii=False)))
        if len(heap) > reorder_window:
            emit(heapq.heappop(heap))
    while heap:
        emit(heapq.heappop(heap))
    writer.close()
    if len(set(new_sid.values())) != len(new_sid):
        violations += len(new_sid) - len(set(new_sid.values()))
        log.error("session_id remapeados colidiram")
    return writer.files, violations


def _elapsed(ms: int) -> str:
    seconds, millis = divmod(max(ms, 0), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class _DayWriter:
    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.day: str | None = None
        self.handle: Any = None
        self.files: list[str] = []

    def write(self, ts: int, text: str) -> None:
        day = datetime.fromtimestamp(ts // 1000, tz=UTC).strftime("%Y%m%d")
        if day != self.day:
            self.close()
            name = f"postgresql-{day}_000000.json.gz"
            mode = "at" if name in self.files else "wt"
            # Fica aberto entre chamadas de write(); fechado em close().
            self.handle = gzip.open(self.out_dir / name, mode, encoding="utf-8")  # noqa: SIM115
            if name not in self.files:
                self.files.append(name)
            self.day = day
        self.handle.write(text + "\n")

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

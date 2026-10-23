"""Estado do monitor contínuo em SQLite: fontes, arquivos, análises, janelas e alertas.

Um único arquivo (`<state_dir>/sacla.sqlite`), em modo WAL, lido pelo painel e escrito pelo
monitor. As chaves (fonte, usuário, início da janela) tornam as gravações idempotentes:
reprocessar um período não duplica janelas nem alertas, e a triagem de um alerta já existente
é preservada.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sacla.config import ConfigError

NEW = "novo"
TRIAGE_STATUSES = ("novo", "em análise", "suspeito confirmado", "falso positivo")
WAITING_FIT = "aguardando ajuste"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_state (
    source_id TEXT PRIMARY KEY,
    last_window_end_ms INTEGER,
    replay_started_at REAL,
    status TEXT NOT NULL DEFAULT 'aguardando ajuste',
    last_error TEXT,
    last_scan_at REAL,
    lock_until REAL NOT NULL DEFAULT 0,
    detector_info TEXT
);
CREATE TABLE IF NOT EXISTS log_files (
    source_id TEXT NOT NULL,
    path TEXT NOT NULL,
    first_ts_ms INTEGER,
    PRIMARY KEY (source_id, path)
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    since_ms INTEGER,
    until_ms INTEGER,
    windows INTEGER,
    alerts INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS windows (
    source_id TEXT NOT NULL,
    user TEXT NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    inicio TEXT NOT NULL,
    dbs TEXT NOT NULL,
    score REAL NOT NULL,
    flagged INTEGER NOT NULL,
    n_commands INTEGER NOT NULL,
    n_errors INTEGER NOT NULL,
    n_auth_failures INTEGER NOT NULL,
    rows_returned INTEGER NOT NULL,
    rows_affected INTEGER NOT NULL,
    tables TEXT NOT NULL,
    ips TEXT NOT NULL,
    PRIMARY KEY (source_id, user, window_start_ms)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL,
    user TEXT NOT NULL,
    window_start_ms INTEGER NOT NULL,
    window_end_ms INTEGER NOT NULL,
    inicio TEXT NOT NULL,
    fim TEXT NOT NULL,
    dbs TEXT NOT NULL,
    dbs_new TEXT NOT NULL,
    score REAL NOT NULL,
    percentile REAL NOT NULL,
    no_history INTEGER NOT NULL,
    explanation TEXT NOT NULL,
    features TEXT NOT NULL,
    summary TEXT NOT NULL,
    tables TEXT NOT NULL,
    ips TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'novo',
    note TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (source_id, user, window_start_ms)
);
CREATE INDEX IF NOT EXISTS alerts_by_time ON alerts (source_id, window_start_ms);
CREATE INDEX IF NOT EXISTS alerts_by_status ON alerts (status);
CREATE INDEX IF NOT EXISTS windows_by_time ON windows (source_id, window_start_ms);
"""

_STATE_FIELDS = (
    "last_window_end_ms",
    "replay_started_at",
    "status",
    "last_error",
    "last_scan_at",
    "detector_info",
)


@dataclass(frozen=True)
class SourceState:
    source_id: str
    last_window_end_ms: int | None
    replay_started_at: float | None
    status: str
    last_error: str | None
    last_scan_at: float | None
    detector_info: dict[str, Any] | None


class Store:
    """Acesso ao SQLite; cada operação abre e fecha a própria conexão (seguro entre processos)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA foreign_keys=ON")
            db.row_factory = sqlite3.Row
            yield db
            db.commit()
        finally:
            db.close()

    # --- Estado das fontes -------------------------------------------------------------------

    def ensure_source(self, source_id: str) -> None:
        with self._connect() as db:
            db.execute("INSERT OR IGNORE INTO source_state (source_id) VALUES (?)", (source_id,))

    def state(self, source_id: str) -> SourceState:
        self.ensure_source(source_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM source_state WHERE source_id = ?", (source_id,)
            ).fetchone()
        info = json.loads(row["detector_info"]) if row["detector_info"] else None
        return SourceState(
            source_id=source_id,
            last_window_end_ms=row["last_window_end_ms"],
            replay_started_at=row["replay_started_at"],
            status=row["status"],
            last_error=row["last_error"],
            last_scan_at=row["last_scan_at"],
            detector_info=info,
        )

    def update_state(self, source_id: str, **fields: Any) -> None:
        unknown = set(fields) - set(_STATE_FIELDS)
        if unknown:
            raise ConfigError(f"campos de estado desconhecidos: {sorted(unknown)}")
        if "detector_info" in fields and fields["detector_info"] is not None:
            fields["detector_info"] = json.dumps(fields["detector_info"], ensure_ascii=False)
        self.ensure_source(source_id)
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._connect() as db:
            db.execute(
                f"UPDATE source_state SET {assignments} WHERE source_id = ?",
                (*fields.values(), source_id),
            )

    @contextmanager
    def locked(
        self, source_id: str, seconds: float, wait: float = 30.0, poll: float = 0.2
    ) -> Iterator[None]:
        """Trava a fonte durante uma ação do painel (limpar, reprocessar), esperando a análise
        em curso terminar; sem isso, a análise desfaria a ação ao gravar o seu resultado."""
        deadline = time.monotonic() + wait
        while not self.try_lock(source_id, seconds):
            if time.monotonic() > deadline:
                raise ConfigError(f"fonte {source_id} ocupada por uma análise; tente de novo")
            time.sleep(poll)
        try:
            yield
        finally:
            self.unlock(source_id)

    def advance(self, source_id: str, expected_ms: int | None, new_ms: int, now: float) -> bool:
        """Move o ponteiro só se ninguém o mudou desde o início da análise."""
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE source_state SET last_window_end_ms = ?, status = 'ativo',"
                " last_error = NULL, last_scan_at = ? WHERE source_id = ?"
                " AND last_window_end_ms IS ?",
                (new_ms, now, source_id, expected_ms),
            )
            return cursor.rowcount == 1

    def try_lock(self, source_id: str, seconds: float, now: float | None = None) -> bool:
        """Trava a fonte para uma análise (monitor e painel não analisam a mesma ao mesmo tempo)."""
        now = time.time() if now is None else now
        self.ensure_source(source_id)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE source_state SET lock_until = ? WHERE source_id = ? AND lock_until < ?",
                (now + seconds, source_id, now),
            )
            return cursor.rowcount == 1

    def unlock(self, source_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE source_state SET lock_until = 0 WHERE source_id = ?", (source_id,))

    def reset_source(self, source_id: str, from_ms: int | None = None) -> None:
        """Apaga janelas, alertas e análises (desde `from_ms`, ou tudo) e volta o ponteiro."""
        with self._connect() as db:
            if from_ms is None:
                for table in ("windows", "alerts", "scans", "log_files"):
                    db.execute(f"DELETE FROM {table} WHERE source_id = ?", (source_id,))
                db.execute(
                    "UPDATE source_state SET last_window_end_ms = NULL, replay_started_at = NULL,"
                    " last_error = NULL WHERE source_id = ?",
                    (source_id,),
                )
            else:
                for table in ("windows", "alerts"):
                    db.execute(
                        f"DELETE FROM {table} WHERE source_id = ? AND window_start_ms >= ?",
                        (source_id, from_ms),
                    )
                db.execute(
                    "UPDATE source_state SET last_window_end_ms = ? WHERE source_id = ?"
                    " AND (last_window_end_ms IS NULL OR last_window_end_ms > ?)",
                    (from_ms, source_id, from_ms),
                )

    def rewind(self, source_id: str, from_ms: int) -> bool:
        """Volta o ponteiro sem apagar nada: reprocessa mantendo a triagem dos alertas.

        Só volta: uma data depois do ponto atual pularia janelas sem analisá-las.
        """
        self.ensure_source(source_id)
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE source_state SET last_window_end_ms = ? WHERE source_id = ?"
                " AND last_window_end_ms > ?",
                (from_ms, source_id, from_ms),
            )
            return cursor.rowcount == 1

    # --- Arquivos de log ---------------------------------------------------------------------

    def file_starts(self, source_id: str) -> dict[str, int | None]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT path, first_ts_ms FROM log_files WHERE source_id = ?", (source_id,)
            ).fetchall()
        return {str(r["path"]): r["first_ts_ms"] for r in rows}

    def save_file_start(self, source_id: str, path: str, first_ts_ms: int | None) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO log_files (source_id, path, first_ts_ms) VALUES (?, ?, ?)",
                (source_id, path, first_ts_ms),
            )

    # --- Análises ----------------------------------------------------------------------------

    def start_scan(self, source_id: str, since_ms: int, until_ms: int) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO scans (source_id, started_at, since_ms, until_ms) VALUES (?, ?, ?, ?)",
                (source_id, time.time(), since_ms, until_ms),
            )
            return int(cursor.lastrowid or 0)

    def finish_scan(
        self, scan_id: int, windows: int, alerts: int, error: str | None = None
    ) -> None:
        """Fecha a análise. Uma análise sem janelas e sem erro não é guardada: depois do fim de
        uma reprodução (ou numa fonte parada) seriam milhares de linhas vazias por dia."""
        with self._connect() as db:
            if windows == 0 and error is None:
                db.execute("DELETE FROM scans WHERE id = ?", (scan_id,))
                return
            db.execute(
                "UPDATE scans SET finished_at = ?, windows = ?, alerts = ?, error = ? WHERE id = ?",
                (time.time(), windows, alerts, error, scan_id),
            )

    def scans(self, limit: int = 50) -> pd.DataFrame:
        with self._connect() as db:
            return pd.read_sql_query(
                "SELECT * FROM scans ORDER BY id DESC LIMIT ?", db, params=(limit,)
            )

    # --- Janelas e alertas -------------------------------------------------------------------

    def save_windows(self, source_id: str, described: pd.DataFrame, flagged: list[bool]) -> int:
        """Grava janelas pontuadas (saída de `sacla.report.describe_windows`)."""
        rows = [
            (
                source_id,
                r["usuario"],
                int(r["window_start_ms"]),
                int(r["window_end_ms"]),
                r["inicio"],
                r["bancos"],
                float(r["score"]),
                int(flag),
                int(r["comandos"]),
                int(r["erros"]),
                int(r["falhas_autenticacao"]),
                int(r["linhas_retornadas"]),
                int(r["linhas_afetadas"]),
                r["tabelas"],
                r["ips"],
            )
            for r, flag in zip(described.to_dict(orient="records"), flagged, strict=True)
        ]
        with self._connect() as db:
            db.executemany(
                f"INSERT OR REPLACE INTO windows VALUES ({', '.join('?' * 15)})",
                rows,
            )
        return len(rows)

    def save_alerts(
        self, source_id: str, described: pd.DataFrame, summary_columns: list[str]
    ) -> int:
        """Grava alertas; devolve quantos são novos. Triagem e nota existentes são mantidas."""
        now = time.time()
        new = 0
        with self._connect() as db:
            for r in described.to_dict(orient="records"):
                values = {
                    "dbs": r["bancos"],
                    "dbs_new": r["bancos_novos"],
                    "score": float(r["score"]),
                    "percentile": float(r["percentil_historico"]),
                    "no_history": int(bool(r["sem_historico"])),
                    "explanation": r["explicacao"],
                    "features": r["atributos"],
                    "summary": json.dumps({c: int(r[c]) for c in summary_columns}),
                    "tables": r["tabelas"],
                    "ips": r["ips"],
                }
                key = (source_id, r["usuario"], int(r["window_start_ms"]))
                cursor = db.execute(
                    "INSERT OR IGNORE INTO alerts (source_id, user, window_start_ms, window_end_ms,"
                    " inicio, fim, dbs, dbs_new, score, percentile, no_history, explanation,"
                    " features, summary, tables, ips, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        *key,
                        int(r["window_end_ms"]),
                        r["inicio"],
                        r["fim"],
                        *values.values(),
                        now,
                        now,
                    ),
                )
                if cursor.rowcount == 1:
                    new += 1
                    continue
                assignments = ", ".join(f"{name} = ?" for name in values)
                db.execute(
                    f"UPDATE alerts SET {assignments} WHERE source_id = ? AND user = ?"
                    " AND window_start_ms = ?",
                    (*values.values(), *key),
                )
        return new

    def set_triage(self, alert_ids: list[int], status: str, note: str | None = None) -> None:
        if status not in TRIAGE_STATUSES:
            raise ConfigError(f"status de triagem inválido: {status!r}")
        with self._connect() as db:
            for alert_id in alert_ids:
                if note is None:
                    db.execute(
                        "UPDATE alerts SET status = ?, updated_at = ? WHERE id = ?",
                        (status, time.time(), alert_id),
                    )
                else:
                    db.execute(
                        "UPDATE alerts SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                        (status, note, time.time(), alert_id),
                    )

    def alerts(
        self,
        source_id: str | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        where, params = self._where(source_id, since_ms, until_ms)
        sql = f"SELECT * FROM alerts{where} ORDER BY window_start_ms DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as db:
            return pd.read_sql_query(sql, db, params=params)

    def prune_alerts(
        self, source_id: str, since_ms: int, until_ms: int, keep: set[tuple[str, int]]
    ) -> int:
        """Apaga alertas ainda não triados do período que o detector atual não gera mais.

        Alertas já triados ficam, com a triagem: são registro do analista.
        """
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, user, window_start_ms FROM alerts WHERE source_id = ? AND status = ?"
                " AND window_start_ms >= ? AND window_start_ms < ?",
                (source_id, NEW, since_ms, until_ms),
            ).fetchall()
            stale = [r["id"] for r in rows if (r["user"], r["window_start_ms"]) not in keep]
            db.executemany("DELETE FROM alerts WHERE id = ?", [(i,) for i in stale])
        return len(stale)

    def latest_window_end(self, source_id: str) -> int | None:
        """Fim da janela mais recente COM atividade (a reprodução pode ter passado do log)."""
        with self._connect() as db:
            row = db.execute(
                "SELECT MAX(window_end_ms) AS fim FROM windows WHERE source_id = ?", (source_id,)
            ).fetchone()
        return None if row["fim"] is None else int(row["fim"])

    def has_windows(self, source_id: str) -> bool:
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM windows WHERE source_id = ? LIMIT 1", (source_id,)
            ).fetchone()
        return row is not None

    def windows(
        self,
        source_id: str | None = None,
        since_ms: int | None = None,
        until_ms: int | None = None,
        user: str | None = None,
    ) -> pd.DataFrame:
        where, params = self._where(source_id, since_ms, until_ms)
        if user is not None:
            where += (" AND" if where else " WHERE") + " user = ?"
            params.append(user)
        with self._connect() as db:
            return pd.read_sql_query(
                f"SELECT * FROM windows{where} ORDER BY window_start_ms", db, params=params
            )

    def counts(self) -> pd.DataFrame:
        """Por fonte, banco e usuário: janelas, alertas, alertas novos e última atividade."""
        with self._connect() as db:
            windows = pd.read_sql_query(
                "SELECT source_id, dbs, user, COUNT(*) AS janelas,"
                " MAX(window_end_ms) AS ultima_atividade_ms, SUM(flagged) AS sinalizadas"
                " FROM windows GROUP BY source_id, dbs, user",
                db,
            )
            alerts = pd.read_sql_query(
                "SELECT source_id, dbs, user, COUNT(*) AS alertas,"
                " SUM(status = 'novo') AS alertas_novos FROM alerts GROUP BY source_id, dbs, user",
                db,
            )
        keys = ["source_id", "dbs", "user"]
        merged = windows.merge(alerts, on=keys, how="outer")
        for column in ("janelas", "sinalizadas", "alertas", "alertas_novos"):
            merged[column] = merged[column].fillna(0).astype(int)
        return merged

    @staticmethod
    def _where(
        source_id: str | None, since_ms: int | None, until_ms: int | None
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if since_ms is not None:
            clauses.append("window_start_ms >= ?")
            params.append(since_ms)
        if until_ms is not None:
            clauses.append("window_start_ms < ?")
            params.append(until_ms)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

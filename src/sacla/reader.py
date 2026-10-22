"""Módulo 1 — leitura dos arquivos jsonlog do PostgreSQL.

Robustez exigida por logs reais:
- vários arquivos (rotação), lidos em ordem de nome, comprimidos ou não;
- última linha incompleta (arquivo ainda em gravação) é descartada e contada à parte; um
  arquivo .gz cortado no meio também (as linhas anteriores ao corte são aproveitadas);
- linhas que não são JSON, ou sem horário válido, são descartadas e contadas; um fuso não
  reconhecido no horário é contado à parte, com erro explícito (ver reader.zone_offsets_min);
- campos ausentes viram None; campos desconhecidos são ignorados (a documentação avisa que
  novos campos podem surgir).
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Any

log = logging.getLogger(__name__)

# Deslocamento numérico que o PostgreSQL escreve no timestamp (ex.: "-03", "+0530").
_OFFSET = re.compile(r"^([+-])(\d{2})(?::?(\d{2}))?$")
DEFAULT_ZONES = {"UTC": 0, "GMT": 0}


class UnknownZoneError(ValueError):
    """Abreviação de fuso que não está em reader.zone_offsets_min."""


@dataclass(frozen=True, slots=True)
class LogRecord:
    """Uma linha do jsonlog com os campos usados pelo SACLA."""

    ts_ms: int
    user: str | None = None
    dbname: str | None = None
    pid: int | None = None
    remote_host: str | None = None
    session_id: str | None = None
    line_num: int | None = None
    severity: str | None = None
    state_code: str | None = None
    message: str = ""
    detail: str | None = None
    statement: str | None = None
    application_name: str | None = None
    backend_type: str | None = None
    query_id: int | None = None


@dataclass
class ReadReport:
    files: list[str] = field(default_factory=list)
    records: int = 0
    malformed: int = 0  # não é JSON, não é objeto ou não tem horário válido
    truncated: int = 0  # última linha incompleta ou arquivo .gz cortado
    unknown_zone: int = 0  # horário com abreviação de fuso desconhecida

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": len(self.files),
            "records": self.records,
            "malformed": self.malformed,
            "truncated": self.truncated,
            "unknown_zone": self.unknown_zone,
        }


def list_log_files(path: Path, patterns: tuple[str, ...]) -> list[Path]:
    """Um arquivo, ou os arquivos de um diretório que casam com os padrões, por nome."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(f"caminho de logs inexistente: {path}")
    files = {p for pattern in patterns for p in path.glob(pattern) if p.is_file()}
    return sorted(files, key=lambda p: p.name)


def read_records(
    files: list[Path],
    report: ReadReport | None = None,
    zone_offsets_min: dict[str, int] | None = None,
) -> Iterator[LogRecord]:
    report = report if report is not None else ReadReport()
    zones = zone_offsets_min if zone_offsets_min is not None else DEFAULT_ZONES
    for path in files:
        report.files.append(path.name)
        try:
            with _open(path) as fh:
                for raw_line in fh:
                    if not raw_line.strip():
                        continue
                    try:
                        record = _parse(raw_line, zones)
                    except UnknownZoneError:
                        report.unknown_zone += 1
                        continue
                    if record is None:
                        if not raw_line.endswith("\n"):
                            report.truncated += 1
                            log.warning("última linha incompleta ignorada em %s", path.name)
                        else:
                            report.malformed += 1
                        continue
                    report.records += 1
                    yield record
        except (EOFError, gzip.BadGzipFile) as exc:
            report.truncated += 1
            log.warning(
                "arquivo comprimido cortado em %s (%s); linhas anteriores mantidas", path.name, exc
            )
    if report.malformed:
        log.warning("%d linha(s) malformada(s) ignorada(s)", report.malformed)
    if report.unknown_zone:
        log.error(
            "%d linha(s) com fuso desconhecido no horário; inclua a abreviação em "
            "reader.zone_offsets_min ou use log_timezone = 'UTC'",
            report.unknown_zone,
        )


def parse_timestamp(value: str, zones: dict[str, int] | None = None) -> int:
    """'2026-03-02 11:00:00.123 UTC' (ou com deslocamento, ex.: '-03') -> ms UTC."""
    stamp, _, zone = value.rpartition(" ")
    known = zones if zones is not None else DEFAULT_ZONES
    if zone in known:
        tz = timezone(timedelta(minutes=known[zone]))
    else:
        match = _OFFSET.match(zone)
        if not match:
            raise UnknownZoneError(f"fuso desconhecido no timestamp: {value!r}")
        sign = -1 if match.group(1) == "-" else 1
        minutes = int(match.group(2)) * 60 + int(match.group(3) or 0)
        tz = timezone(sign * timedelta(minutes=minutes))
    moment = datetime.fromisoformat(stamp).replace(tzinfo=tz)
    return round(moment.timestamp() * 1000)


def _open(path: Path) -> IO[str]:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def _parse(line: str, zones: dict[str, int]) -> LogRecord | None:
    """Registro da linha, ou None se malformada; UnknownZoneError se o fuso for desconhecido."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "timestamp" not in data:
        return None
    try:
        ts_ms = parse_timestamp(str(data["timestamp"]), zones)
    except UnknownZoneError:
        raise
    except ValueError:
        return None
    return LogRecord(
        ts_ms=ts_ms,
        user=_str(data.get("user")),
        dbname=_str(data.get("dbname")),
        pid=_int(data.get("pid")),
        remote_host=_str(data.get("remote_host")),
        session_id=_str(data.get("session_id")),
        line_num=_int(data.get("line_num")),
        severity=_str(data.get("error_severity")),
        state_code=_str(data.get("state_code")),
        message=_str(data.get("message")) or "",
        detail=_str(data.get("detail")),
        statement=_str(data.get("statement")),
        application_name=_str(data.get("application_name")),
        backend_type=_str(data.get("backend_type")),
        query_id=_int(data.get("query_id")),
    )


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None

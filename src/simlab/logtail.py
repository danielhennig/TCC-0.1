"""Leitura incremental dos arquivos jsonlog durante as verificações da bancada.

Ferramenta de apoio da bancada; o leitor do SACLA (Fase 3) é independente deste módulo.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Cabeçalho exato das mensagens do auto_explain; "plan:" em qualquer outro ponto (por
# exemplo, dentro do texto de um comando) não identifica um plano.
AUTO_EXPLAIN_HEADER = re.compile(r"duration: \d+(?:\.\d+)? ms  plan:\n")


@dataclass(frozen=True)
class LogSnapshot:
    """Tamanho de cada arquivo de log em um instante; serve de ponto de partida da leitura."""

    offsets: dict[str, int]

    @classmethod
    def take(cls, log_dir: Path, pattern: str) -> LogSnapshot:
        return cls({p.name: p.stat().st_size for p in sorted(log_dir.glob(pattern))})


def total_size(log_dir: Path, pattern: str) -> int:
    return sum(p.stat().st_size for p in log_dir.glob(pattern))


def wait_until_quiet(log_dir: Path, pattern: str, timeout_s: float, settle_s: float = 0.5) -> None:
    """Espera os arquivos de log pararem de crescer por `settle_s` segundos.

    O coletor de logs grava de forma assíncrona; sem esta espera as últimas linhas
    poderiam ficar de fora da leitura.
    """
    deadline = time.monotonic() + timeout_s
    last = total_size(log_dir, pattern)
    stable_since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.1)
        current = total_size(log_dir, pattern)
        if current != last:
            last, stable_since = current, time.monotonic()
        elif time.monotonic() - stable_since >= settle_s:
            return
    log.warning("os logs ainda cresciam após %.1f s; a leitura pode estar incompleta", timeout_s)


def read_new_records(log_dir: Path, pattern: str, since: LogSnapshot) -> list[dict[str, Any]]:
    """Lê as linhas JSON gravadas depois de `since`, em ordem de arquivo (rotação)."""
    records: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob(pattern)):
        with path.open("rb") as fh:
            fh.seek(since.offsets.get(path.name, 0))
            data = fh.read()
        for number, raw in enumerate(data.split(b"\n"), start=1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("linha malformada ignorada (%s, linha nova %d)", path.name, number)
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def parse_auto_explain(message: str) -> dict[str, Any] | None:
    """Extrai o plano JSON de uma mensagem do auto_explain ("duration: X ms  plan:\\n{...}")."""
    header = AUTO_EXPLAIN_HEADER.match(message)
    if header is None:
        return None
    try:
        plan = json.loads(message[header.end() :])
    except json.JSONDecodeError:
        return None
    return plan if isinstance(plan, dict) else None

"""Coleta dos logs gerados durante a execução (só o trecho novo, comprimido com gzip)."""

from __future__ import annotations

import gzip
import logging
from pathlib import Path

from simlab.lab import LabConfig
from simlab.logtail import LogSnapshot, wait_until_quiet

log = logging.getLogger(__name__)

CHUNK = 8 * 1024 * 1024


def collect_logs(lab: LabConfig, since: LogSnapshot, raw_dir: Path) -> dict[str, int]:
    """Copia para `raw_dir/<arquivo>.gz` os bytes gravados em cada arquivo após `since`."""
    wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
    raw_dir.mkdir(parents=True, exist_ok=True)
    copied: dict[str, int] = {}
    for path in sorted(lab.log_dir.glob(lab.log_glob)):
        offset = since.offsets.get(path.name, 0)
        if path.stat().st_size <= offset:
            continue
        total = 0
        with path.open("rb") as src, gzip.open(raw_dir / f"{path.name}.gz", "wb") as dst:
            src.seek(offset)
            while chunk := src.read(CHUNK):
                dst.write(chunk)
                total += len(chunk)
        copied[path.name] = total
    log.info("logs coletados: %d arquivo(s), %.1f MB", len(copied), sum(copied.values()) / 1e6)
    return copied

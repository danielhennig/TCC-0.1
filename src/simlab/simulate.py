"""Pipeline completo da simulação: agenda → execução → coleta dos logs → retime.

Espera um banco recém-criado (`python tasks.py simulate` recria banco e logs antes), para que
a execução seja determinística dado a semente.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

import pandas as pd

from sacla.config import PROJECT_ROOT, ConfigError
from simlab.anomalies import load_anomalies
from simlab.behavior import load_behaviors
from simlab.catalog import load_catalog
from simlab.collect import collect_logs
from simlab.executor import execute_schedule, execution_summary
from simlab.ids import fetch_id_pools, pools_digest
from simlab.lab import LabConfig
from simlab.logtail import LogSnapshot, wait_until_quiet
from simlab.profiles import Profiles
from simlab.retime import retime_run
from simlab.schedule import SimulationSettings, build_schedule, write_schedule
from simlab.verify import verify_manifest

log = logging.getLogger(__name__)

RUN_MARKER = "run.json"
# Arquivos pequenos copiados para results/ (versionados): ground truth, definição da execução
# e relatórios. Os logs e a agenda completa ficam só em data/ (reproduzíveis pela semente).
VERSIONED_FILES = (
    "manifest.csv",
    "run.json",
    "schedule_summary.json",
    "retime_report.json",
    "simulation_report.json",
)


def run_simulation(lab: LabConfig, profiles: Profiles, settings: SimulationSettings) -> Path:
    run_dir = settings.run_dir
    _prepare(run_dir)
    timings: dict[str, float] = {}

    started = time.monotonic()
    catalog = load_catalog()
    behaviors = load_behaviors(profiles, catalog)
    anomalies = load_anomalies(profiles, behaviors, catalog)
    ids = fetch_id_pools(lab, catalog.id_pools)
    schedule = build_schedule(settings, profiles, behaviors, catalog, anomalies, ids)
    summary = write_schedule(schedule, settings, run_dir, pools_digest(ids))
    timings["agenda_s"] = time.monotonic() - started
    log.info("agenda gravada em %s: %s", run_dir, json.dumps(summary["events_by_kind"]))

    started = time.monotonic()
    wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
    snapshot = LogSnapshot.take(lab.log_dir, lab.log_glob)
    events = pd.read_parquet(run_dir / "events.parquet")
    executed = execute_schedule(lab, events, settings)
    executed.to_parquet(run_dir / "exec.parquet", index=False)
    exec_summary = execution_summary(events, executed, settings.expected_sqlstates)
    timings["execucao_s"] = time.monotonic() - started

    started = time.monotonic()
    copied = collect_logs(lab, snapshot, run_dir / "raw")
    timings["coleta_s"] = time.monotonic() - started

    started = time.monotonic()
    retime = retime_run(run_dir, settings.retime_reorder_window)
    timings["retime_s"] = time.monotonic() - started
    manifest_check = verify_manifest(run_dir)

    report = {
        "run_id": settings.run_id,
        "schedule": summary,
        "execution": exec_summary,
        "raw_log_bytes": sum(copied.values()),
        "retimed_bytes": sum(f.stat().st_size for f in (run_dir / "retimed").glob("*.json.gz")),
        "raw_gz_bytes": sum(f.stat().st_size for f in (run_dir / "raw").glob("*.json.gz")),
        "retime": {
            k: retime[k]
            for k in (
                "lines",
                "client_lines",
                "sessions_mapped",
                "unmapped_client_lines",
                "order_violations",
                "passed",
            )
        },
        "manifest_check": manifest_check,
        "timings_s": {k: round(v, 1) for k, v in timings.items()},
    }
    (run_dir / "simulation_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _print_report(report, retime)
    results = settings.results_run_dir
    results.mkdir(parents=True, exist_ok=True)
    for name in VERSIONED_FILES:
        shutil.copyfile(run_dir / name, results / name)
    log.info("evidências copiadas para %s", results.relative_to(PROJECT_ROOT))
    if not manifest_check["passed"]:
        raise ConfigError(f"manifesto incoerente com o log: {manifest_check['examples']}")
    if exec_summary["unexpected_errors"] or exec_summary["unexpected_logins"]:
        raise ConfigError(
            "a execução teve erros inesperados: "
            f"{exec_summary['unexpected_errors']}, logins inesperados: "
            f"{exec_summary['unexpected_logins']}"
        )
    return run_dir


def _prepare(run_dir: Path) -> None:
    if run_dir.exists():
        if not (run_dir / RUN_MARKER).exists() and any(run_dir.iterdir()):
            raise ConfigError(f"{run_dir} existe e não parece uma execução da simulação")
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)


def _print_report(report: dict[str, Any], retime: dict[str, Any]) -> None:
    print("\n=== Simulação", report["run_id"])
    print(
        json.dumps(
            {k: report[k] for k in ("schedule", "execution", "timings_s")},
            indent=2,
            ensure_ascii=False,
        )
    )
    print(
        f"log bruto: {report['raw_log_bytes'] / 1e6:.1f} MB "
        f"(gzip: {report['raw_gz_bytes'] / 1e6:.1f} MB); "
        f"remapeado (gzip): {report['retimed_bytes'] / 1e6:.1f} MB"
    )
    print("\n=== Verificação do retime")
    print(
        json.dumps(
            {k: v for k, v in retime.items() if k != "output_files"}, indent=2, ensure_ascii=False
        )
    )
    print("\n=== Manifesto x log remapeado")
    print(json.dumps(report["manifest_check"], indent=2, ensure_ascii=False))

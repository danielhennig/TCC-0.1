"""Conjunto de dados de uma execução: janelas com atributos (SACLA), split e rótulos.

Fluxo: retimed/*.json.gz → sacla.reader → sacla.events → sacla.features (histórico = treino)
→ split (run.json) → rótulos (manifest.csv) → data/features/<run_id>/w<Δ>m.parquet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from evaluation.crosscheck import crosscheck
from evaluation.labels import assign_split, label_windows
from sacla.config import ConfigError, SaclaSettings
from sacla.events import ParseReport, extract_events
from sacla.features import FEATURE_COLUMNS, build_features
from sacla.reader import ReadReport, list_log_files, read_records

log = logging.getLogger(__name__)


def run_fingerprint(run_dir: Path) -> str:
    """Impressão digital da execução (definição + ground truth): muda se a simulação for refeita."""
    digest = hashlib.sha256()
    for name in ("run.json", "manifest.csv"):
        digest.update((run_dir / name).read_bytes())
    return digest.hexdigest()


def dataset_name(window_minutes: int) -> str:
    return f"w{window_minutes}m"


def build_dataset(
    run_dir: Path, out_dir: Path, settings: SaclaSettings, window_minutes: int | None = None
) -> dict[str, Any]:
    if window_minutes is not None:
        settings = replace(
            settings, features=replace(settings.features, window_minutes=window_minutes)
        )
    window = settings.features.window_minutes
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if run["timezone"] != settings.features.timezone:
        raise ConfigError(
            f"fuso da simulação ({run['timezone']}) difere de features.timezone "
            f"({settings.features.timezone})"
        )
    _check_window_fits_splits(window, run["splits"])
    timings: dict[str, float] = {}

    started = time.monotonic()
    files = list_log_files(run_dir / "retimed", settings.reader.patterns)
    read_report, parse_report = ReadReport(), ParseReport()
    records = read_records(files, read_report, settings.reader.zone_offsets_min)
    events = extract_events(records, settings.events, parse_report)
    timings["leitura_e_eventos_s"] = time.monotonic() - started

    started = time.monotonic()
    history_end = int(run["splits"]["train"]["end_ms"])
    features, baselines = build_features(events, settings, history_end)
    timings["agregacao_s"] = time.monotonic() - started

    features["split"] = assign_split(features, run["splits"])
    manifest = pd.read_csv(run_dir / "manifest.csv")
    dataset = label_windows(features, manifest)

    check = crosscheck(events, run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = dataset_name(window)
    summary = {
        "run_id": run["run_id"],
        "run_fingerprint": run_fingerprint(run_dir),
        "window_minutes": window,
        "history_end_utc": run["splits"]["train"]["end_utc"],
        "features": list(FEATURE_COLUMNS),
        "read": read_report.as_dict(),
        "parse": parse_report.as_dict(),
        "events": len(events),
        "events_by_kind": {str(k): int(v) for k, v in events["kind"].value_counts().items()},
        "users_with_history": len(baselines),
        "windows_by_split": _by_split(dataset),
        "anomalous_windows_by_type": _by_type(dataset),
        "crosscheck": {k: v for k, v in check.items() if k != "table"},
        "timings_s": {k: round(v, 2) for k, v in timings.items()},
    }
    (out_dir / f"{name}_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(check["table"].to_string())
    # Só grava o conjunto se a conferência passar; o resumo fica para diagnóstico.
    for stale in (out_dir / f"{name}.parquet", out_dir / "events.parquet"):
        stale.unlink(missing_ok=True)
    if not check["passed"]:
        raise ConfigError(f"conferência com o executor falhou: {check['differences'][:10]}")
    dataset.to_parquet(out_dir / f"{name}.parquet", index=False)
    events.to_parquet(out_dir / "events.parquet", index=False)
    return summary


def _check_window_fits_splits(window_minutes: int, splits: dict[str, dict[str, int]]) -> None:
    """Toda janela precisa cair inteira num split (e o histórico, inteiro no treino)."""
    window_ms = window_minutes * 60_000
    for name, bounds in splits.items():
        if bounds["start_ms"] % window_ms or bounds["end_ms"] % window_ms:
            raise ConfigError(
                f"janela de {window_minutes} min não divide os limites do split {name}; "
                "escolha um tamanho que divida o dia (ex.: 1, 5, 10, 15, 30, 60)"
            )


def _by_split(dataset: pd.DataFrame) -> dict[str, dict[str, Any]]:
    result = {}
    for split, frame in dataset.groupby("split", sort=False):
        anomalous = int(frame["label"].sum())
        result[str(split)] = {
            "windows": len(frame),
            "anomalous": anomalous,
            "contamination_pct": round(100 * anomalous / max(len(frame), 1), 2),
        }
    return result


def _by_type(dataset: pd.DataFrame) -> dict[str, dict[str, int]]:
    anomalous = dataset[dataset["label"] == 1]
    table = anomalous.groupby(["anomaly_type", "split"]).size().unstack(fill_value=0)
    return {str(t): {str(s): int(n) for s, n in row.items()} for t, row in table.iterrows()}

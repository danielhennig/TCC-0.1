"""Leitura dos arquivos YAML de configuração e configuração do logging."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(Exception):
    """Configuração ausente, malformada ou inconsistente."""


def load_yaml(path: Path) -> dict[str, Any]:
    """Lê um arquivo YAML cujo nível superior é um mapeamento."""
    try:
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"arquivo de configuração não encontrado: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} deve conter um mapeamento no nível superior")
    return data


@dataclass(frozen=True)
class ReaderSettings:
    patterns: tuple[str, ...]
    zone_offsets_min: dict[str, int]  # abreviação de fuso do timestamp -> minutos em relação a UTC


@dataclass(frozen=True)
class EventSettings:
    client_backend_types: frozenset[str]
    ignore_users: frozenset[str]
    partition_aliases: tuple[tuple[re.Pattern[str], str], ...]
    auth_failure_states: frozenset[str]
    permission_denied_states: frozenset[str]


@dataclass(frozen=True)
class FeatureSettings:
    window_minutes: int
    timezone: str
    weekend_days: frozenset[int]
    hour_smoothing: float
    robust_scale_floor: float
    training_history: str  # in_sample | leave_one_day_out (docs/decisoes.md, D45)

    def __post_init__(self) -> None:
        # Também roda em dataclasses.replace (ex.: --window na linha de comando).
        if self.window_minutes <= 0:
            raise ConfigError("features.window_minutes deve ser positivo")
        if self.training_history not in ("in_sample", "leave_one_day_out"):
            raise ConfigError("features.training_history deve ser in_sample ou leave_one_day_out")
        if self.hour_smoothing <= 0 or self.robust_scale_floor <= 0:
            raise ConfigError("features.hour_smoothing e robust_scale_floor devem ser positivos")


@dataclass(frozen=True)
class DetectorSettings:
    """Isolation Forest do uso real (`sacla fit`); a avaliação escolhe os seus em model.yaml."""

    log1p_features: tuple[str, ...]
    n_estimators: int
    max_samples: int | float | str
    max_features: float
    log1p: bool
    seed: int
    n_jobs: int
    train_alert_rate: float

    def __post_init__(self) -> None:
        if not 0 < self.train_alert_rate < 1:
            raise ConfigError("detector.train_alert_rate deve estar entre 0 e 1 (exclusive)")


@dataclass(frozen=True)
class ReportSettings:
    explain_features: tuple[str, ...]
    top_features: int
    max_tail_fraction: float
    episode_gap_windows: int
    max_html_episodes: int

    def __post_init__(self) -> None:
        if self.top_features <= 0 or self.max_html_episodes <= 0:
            raise ConfigError("report.top_features e max_html_episodes devem ser positivos")
        if not 0 < self.max_tail_fraction <= 1 or self.episode_gap_windows < 0:
            raise ConfigError("report.max_tail_fraction em (0, 1] e episode_gap_windows >= 0")


@dataclass(frozen=True)
class SaclaSettings:
    reader: ReaderSettings
    events: EventSettings
    features: FeatureSettings
    detector: DetectorSettings
    report: ReportSettings


def load_settings(path: Path | None = None) -> SaclaSettings:
    """Lê config/sacla.yaml."""
    raw = load_yaml(path or CONFIG_DIR / "sacla.yaml")
    try:
        reader, events, features = raw["reader"], raw["events"], raw["features"]
        detector, report = raw["detector"], raw["report"]
        settings = SaclaSettings(
            reader=ReaderSettings(
                patterns=tuple(str(p) for p in reader["patterns"]),
                zone_offsets_min={
                    str(k): int(v) for k, v in (reader.get("zone_offsets_min") or {}).items()
                },
            ),
            events=EventSettings(
                client_backend_types=frozenset(str(t) for t in events["client_backend_types"]),
                ignore_users=frozenset(str(u) for u in events.get("ignore_users") or []),
                partition_aliases=tuple(
                    (re.compile(str(a["pattern"])), str(a["table"]))
                    for a in events.get("partition_aliases") or []
                ),
                auth_failure_states=frozenset(str(s) for s in events["auth_failure_states"]),
                permission_denied_states=frozenset(
                    str(s) for s in events["permission_denied_states"]
                ),
            ),
            features=FeatureSettings(
                window_minutes=int(features["window_minutes"]),
                timezone=str(features["timezone"]),
                weekend_days=frozenset(int(d) for d in features["weekend_days"]),
                hour_smoothing=float(features["hour_smoothing"]),
                robust_scale_floor=float(features["robust_scale_floor"]),
                training_history=str(features["training_history"]),
            ),
            detector=DetectorSettings(
                log1p_features=tuple(str(f) for f in detector["log1p_features"]),
                n_estimators=int(detector["n_estimators"]),
                max_samples=detector["max_samples"],
                max_features=float(detector["max_features"]),
                log1p=bool(detector["log1p"]),
                seed=int(detector["seed"]),
                n_jobs=int(detector["n_jobs"]),
                train_alert_rate=float(detector["train_alert_rate"]),
            ),
            report=ReportSettings(
                explain_features=tuple(str(f) for f in report["explain_features"]),
                top_features=int(report["top_features"]),
                max_tail_fraction=float(report["max_tail_fraction"]),
                episode_gap_windows=int(report["episode_gap_windows"]),
                max_html_episodes=int(report["max_html_episodes"]),
            ),
        )
    except (KeyError, TypeError, ValueError, AttributeError, re.error) as exc:
        raise ConfigError(f"sacla.yaml malformado: {exc!r}") from exc
    return settings


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

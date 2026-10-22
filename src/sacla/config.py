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

    def __post_init__(self) -> None:
        # Também roda em dataclasses.replace (ex.: --window na linha de comando).
        if self.window_minutes <= 0:
            raise ConfigError("features.window_minutes deve ser positivo")
        if self.hour_smoothing <= 0 or self.robust_scale_floor <= 0:
            raise ConfigError("features.hour_smoothing e robust_scale_floor devem ser positivos")


@dataclass(frozen=True)
class SaclaSettings:
    reader: ReaderSettings
    events: EventSettings
    features: FeatureSettings


def load_settings(path: Path | None = None) -> SaclaSettings:
    """Lê config/sacla.yaml."""
    raw = load_yaml(path or CONFIG_DIR / "sacla.yaml")
    try:
        reader, events, features = raw["reader"], raw["events"], raw["features"]
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

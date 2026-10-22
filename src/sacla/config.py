"""Leitura dos arquivos YAML de configuração e configuração do logging."""

from __future__ import annotations

import logging
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


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

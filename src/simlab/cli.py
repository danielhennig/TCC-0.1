"""Linha de comando da bancada experimental: `python -m simlab.cli <comando>`."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import psycopg

from sacla.config import ConfigError, setup_logging
from simlab.bench import BenchSettings, run_bench
from simlab.lab import load_experiment, load_lab
from simlab.profiles import load_profiles
from simlab.provision import ProvisionError, provision
from simlab.smoke import SmokeSettings, run_smoke

log = logging.getLogger("simlab")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simlab", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_provision = sub.add_parser("provision", help="valida o Pagila e cria papéis e usuários")
    p_provision.add_argument(
        "--skip-count-check",
        action="store_true",
        help="não confere as contagens do Pagila (banco já usado)",
    )
    sub.add_parser("smoke", help="gera eventos conhecidos e confere o jsonlog")
    sub.add_parser("bench", help="mede o overhead do registro de comandos e do auto_explain")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        experiment = load_experiment()
        lab = load_lab(experiment)
        if args.command == "provision":
            provision(lab, load_profiles(), check_counts=not args.skip_count_check)
            return 0
        if args.command == "smoke":
            smoke = SmokeSettings.from_mapping(_section(experiment, "smoke"))
            return 0 if run_smoke(lab, load_profiles(), smoke) else 1
        if args.command == "bench":
            run_bench(lab, BenchSettings.from_mapping(_section(experiment, "bench")))
            return 0
    except (ConfigError, ProvisionError) as exc:
        log.error("%s", exc)
        return 2
    except psycopg.Error as exc:
        log.error("erro do PostgreSQL: %s", exc)
        return 3
    parser.error(f"comando desconhecido: {args.command}")
    return 2


def _section(experiment: dict[str, Any], name: str) -> dict[str, Any]:
    section = experiment.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"seção {name} ausente em experiment.yaml")
    return section


if __name__ == "__main__":
    sys.exit(main())

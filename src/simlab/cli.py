"""Linha de comando da bancada experimental: `python -m simlab.cli <comando>`."""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import psycopg

from sacla.config import ConfigError, setup_logging
from simlab.anomalies import PlacementError
from simlab.bench import BenchSettings, run_bench
from simlab.executor import ExecutionError
from simlab.lab import load_experiment, load_lab
from simlab.profiles import load_profiles
from simlab.provision import ProvisionError, provision
from simlab.retime import RetimeError, retime_run
from simlab.schedule import SimulationSettings
from simlab.simulate import run_simulation
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
    for name, help_text in (
        ("simulate", "gera a agenda, executa, coleta os logs e remapeia os horários"),
        ("retime", "refaz só o remapeamento de horários de uma execução existente"),
    ):
        p_sim = sub.add_parser(name, help=help_text)
        p_sim.add_argument("--seed", type=int, help="semente (padrão: experiment.yaml)")
        p_sim.add_argument("--days", type=int, help="dias simulados (padrão: experiment.yaml)")
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
        if args.command in ("simulate", "retime"):
            settings = SimulationSettings.from_experiment(experiment, args.seed, args.days)
            if args.command == "simulate":
                run_simulation(lab, load_profiles(), settings)
            else:
                report = retime_run(settings.run_dir, settings.retime_reorder_window)
                log.info("retime concluído: %d linhas", report["lines"])
            return 0
    except (ConfigError, ProvisionError, PlacementError, RetimeError) as exc:
        log.error("%s", exc)
        return 2
    except ExecutionError as exc:
        log.error("execução interrompida: %s", exc)
        return 4
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

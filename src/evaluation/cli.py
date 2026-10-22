"""Linha de comando da avaliação: `python -m evaluation.cli <comando>`."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys

from evaluation.dataset import build_dataset, dataset_name
from sacla.config import (
    CONFIG_DIR,
    PROJECT_ROOT,
    ConfigError,
    load_settings,
    load_yaml,
    setup_logging,
)

log = logging.getLogger("evaluation")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evaluation", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p_features = sub.add_parser(
        "features", help="lê o log remapeado de uma execução e monta o conjunto rotulado"
    )
    p_features.add_argument("--run-id", help="execução em data/runs (padrão: experiment.yaml)")
    p_features.add_argument("--window", type=int, help="minutos por janela (padrão: sacla.yaml)")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        experiment = load_yaml(CONFIG_DIR / "experiment.yaml")
        sim, evaluation = experiment["simulation"], experiment["evaluation"]
        run_id = args.run_id or str(sim["run_id"]).format(seed=sim["seed"], days=sim["days"])
        run_dir = PROJECT_ROOT / sim["data_dir"] / run_id
        if not (run_dir / "run.json").exists():
            raise ConfigError(f"execução não encontrada: {run_dir} (rode `tasks.py simulate`)")
        if args.command == "features":
            out_dir = PROJECT_ROOT / evaluation["features_dir"] / run_id
            summary = build_dataset(run_dir, out_dir, load_settings(), args.window)
            name = dataset_name(summary["window_minutes"])
            versioned = PROJECT_ROOT / evaluation["results_dir"] / run_id
            versioned.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_dir / f"{name}_summary.json", versioned / f"{name}_summary.json")
            print(
                json.dumps(
                    {k: v for k, v in summary.items() if k != "features"},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
    except (ConfigError, KeyError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2
    parser.error(f"comando desconhecido: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

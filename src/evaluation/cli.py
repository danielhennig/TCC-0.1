"""Linha de comando da avaliação: `python -m evaluation.cli <comando>`."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from evaluation.ablation import run_ablation
from evaluation.confirmatory import run_confirmatory
from evaluation.dataset import build_dataset, dataset_name
from evaluation.experiment import run_experiment
from evaluation.plots import make_figures
from evaluation.report import experiment_report
from evaluation.robustness import run_robustness
from evaluation.tuning import load_model_config
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
    for name, help_text in (
        ("features", "lê o log remapeado de uma execução e monta o conjunto rotulado"),
        ("evaluate", "ajuste na validação, teste único, regras e sensibilidade"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--run-id", help="execução em data/runs (padrão: experiment.yaml)")
        p.add_argument("--window", type=int, help="minutos por janela (padrão: sacla.yaml)")
    sub.add_parser("robustness-plan", help="mostra as sementes de simulação da robustez (JSON)")
    sub.add_parser("confirm-plan", help="mostra as sementes das simulações confirmatórias (JSON)")
    p_conf = sub.add_parser("confirm", help="validação confirmatória pré-registrada (D52)")
    p_conf.add_argument("--exp-id", required=True, help="pasta em results/experimentos")
    p_abl = sub.add_parser("ablation", help="ablação de atributos na validação (D52)")
    p_abl.add_argument("--exp-id", required=True, help="pasta em results/experimentos")
    p_rob = sub.add_parser("robustness", help="reavalia um experimento em outras simulações")
    p_rob.add_argument("--exp-id", required=True, help="pasta em results/experimentos")
    p_rob.add_argument("--run-ids", nargs="+", required=True, help="execuções adicionais")
    p_rep = sub.add_parser("report", help="relatório de anomalias do teste e figuras")
    p_rep.add_argument("--exp-id", help="pasta em results/experimentos (padrão: a única)")
    args = parser.parse_args(argv)
    setup_logging()

    try:
        experiment = load_yaml(CONFIG_DIR / "experiment.yaml")
        sim, evaluation = experiment["simulation"], experiment["evaluation"]
        data_root = PROJECT_ROOT / sim["data_dir"]
        features_root = PROJECT_ROOT / evaluation["features_dir"]
        if args.command in ("robustness-plan", "confirm-plan"):
            section = "robustness" if args.command == "robustness-plan" else "confirmatory"
            seeds = load_model_config().raw[section]["simulation_seeds"]
            run_ids = [_run_id(sim, seed) for seed in seeds]
            plan = {
                "seeds": seeds,
                "run_ids": run_ids,
                # Caminhos relativos à raiz do projeto, para o tasks.py verificar no host.
                "run_dirs": [f"{sim['data_dir']}/{run_id}" for run_id in run_ids],
            }
            print(json.dumps(plan))
            return 0
        if args.command in ("confirm", "ablation"):
            settings = load_settings()
            model = load_model_config(settings=settings)
            exp_dir = _experiment_dir(PROJECT_ROOT / model.results_dir, args.exp_id)
            development = [_run_id(sim, s) for s in model.raw["robustness"]["simulation_seeds"]]
            confirmatory = [_run_id(sim, s) for s in model.raw["confirmatory"]["simulation_seeds"]]
            missing = [
                r for r in development + confirmatory if not (data_root / r / "run.json").exists()
            ]
            if missing:
                raise ConfigError(
                    f"simulações ausentes em {data_root}: {missing} "
                    "(rode `tasks.py robustness` e `tasks.py confirm`)"
                )
            if args.command == "confirm":
                out = run_confirmatory(
                    exp_dir, development, confirmatory, data_root, features_root, settings
                )
            else:
                reference = json.loads((exp_dir / "metrics.json").read_text(encoding="utf-8"))
                runs = [reference["run_id"], *development, *confirmatory]
                out = run_ablation(exp_dir, runs, data_root, features_root, settings)
            print(out.read_text(encoding="utf-8"))
            return 0
        if args.command == "report":
            settings = load_settings()
            model = load_model_config(settings=settings)
            exp_dir = _experiment_dir(PROJECT_ROOT / model.results_dir, args.exp_id)
            summary = experiment_report(exp_dir, data_root, features_root, settings)
            robustness_dir = PROJECT_ROOT / model.robustness_dir / exp_dir.name
            figures = make_figures(exp_dir, robustness_dir, model)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            print(f"{len(figures)} arquivos de figura em {exp_dir / 'figuras'}")
            return 0
        if args.command == "robustness":
            out = run_robustness(
                PROJECT_ROOT / load_model_config().results_dir / args.exp_id,
                args.run_ids,
                data_root,
                features_root,
                load_settings(),
            )
            print(out.read_text(encoding="utf-8"))
            return 0

        run_id = args.run_id or _run_id(sim, sim["seed"])
        run_dir = data_root / run_id
        if not (run_dir / "run.json").exists():
            raise ConfigError(f"execução não encontrada: {run_dir} (rode `tasks.py simulate`)")
        if args.command == "features":
            out_dir = features_root / run_id
            summary = build_dataset(run_dir, out_dir, load_settings(), args.window)
            name = dataset_name(summary["window_minutes"])
            versioned = PROJECT_ROOT / evaluation["results_dir"] / run_id
            versioned.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_dir / f"{name}_summary.json", versioned / f"{name}_summary.json")
            shown = {k: v for k, v in summary.items() if k != "features"}
            print(json.dumps(shown, indent=2, ensure_ascii=False))
            return 0
        if args.command == "evaluate":
            out = run_experiment(run_dir, features_root / run_id, load_settings(), args.window)
            print((out / "resumo.md").read_text(encoding="utf-8"))
            return 0
    except (ConfigError, KeyError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 2
    parser.error(f"comando desconhecido: {args.command}")
    return 2


def _experiment_dir(results: Path, exp_id: str | None) -> Path:
    if exp_id:
        path = results / exp_id
        if not path.is_dir():
            raise ConfigError(f"experimento não encontrado: {path}")
        return path
    found = sorted(p for p in results.iterdir() if (p / "metrics.json").exists())
    if len(found) != 1:
        names = [p.name for p in found]
        raise ConfigError(f"informe --exp-id; experimentos em {results}: {names}")
    return found[0]


def _run_id(sim: dict[str, Any], seed: Any) -> str:
    return str(sim["run_id"]).format(seed=seed, days=sim["days"])


if __name__ == "__main__":
    sys.exit(main())

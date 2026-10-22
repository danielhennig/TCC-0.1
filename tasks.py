#!/usr/bin/env python3
"""Executor de tarefas do SACLA (substitui o make; usa só a biblioteca padrão).

Uso: python tasks.py <comando> [argumentos extras]
Tudo roda nos containers definidos em docker-compose.yml.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPOSE = ["docker", "compose", "--project-directory", str(ROOT)]
# -T: sem pseudo-terminal, para funcionar também fora de um terminal interativo.
RUN = [*COMPOSE, "run", "--rm", "-T", "sacla"]
RUN_TTY = [*COMPOSE, "run", "--rm", "sacla"]


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd), flush=True)
    code = subprocess.run(cmd, cwd=ROOT).returncode
    if code != 0:
        sys.exit(code)


def ensure_env() -> None:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists():
        shutil.copyfile(example, env)
        print("criado .env a partir de .env.example (senhas do laboratório local)")


def setup(extra: list[str]) -> None:
    """Constrói as imagens e recria o banco do zero (apaga os volumes pgdata e pglogs)."""
    run([*COMPOSE, "--profile", "tools", "build"])
    reset(extra)


def reset(extra: list[str]) -> None:
    """Recria o banco e os logs do zero, sem reconstruir as imagens."""
    run([*COMPOSE, "--profile", "tools", "down", "--volumes", "--remove-orphans"])
    run([*COMPOSE, "up", "--detach", "--wait", "db"])
    run([*RUN, "python", "-m", "simlab.cli", "provision", *extra])


def up(extra: list[str]) -> None:
    """Sobe o banco (mantém os dados existentes)."""
    run([*COMPOSE, "up", "--detach", "--wait", "db", *extra])


def down(extra: list[str]) -> None:
    """Para os containers (mantém os volumes)."""
    run([*COMPOSE, "--profile", "tools", "down", *extra])


def smoke(extra: list[str]) -> None:
    """Gera eventos conhecidos e mostra/confere as linhas reais do jsonlog."""
    run([*RUN, "python", "-m", "simlab.cli", "smoke", *extra])


def bench(extra: list[str]) -> None:
    """Mede o overhead do registro de comandos e do auto_explain."""
    run([*RUN, "python", "-m", "simlab.cli", "bench", *extra])


def simulate(extra: list[str]) -> None:
    """Recria banco e logs e roda a simulação completa (ex.: --days 2 --seed 7)."""
    reset([])
    run([*RUN, "python", "-m", "simlab.cli", "simulate", *extra])


def features(extra: list[str]) -> None:
    """Monta o conjunto rotulado de uma execução (ex.: --window 5 --run-id sim-s42-28d)."""
    run([*RUN, "python", "-m", "evaluation.cli", "features", *extra])


def retime(extra: list[str]) -> None:
    """Refaz só o remapeamento de horários de uma execução (mesmos --seed/--days)."""
    run([*RUN, "python", "-m", "simlab.cli", "retime", *extra])


def test(extra: list[str]) -> None:
    """Roda a suíte pytest no container."""
    run([*RUN, "pytest", *extra])


def lint(extra: list[str]) -> None:
    """ruff (lint + formatação) e mypy."""
    run([*RUN, "ruff", "check", "src", "tests", "tasks.py"])
    run([*RUN, "ruff", "format", "--check", "src", "tests", "tasks.py"])
    run([*RUN, "mypy", *extra])


def fmt(extra: list[str]) -> None:
    """Formata o código com ruff."""
    run([*RUN, "ruff", "format", "src", "tests", "tasks.py"])
    run([*RUN, "ruff", "check", "--fix", "src", "tests", "tasks.py"])


def lock(extra: list[str]) -> None:
    """Gera requirements.lock (versões exatas de todas as dependências do container)."""
    run([*RUN, "sh", "-c", "pip freeze --all > requirements.lock"])


def psql(extra: list[str]) -> None:
    """Abre o psql como superusuário (as linhas dessa sessão também vão para o log)."""
    run([*COMPOSE, "exec", "db", "psql", "-U", "postgres", "-d", "pagila", *extra])


def shell(extra: list[str]) -> None:
    """Abre um shell no container Python."""
    run([*RUN_TTY, "bash", *extra])


TASKS: dict[str, Callable[[list[str]], None]] = {
    "setup": setup,
    "reset": reset,
    "up": up,
    "down": down,
    "smoke": smoke,
    "bench": bench,
    "simulate": simulate,
    "retime": retime,
    "features": features,
    "test": test,
    "lint": lint,
    "fmt": fmt,
    "lock": lock,
    "psql": psql,
    "shell": shell,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(f"  {name:<6} {fn.__doc__}" for name, fn in TASKS.items()),
    )
    parser.add_argument("task", choices=TASKS)
    args, extra = parser.parse_known_args()
    # O docker-compose.yml exige o .env (senhas e env_file) para qualquer comando.
    ensure_env()
    TASKS[args.task](extra)


if __name__ == "__main__":
    main()

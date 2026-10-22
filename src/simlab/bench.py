"""Overhead do registro de comandos e do auto_explain (tabela para a monografia).

Cada condição de log é aplicada no servidor (ALTER SYSTEM + pg_reload_conf) e mede-se,
no cliente, a latência de cada comando de uma carga fixa e representativa, além dos bytes
de log gerados por comando. A carga de cada repetição é a mesma para todas as condições,
que rodam em ordem rotativa para diluir efeitos de cache e de ordem.
"""

from __future__ import annotations

import csv
import logging
import random
import re
import statistics
import string
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import sql

from sacla.config import PROJECT_ROOT, ConfigError
from simlab.lab import LabConfig, admin_connection, connect, server_version
from simlab.logtail import total_size, wait_until_quiet

log = logging.getLogger(__name__)

SETTING_NAME = re.compile(r"^[a-z_]+(\.[a-z_]+)?$")

Statement = tuple[str, tuple[Any, ...]]
Workload = tuple[tuple[str, Callable[[random.Random], tuple[Any, ...]]], ...]


def _workload(counts: dict[str, int]) -> Workload:
    """Carga representativa dos perfis, com parâmetros sorteados com semente fixa.

    Busca por chave, busca textual, junções do histórico de um cliente, agregação e uma
    leitura média. Os identificadores são sorteados entre 1 e o tamanho da tabela no Pagila.
    """
    customers, rentals = counts["customer"], counts["rental"]
    return (
        (
            "SELECT customer_id, first_name, last_name, email FROM customer WHERE customer_id = %s",
            lambda r: (r.randint(1, customers),),
        ),
        (
            "SELECT film_id, title, rental_rate FROM film WHERE title ILIKE %s "
            "ORDER BY title LIMIT 20",
            lambda r: (r.choice(string.ascii_uppercase) + "%",),
        ),
        (
            "SELECT r.rental_id, r.rental_date, f.title FROM rental r "
            "JOIN inventory i ON i.inventory_id = r.inventory_id "
            "JOIN film f ON f.film_id = i.film_id "
            "WHERE r.customer_id = %s ORDER BY r.rental_date DESC LIMIT 50",
            lambda r: (r.randint(1, customers),),
        ),
        (
            "SELECT c.name, count(*) FROM film_category fc "
            "JOIN category c ON c.category_id = fc.category_id GROUP BY c.name",
            lambda r: (),
        ),
        (
            "SELECT payment_id, amount, payment_date FROM payment WHERE customer_id = %s",
            lambda r: (r.randint(1, customers),),
        ),
        (
            "SELECT rental_id, rental_date, customer_id FROM rental WHERE rental_id > %s "
            "ORDER BY rental_id LIMIT 1000",
            lambda r: (r.randint(1, rentals),),
        ),
    )


@dataclass(frozen=True)
class BenchCondition:
    name: str
    description: str
    settings: dict[str, str]


@dataclass(frozen=True)
class BenchSettings:
    user: str
    network: str
    seed: int
    warmup_statements: int
    repetitions: int
    statements_per_block: int
    reload_grace_s: float
    output_dir: Path
    conditions: tuple[BenchCondition, ...]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> BenchSettings:
        try:
            conditions = tuple(
                BenchCondition(
                    name=str(c["name"]),
                    description=str(c["description"]),
                    settings={str(k): str(v) for k, v in c["settings"].items()},
                )
                for c in raw["configs"]
            )
            settings = cls(
                user=str(raw["user"]),
                network=str(raw["network"]),
                seed=int(raw["seed"]),
                warmup_statements=int(raw["warmup_statements"]),
                repetitions=int(raw["repetitions"]),
                statements_per_block=int(raw["statements_per_block"]),
                reload_grace_s=float(raw["reload_grace_s"]),
                output_dir=PROJECT_ROOT / raw["output_dir"],
                conditions=conditions,
            )
        except KeyError as exc:
            raise ConfigError(f"campo obrigatório ausente na seção bench: {exc}") from exc
        except (TypeError, AttributeError, ValueError) as exc:
            raise ConfigError(f"seção bench malformada: {exc!r}") from exc
        for condition in conditions:
            for name in condition.settings:
                if not SETTING_NAME.match(name):
                    raise ConfigError(f"nome de parâmetro inválido em bench: {name}")
        if not conditions or conditions[0].name != "sem_log":
            raise ConfigError("a primeira condição de bench deve ser 'sem_log' (referência)")
        return settings


@dataclass
class ConditionStats:
    latencies_us: list[float]
    log_bytes: int = 0


def run_bench(lab: LabConfig, settings: BenchSettings) -> Path:
    rng = random.Random(settings.seed)
    workload = _workload(lab.pagila_expected_counts)
    stats = {c.name: ConditionStats(latencies_us=[]) for c in settings.conditions}
    touched = sorted({name for c in settings.conditions for name in c.settings})
    with admin_connection(lab, "sacla-bench-admin") as conn:
        log.info("servidor: %s", server_version(conn))
    try:
        # Começa da configuração do arquivo (desfaz ALTER SYSTEM de uma execução interrompida)
        # e aquece os caches do banco e do sistema.
        _apply(lab, settings, {}, touched)
        _execute_block(lab, settings, _make_statements(rng, workload, settings.warmup_statements))
        for rep in range(settings.repetitions):
            statements = _make_statements(rng, workload, settings.statements_per_block)
            shift = rep % len(settings.conditions)
            order = settings.conditions[shift:] + settings.conditions[:shift]
            for condition in order:
                _apply(lab, settings, condition.settings, touched)
                latencies, log_bytes = _execute_block(lab, settings, statements)
                stats[condition.name].latencies_us.extend(latencies)
                stats[condition.name].log_bytes += log_bytes
            log.info("repetição %d/%d concluída", rep + 1, settings.repetitions)
    finally:
        _apply(lab, settings, {}, touched)
        log.info("parâmetros do benchmark revertidos para a configuração do arquivo")
    return _write_results(settings, stats)


def _make_statements(rng: random.Random, workload: Workload, n: int) -> list[Statement]:
    statements = []
    for _ in range(n):
        query, params = rng.choice(workload)
        statements.append((query, params(rng)))
    return statements


def _apply(
    lab: LabConfig, settings: BenchSettings, values: dict[str, str], touched: Sequence[str]
) -> None:
    """Aplica `values` com ALTER SYSTEM; os demais parâmetros de `touched` voltam ao arquivo."""
    with admin_connection(lab, "sacla-bench-admin") as conn:
        for name in touched:
            if name in values:
                conn.execute(
                    sql.SQL("ALTER SYSTEM SET {} = {}").format(
                        sql.SQL(name), sql.Literal(values[name])
                    )
                )
            else:
                conn.execute(sql.SQL("ALTER SYSTEM RESET {}").format(sql.SQL(name)))
        conn.execute("SELECT pg_reload_conf()")
    # Tempo para os backends processarem o SIGHUP antes da medição.
    time.sleep(settings.reload_grace_s)


def _execute_block(
    lab: LabConfig, settings: BenchSettings, statements: list[Statement]
) -> tuple[list[float], int]:
    latencies = []
    with connect(lab, settings.user, lab.sim_password, settings.network, "sacla-bench") as conn:
        wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
        size_before = total_size(lab.log_dir, lab.log_glob)
        with conn.cursor() as cur:
            for query, params in statements:
                start = time.perf_counter_ns()
                cur.execute(query, params)
                cur.fetchall()
                latencies.append((time.perf_counter_ns() - start) / 1000)
    wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
    return latencies, total_size(lab.log_dir, lab.log_glob) - size_before


def _write_results(settings: BenchSettings, stats: dict[str, ConditionStats]) -> Path:
    baseline = statistics.median(stats["sem_log"].latencies_us)
    rows = []
    for condition in settings.conditions:
        lat = stats[condition.name].latencies_us
        median = statistics.median(lat)
        rows.append(
            {
                "condicao": condition.name,
                "descricao": condition.description,
                "comandos": len(lat),
                "latencia_mediana_us": round(median, 1),
                "latencia_p95_us": round(statistics.quantiles(lat, n=20)[-1], 1),
                "latencia_media_us": round(statistics.fmean(lat), 1),
                "overhead_mediana_pct": round(100 * (median - baseline) / baseline, 1),
                "bytes_log_por_comando": round(stats[condition.name].log_bytes / len(lat), 1),
            }
        )
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"bench_auto_explain__n{settings.statements_per_block}"
        f"x{settings.repetitions}_seed{settings.seed}"
    )
    csv_path = settings.output_dir / f"{stem}.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    md_path = settings.output_dir / f"{stem}.md"
    md_path.write_text(_markdown_table(rows), encoding="utf-8")
    print(_markdown_table(rows))
    log.info("resultados salvos em %s", csv_path.relative_to(PROJECT_ROOT))
    return csv_path


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    header = (
        "| Condição | Comandos | Mediana (µs) | p95 (µs) | Média (µs) | "
        "Overhead da mediana | Bytes de log/comando |\n"
        "|---|---:|---:|---:|---:|---:|---:|\n"
    )
    body = "".join(
        f"| {r['condicao']} | {r['comandos']} | {r['latencia_mediana_us']} | "
        f"{r['latencia_p95_us']} | {r['latencia_media_us']} | {r['overhead_mediana_pct']}% | "
        f"{r['bytes_log_por_comando']} |\n"
        for r in rows
    )
    return header + body

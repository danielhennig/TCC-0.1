"""Configuração do laboratório e conexões ao PostgreSQL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from sacla.config import CONFIG_DIR, ConfigError, load_yaml

# Desliga o registro de comandos na sessão administrativa: o provisionamento envia senhas
# e não faz parte do comportamento observado. O próprio SET não é registrado, porque a
# duração é avaliada ao fim do comando, quando o valor já é -1.
QUIET_SESSION_SETTINGS = (
    "SET log_min_duration_statement = -1",
    "SET auto_explain.log_min_duration = -1",
    "SET log_min_error_statement = panic",
)


@dataclass(frozen=True)
class Network:
    name: str
    host: str
    client_ip: str


@dataclass(frozen=True)
class LabConfig:
    database: str
    port: int
    admin_user: str
    admin_password: str
    sim_password: str
    networks: dict[str, Network]
    admin_network: str
    log_dir: Path
    log_glob: str
    log_flush_timeout_s: float
    connect_options: dict[str, str]
    payment_partitions_until: str
    pagila_expected_counts: dict[str, int]

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> LabConfig:
        try:
            networks = {
                name: Network(name=name, host=str(spec["host"]), client_ip=str(spec["client_ip"]))
                for name, spec in raw["networks"].items()
            }
            admin_network = str(raw["admin_network"])
            if admin_network not in networks:
                raise ConfigError(f"lab.admin_network '{admin_network}' não está em lab.networks")
            return cls(
                database=str(raw["database"]),
                port=int(raw["port"]),
                admin_user=str(raw["admin_user"]),
                admin_password=_env(str(raw["admin_password_env"])),
                sim_password=_env(str(raw["sim_password_env"])),
                networks=networks,
                admin_network=admin_network,
                log_dir=Path(raw["log_dir"]),
                log_glob=str(raw["log_glob"]),
                log_flush_timeout_s=float(raw["log_flush_timeout_s"]),
                connect_options={k: str(v) for k, v in raw["connect_options"].items()},
                payment_partitions_until=str(raw["payment_partitions_until"]),
                pagila_expected_counts={
                    str(k): int(v) for k, v in raw["pagila_expected_counts"].items()
                },
            )
        except KeyError as exc:
            raise ConfigError(f"campo obrigatório ausente na seção lab: {exc}") from exc
        except (TypeError, AttributeError, ValueError) as exc:
            raise ConfigError(f"seção lab malformada: {exc!r}") from exc

    def network(self, name: str) -> Network:
        try:
            return self.networks[name]
        except KeyError as exc:
            raise ConfigError(f"rede desconhecida: {name}") from exc


def load_experiment(path: Path | None = None) -> dict[str, Any]:
    return load_yaml(path or CONFIG_DIR / "experiment.yaml")


def load_lab(experiment: dict[str, Any]) -> LabConfig:
    if "lab" not in experiment:
        raise ConfigError("seção lab ausente em experiment.yaml")
    return LabConfig.from_mapping(experiment["lab"])


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"variável de ambiente {name} não definida (veja .env.example)")
    return value


def connect(
    lab: LabConfig,
    user: str,
    password: str,
    network: str,
    application_name: str,
    *,
    client_side_binding: bool = True,
) -> psycopg.Connection[Any]:
    """Abre uma conexão em autocommit saindo pela rede indicada.

    Com `client_side_binding` (padrão) os parâmetros são embutidos no cliente e o comando
    vai pelo protocolo simples: uma linha de log por comando. Sem ele, o psycopg usa o
    protocolo estendido (parse/bind/execute registrados separadamente).
    """
    conninfo = make_conninfo(
        host=lab.network(network).host,
        port=lab.port,
        dbname=lab.database,
        user=user,
        password=password,
        application_name=application_name,
        **lab.connect_options,
    )
    if client_side_binding:
        return psycopg.connect(conninfo, autocommit=True, cursor_factory=psycopg.ClientCursor)
    return psycopg.connect(conninfo, autocommit=True)


def admin_connection(
    lab: LabConfig, application_name: str = "sacla-admin"
) -> psycopg.Connection[Any]:
    """Conexão de superusuário com o registro de comandos desligado na sessão."""
    conn = connect(lab, lab.admin_user, lab.admin_password, lab.admin_network, application_name)
    for statement in QUIET_SESSION_SETTINGS:
        conn.execute(statement)
    return conn


def server_version(conn: psycopg.Connection[Any]) -> str:
    row = conn.execute("SELECT version()").fetchone()
    if row is None:
        raise psycopg.DataError("SELECT version() não devolveu resultado")
    return str(row[0])

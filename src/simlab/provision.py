"""Provisionamento do banco: validação do Pagila, partições, papéis e usuários."""

from __future__ import annotations

import logging
import re
from typing import Any

import psycopg
from psycopg import sql

from simlab.lab import LabConfig, admin_connection, server_version
from simlab.profiles import Profiles, Role, User

log = logging.getLogger(__name__)

PARTITION_NAME = re.compile(r"^payment_p(\d{4})_(\d{2})$")

# Parâmetros exibidos ao fim do provisionamento para comprovar a configuração ativa.
LOGGING_SETTINGS = (
    "logging_collector",
    "log_destination",
    "log_directory",
    "log_filename",
    "log_timezone",
    "log_connections",
    "log_disconnections",
    "log_statement",
    "log_min_duration_statement",
    "log_min_error_statement",
    "compute_query_id",
    "shared_preload_libraries",
    "auto_explain.log_min_duration",
    "auto_explain.log_analyze",
    "auto_explain.log_timing",
    "auto_explain.log_format",
    "auto_explain.log_nested_statements",
)


class ProvisionError(Exception):
    """O banco não está no estado esperado."""


def provision(lab: LabConfig, profiles: Profiles, *, check_counts: bool = True) -> None:
    """Deixa papéis, privilégios e usuários exatamente como em profiles.yaml.

    Feito para um banco recém-criado; rodado de novo, revoga privilégios e filiações que não
    estão mais na configuração. Usuários removidos do YAML não são apagados.
    """
    with admin_connection(lab) as conn:
        log.info("servidor: %s", server_version(conn))
        if check_counts:
            check_pagila_counts(conn, lab.pagila_expected_counts)
        restrict_public_schema(conn)
        ensure_payment_partitions(conn, lab.payment_partitions_until)
        for role in profiles.roles.values():
            ensure_group_role(conn, role)
        for user in profiles.users:
            ensure_login_user(conn, user, lab.sim_password)
        log.info("%d papéis e %d usuários provisionados", len(profiles.roles), len(profiles.users))
        report_logging_settings(conn)


def check_pagila_counts(conn: psycopg.Connection[Any], expected: dict[str, int]) -> None:
    mismatches = {}
    for table, count in expected.items():
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table))
        actual = _scalar(conn, query)
        if actual != count:
            mismatches[table] = (actual, count)
    if mismatches:
        detail = ", ".join(f"{t}: {a} (esperado {e})" for t, (a, e) in mismatches.items())
        raise ProvisionError(
            f"contagens do Pagila diferentes do esperado: {detail}. "
            "O banco precisa estar recém-criado (python tasks.py setup)."
        )
    log.info("Pagila conferido: %d tabelas com as contagens esperadas", len(expected))


def restrict_public_schema(conn: psycopg.Connection[Any]) -> None:
    # O dump do Pagila concede ALL no schema public a PUBLIC; usuários simulados não
    # devem conseguir criar objetos.
    conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")


def ensure_payment_partitions(conn: psycopg.Connection[Any], until: str) -> None:
    """Cria partições mensais de payment após a última existente, até `until` (AAAA-MM)."""
    rows = conn.execute(
        "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'public.payment'::regclass"
    ).fetchall()
    months = []
    for (name,) in rows:
        match = PARTITION_NAME.match(name)
        if match:
            months.append((int(match.group(1)), int(match.group(2))))
    if not months:
        raise ProvisionError("nenhuma partição payment_pAAAA_MM encontrada")
    end = _parse_year_month(until)
    year, month = max(months)
    created = 0
    while (year, month) < end:
        year, month = _next_month(year, month)
        next_year, next_month = _next_month(year, month)
        conn.execute(
            sql.SQL(
                "CREATE TABLE IF NOT EXISTS {} PARTITION OF public.payment "
                "FOR VALUES FROM ({}) TO ({})"
            ).format(
                sql.Identifier("public", f"payment_p{year:04d}_{month:02d}"),
                sql.Literal(f"{year:04d}-{month:02d}-01 00:00:00+00"),
                sql.Literal(f"{next_year:04d}-{next_month:02d}-01 00:00:00+00"),
            )
        )
        created += 1
    log.info("partições de payment criadas: %d (até %s)", created, until)


def ensure_group_role(conn: psycopg.Connection[Any], role: Role) -> None:
    ident = sql.Identifier(role.name)
    if not _role_exists(conn, role.name):
        conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(ident))
    # Parte do zero para que privilégios retirados do YAML não sobrevivam a um novo provision.
    conn.execute(sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(ident))
    conn.execute(sql.SQL("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {}").format(ident))
    for grant in role.grants:
        # Os nomes de privilégio já foram validados contra ALLOWED_PRIVILEGES.
        privileges = sql.SQL(", ").join(sql.SQL(p) for p in grant.privileges)
        if grant.objects is None:
            target: sql.Composable = sql.SQL("ALL TABLES IN SCHEMA public")
        else:
            target = sql.SQL("TABLE {}").format(
                sql.SQL(", ").join(sql.Identifier("public", o) for o in grant.objects)
            )
        conn.execute(sql.SQL("GRANT {} ON {} TO {}").format(privileges, target, ident))
    if role.sequences:
        sequences = sql.SQL(", ").join(sql.Identifier("public", s) for s in role.sequences)
        conn.execute(sql.SQL("GRANT USAGE ON SEQUENCE {} TO {}").format(sequences, ident))


def ensure_login_user(conn: psycopg.Connection[Any], user: User, password: str) -> None:
    ident = sql.Identifier(user.name)
    if _role_exists(conn, user.name):
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(ident, sql.Literal(password))
        )
    else:
        conn.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(ident, sql.Literal(password))
        )
    current = conn.execute(
        "SELECT r.rolname FROM pg_auth_members m "
        "JOIN pg_roles r ON r.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = %s",
        (user.name,),
    ).fetchall()
    for (stale,) in current:
        if stale != user.role:
            conn.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(stale), ident))
    conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(user.role), ident))


def report_logging_settings(conn: psycopg.Connection[Any]) -> dict[str, str]:
    # pg_settings mostra os valores da sessão; os SET da sessão administrativa são
    # trocados pelos valores do servidor (reset_val) nos parâmetros que ela altera.
    rows = conn.execute(
        "SELECT name, reset_val FROM pg_settings WHERE name = ANY(%s) ORDER BY name",
        (list(LOGGING_SETTINGS),),
    ).fetchall()
    settings = {name: value for name, value in rows}
    missing = set(LOGGING_SETTINGS) - settings.keys()
    if missing:
        raise ProvisionError(f"parâmetros ausentes no servidor: {sorted(missing)}")
    width = max(len(n) for n in settings)
    log.info("configuração de logs ativa no servidor:")
    for name in LOGGING_SETTINGS:
        log.info("  %-*s = %s", width, name, settings[name])
    overrides = alter_system_overrides(conn)
    if overrides:
        log.warning(
            "parâmetros sobrescritos via ALTER SYSTEM (postgresql.auto.conf), possivelmente "
            "por um benchmark interrompido: %s. Use `python tasks.py setup` para recomeçar.",
            ", ".join(f"{k}={v}" for k, v in overrides.items()),
        )
    return settings


def alter_system_overrides(conn: psycopg.Connection[Any]) -> dict[str, str]:
    rows = conn.execute(
        "SELECT name, setting FROM pg_file_settings "
        "WHERE sourcefile LIKE %s AND applied ORDER BY name",
        ("%postgresql.auto.conf",),
    ).fetchall()
    return {name: value for name, value in rows}


def _role_exists(conn: psycopg.Connection[Any], name: str) -> bool:
    return conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone() is not None


def _scalar(conn: psycopg.Connection[Any], query: sql.Composed) -> Any:
    row = conn.execute(query).fetchone()
    if row is None:
        raise ProvisionError(f"consulta sem resultado: {query!r}")
    return row[0]


def _parse_year_month(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match or not 1 <= int(match.group(2)) <= 12:
        raise ProvisionError(f"mês inválido (esperado AAAA-MM): {value}")
    return int(match.group(1)), int(match.group(2))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)

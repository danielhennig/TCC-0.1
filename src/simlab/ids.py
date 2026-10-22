"""Identificadores válidos do Pagila, sorteados como parâmetros das ações simuladas."""

from __future__ import annotations

import hashlib
import json
import logging

from psycopg import sql

from simlab.lab import LabConfig, admin_connection

log = logging.getLogger(__name__)


def fetch_id_pools(lab: LabConfig, pools: dict[str, tuple[str, str]]) -> dict[str, list[int]]:
    """Lê, em ordem crescente, os identificadores de cada pool (tabela, coluna).

    Usa a sessão administrativa silenciosa e roda antes da execução; num banco recém-criado
    o resultado é sempre o mesmo, o que mantém a agenda determinística.
    """
    result: dict[str, list[int]] = {}
    with admin_connection(lab, "sacla-ids") as conn:
        for pool, (table, column) in pools.items():
            query = sql.SQL("SELECT {} FROM {} ORDER BY 1").format(
                sql.Identifier(column), sql.Identifier("public", table)
            )
            result[pool] = [int(row[0]) for row in conn.execute(query).fetchall()]
    log.info("pools de ids: %s", {k: len(v) for k, v in result.items()})
    return result


def pools_digest(pools: dict[str, list[int]]) -> str:
    payload = json.dumps(pools, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()

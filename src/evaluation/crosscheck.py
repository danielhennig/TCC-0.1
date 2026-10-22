"""Conferência: o que o SACLA extraiu do log bate com o que o executor de fato fez?

Por usuário, compara os eventos do SACLA com events.parquet + exec.parquet da simulação:
comandos (inclusive BEGIN/COMMIT), comandos com erro, falhas de login, conexões e linhas
(retornadas por SELECT; afetadas por INSERT/UPDATE/DELETE). Qualquer diferença indica erro no
parser e faz a construção do conjunto de dados falhar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from sacla.events import AUTH_FAILURE, CONNECTION, QUERY, QUERY_ERROR
from sacla.sqlclass import DATA_CLASSES, SELECT

MEASURES = ("comandos", "comandos_com_erro", "falhas_de_login", "conexoes", "linhas")


def crosscheck(events: pd.DataFrame, run_dir: Path) -> dict[str, Any]:
    scheduled = pd.read_parquet(run_dir / "events.parquet")
    executed = pd.read_parquet(run_dir / "exec.parquet")
    merged = scheduled.merge(executed, on="event_id")
    statements = merged[merged["kind"] == "statement"]
    expected = pd.DataFrame(
        {
            "comandos": statements.groupby("user").size(),
            "comandos_com_erro": statements[statements["outcome"] != "ok"].groupby("user").size(),
            "falhas_de_login": merged[merged["kind"] == "failed_login"].groupby("user").size(),
            "conexoes": merged[merged["kind"] == "connect"].groupby("user").size(),
            "linhas": statements.groupby("user")["rows"].sum(),
        }
    )

    commands = events[events["kind"].isin([QUERY, QUERY_ERROR])]
    queries = events[events["kind"] == QUERY]
    rows = queries["rows_returned"].where(queries["sql_class"] == SELECT).fillna(0) + queries[
        "rows_affected"
    ].where(queries["sql_class"].isin(list(DATA_CLASSES - {SELECT}))).fillna(0)
    observed = pd.DataFrame(
        {
            "comandos": commands.groupby("user").size(),
            "comandos_com_erro": events[events["kind"] == QUERY_ERROR].groupby("user").size(),
            "falhas_de_login": events[events["kind"] == AUTH_FAILURE].groupby("user").size(),
            "conexoes": events[events["kind"] == CONNECTION].groupby("user").size(),
            "linhas": rows.groupby(queries["user"]).sum(),
        }
    )
    users = sorted(set(expected.index) | set(observed.index))
    expected = expected.reindex(users).fillna(0).astype("int64")
    observed = observed.reindex(users).fillna(0).astype("int64")
    table = pd.concat({"executor": expected, "sacla": observed}, axis=1)
    differences = [
        f"{user}/{measure}: executor={expected.at[user, measure]} "
        f"sacla={observed.at[user, measure]}"
        for user in users
        for measure in MEASURES
        if expected.at[user, measure] != observed.at[user, measure]
    ]
    return {
        "totals": {
            "executor": {m: int(expected[m].sum()) for m in MEASURES},
            "sacla": {m: int(observed[m].sum()) for m in MEASURES},
        },
        "differences": differences,
        "passed": not differences,
        "table": table,
    }

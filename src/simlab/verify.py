"""Coerência entre o manifesto de ground truth e o log remapeado (verificação da bancada).

Para cada cenário: o intervalo cabe no split declarado em run.json, e o log remapeado do
usuário tem, dentro do intervalo, pelo menos tantos comandos (linhas de duração ou de erro) e
falhas de login quanto o cenário gerou. Se o retime deslocasse horários de forma errada, os
comandos da anomalia cairiam fora do intervalo do manifesto e esta verificação falharia.
"""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from simlab.retime import iter_records, parse_log_ts

MAX_EXAMPLES = 10


def verify_manifest(run_dir: Path) -> dict[str, Any]:
    manifest = pd.read_csv(run_dir / "manifest.csv")
    splits = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["splits"]
    activity: dict[str, list[int]] = defaultdict(list)
    for rec in iter_records(sorted((run_dir / "retimed").glob("*.json.gz"))):
        if rec is not None and _is_activity(rec):
            activity[rec.get("user") or ""].append(parse_log_ts(rec["timestamp"]))
    for times in activity.values():
        times.sort()

    problems = []
    scenarios: Iterable[Any] = manifest.itertuples(index=False)
    for sc in scenarios:
        split = splits[sc.split]
        if not (split["start_ms"] <= sc.start_ms and sc.end_ms <= split["end_ms"]):
            problems.append(f"cenário {sc.scenario_id}: fora do split {sc.split}")
        times = activity.get(sc.user, [])
        found = bisect.bisect_left(times, sc.end_ms) - bisect.bisect_left(times, sc.start_ms)
        expected = int(sc.statements) + int(sc.failed_logins)
        if found < expected:
            problems.append(
                f"cenário {sc.scenario_id} ({sc.type}): {found} linhas de atividade no "
                f"intervalo, esperado >= {expected}"
            )
    return {
        "scenarios": len(manifest),
        "problems": len(problems),
        "examples": problems[:MAX_EXAMPLES],
        "passed": not problems,
    }


def _is_activity(rec: dict[str, Any]) -> bool:
    """Linha que corresponde a um comando executado, um comando com erro ou login falho."""
    message = rec.get("message", "")
    if rec.get("state_code") == "28P01" or rec.get("error_severity") == "ERROR":
        return True
    return message.startswith("duration:") and "  statement: " in message

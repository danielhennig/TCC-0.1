"""Rótulos das janelas a partir do manifesto de ground truth (nunca do conteúdo do log).

Regra: a janela [t, t + Δ) do usuário u é anômala se, e somente se, existe um cenário do
manifesto com usuário u e intervalo [início, fim) tal que início < t + Δ e t < fim
(intervalos semiabertos que se sobrepõem). Uma janela que começa exatamente no fim de um
cenário, ou termina exatamente no início dele, não é anômala.

Quando mais de um cenário toca a janela, todos ficam em `scenario_ids`, e tipo, intensidade e
"inédito" vêm do cenário com maior sobreposição. `overlap_frac` é a fração da janela coberta
por esse cenário.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import numpy as np
import pandas as pd

LABEL_COLUMNS = ("label", "scenario_ids", "anomaly_type", "intensity", "novel", "overlap_frac")


def label_windows(windows: pd.DataFrame, manifest: pd.DataFrame) -> pd.DataFrame:
    out = windows.copy()
    n = len(out)
    ids: list[list[int]] = [[] for _ in range(n)]
    best = np.zeros(n)
    kind = np.array([""] * n, dtype=object)
    intensity = np.array([""] * n, dtype=object)
    novel = np.zeros(n, dtype=bool)

    users = out["user"].to_numpy()
    starts = out["window_start_ms"].to_numpy()
    ends = out["window_end_ms"].to_numpy()
    scenarios: Iterable[Any] = manifest.itertuples(index=False)
    for sc in scenarios:
        mask = (users == sc.user) & (starts < sc.end_ms) & (sc.start_ms < ends)
        if not mask.any():
            continue
        covered = (np.minimum(ends, sc.end_ms) - np.maximum(starts, sc.start_ms)) / (ends - starts)
        for i in np.flatnonzero(mask):
            ids[i].append(int(sc.scenario_id))
            if covered[i] > best[i]:
                best[i] = covered[i]
                kind[i] = sc.type
                intensity[i] = sc.intensity
                novel[i] = bool(sc.novel)

    out["label"] = np.array([1 if s else 0 for s in ids], dtype=int)
    out["scenario_ids"] = ["|".join(str(s) for s in sorted(x)) for x in ids]
    out["anomaly_type"] = kind
    out["intensity"] = intensity
    out["novel"] = novel
    out["overlap_frac"] = best
    return out


def assign_split(windows: pd.DataFrame, splits: dict[str, dict[str, int]]) -> pd.Series:
    """Split de cada janela pelo seu início (os limites dos splits são múltiplos da janela)."""
    result = pd.Series(["fora"] * len(windows), index=windows.index, dtype=object)
    for name, bounds in splits.items():
        inside = (windows["window_start_ms"] >= bounds["start_ms"]) & (
            windows["window_start_ms"] < bounds["end_ms"]
        )
        result[inside] = name
    return result

"""Módulo 2b — agregação dos eventos por usuário x janela de tempo e atributos.

Janelas fixas [t, t + Δ), alinhadas ao relógio (Δ = features.window_minutes). Só existem
janelas com pelo menos um evento do usuário.

Atributos absolutos: contagens por tipo de comando, erros, erros de permissão, falhas de
autenticação, conexões, linhas retornadas/afetadas, durações, tabelas e IPs distintos, hora do
dia em seno/cosseno e fim de semana (no fuso configurado).

Atributos relativos ao histórico do próprio usuário: calculados SÓ com janelas anteriores a
`history_end_ms` (o fim do período de treino), para não vazar informação do futuro:
- z robusto (mediana e MAD) do número de comandos e de log(1 + linhas retornadas);
- surpresa da hora (-log da frequência suavizada daquela hora no histórico do usuário);
- frações de tabelas, IPs e formatos de consulta (query_id) nunca vistos no histórico.

Janelas do próprio histórico (o treino), com `features.training_history: leave_one_day_out`:
cada uma é comparada com o histórico do usuário SEM o seu dia (local). Comparadas com o
histórico inteiro, as frações de novidade dariam sempre 0 no treino, e o Isolation Forest
nunca cortaria nesses atributos (docs/decisoes.md, D41 e D45). Assim o treino tem a mesma
natureza "fora da amostra" das janelas que serão pontuadas depois.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np
import pandas as pd

from sacla.config import SaclaSettings
from sacla.events import AUTH_FAILURE, CONNECTION, QUERY, QUERY_ERROR, command_mask
from sacla.sqlclass import DDL, DELETE, INSERT, OTHER, SELECT, UPDATE

MS_PER_MIN = 60_000
IN_SAMPLE = "in_sample"
LEAVE_ONE_DAY_OUT = "leave_one_day_out"
TRAINING_HISTORY_MODES = (IN_SAMPLE, LEAVE_ONE_DAY_OUT)
MAD_TO_SIGMA = 1.4826  # MAD -> desvio-padrão sob normalidade
T = TypeVar("T")

ABSOLUTE_FEATURES = (
    "n_commands",
    "n_select",
    "n_insert",
    "n_update",
    "n_delete",
    "n_ddl",
    "n_other",
    "n_errors",
    "n_permission_denied",
    "n_auth_failures",
    "n_connections",
    "rows_returned_sum",
    "rows_returned_max",
    "rows_affected_sum",
    "duration_mean_ms",
    "duration_max_ms",
    "duration_p95_ms",
    "n_tables",
    "n_ips",
    "hour_sin",
    "hour_cos",
    "weekend",
)
RELATIVE_FEATURES = (
    "z_commands",
    "z_rows_returned",
    "hour_surprisal",
    "new_tables_frac",
    "new_ips_frac",
    "new_query_shapes_frac",
)
FEATURE_COLUMNS = ABSOLUTE_FEATURES + RELATIVE_FEATURES

# Nomes em português para relatórios e gráficos.
FEATURE_LABELS = {
    "n_commands": "comandos",
    "n_select": "SELECT",
    "n_insert": "INSERT",
    "n_update": "UPDATE",
    "n_delete": "DELETE",
    "n_ddl": "DDL",
    "n_other": "outros comandos",
    "n_errors": "comandos com erro",
    "n_permission_denied": "erros de permissão",
    "n_auth_failures": "falhas de autenticação",
    "n_connections": "conexões",
    "rows_returned_sum": "linhas retornadas (soma)",
    "rows_returned_max": "linhas retornadas (máx.)",
    "rows_affected_sum": "linhas afetadas",
    "duration_mean_ms": "duração média (ms)",
    "duration_max_ms": "duração máx. (ms)",
    "duration_p95_ms": "duração p95 (ms)",
    "n_tables": "tabelas distintas",
    "n_ips": "IPs distintos",
    "hour_sin": "hora (seno)",
    "hour_cos": "hora (cosseno)",
    "weekend": "fim de semana",
    "z_commands": "comandos vs. histórico (z robusto)",
    "z_rows_returned": "linhas vs. histórico (z robusto)",
    "hour_surprisal": "raridade da hora para o usuário",
    "new_tables_frac": "tabelas inéditas para o usuário",
    "new_ips_frac": "IPs inéditos para o usuário",
    "new_query_shapes_frac": "formatos de consulta inéditos",
}

_CLASS_COUNTS = {
    "n_select": SELECT,
    "n_insert": INSERT,
    "n_update": UPDATE,
    "n_delete": DELETE,
    "n_ddl": DDL,
    "n_other": OTHER,
}


@dataclass(frozen=True)
class UserBaseline:
    """Histórico de um usuário no período de treino."""

    windows: int
    commands_median: float
    commands_scale: float
    rows_median: float
    rows_scale: float
    hour_log_prob: tuple[float, ...]  # 24 valores: log da frequência suavizada por hora local
    tables: frozenset[str]
    ips: frozenset[str]
    query_ids: frozenset[int]


def aggregate_windows(events: pd.DataFrame, settings: SaclaSettings) -> pd.DataFrame:
    """Atributos absolutos por usuário x janela, mais conjuntos auxiliares (tables_set etc.)."""
    window_ms = settings.features.window_minutes * MS_PER_MIN
    ev = events.copy()
    ev["window_start_ms"] = ev["ts_ms"] - ev["ts_ms"] % window_ms
    is_command = command_mask(ev)
    is_query = ev["kind"] == QUERY
    ev["n_commands"] = is_command.astype(int)
    for column, sql_class in _CLASS_COUNTS.items():
        ev[column] = (is_command & (ev["sql_class"] == sql_class)).astype(int)
    ev["n_errors"] = (ev["kind"] == QUERY_ERROR).astype(int)
    denied = ev["state_code"].isin(list(settings.events.permission_denied_states))
    ev["n_permission_denied"] = ((ev["kind"] == QUERY_ERROR) & denied).astype(int)
    ev["n_auth_failures"] = (ev["kind"] == AUTH_FAILURE).astype(int)
    ev["n_connections"] = (ev["kind"] == CONNECTION).astype(int)
    ev["rows_returned"] = ev["rows_returned"].where(is_query).fillna(0).astype("int64")
    ev["rows_affected"] = ev["rows_affected"].where(is_query).fillna(0).astype("int64")
    # Durações só de comandos (sem BEGIN/COMMIT), coerente com n_commands.
    ev["duration"] = ev["duration_ms"].where(is_query & is_command).astype("float64")
    ev["qid"] = ev["query_id"].where(is_command)

    keys = ["user", "window_start_ms"]
    grouped = ev.groupby(keys, sort=True)
    counts = [
        "n_commands",
        *_CLASS_COUNTS,
        "n_errors",
        "n_permission_denied",
        "n_auth_failures",
        "n_connections",
    ]
    out = grouped[counts].sum()
    out["rows_returned_sum"] = grouped["rows_returned"].sum()
    out["rows_returned_max"] = grouped["rows_returned"].max()
    out["rows_affected_sum"] = grouped["rows_affected"].sum()
    out["duration_mean_ms"] = grouped["duration"].mean()
    out["duration_max_ms"] = grouped["duration"].max()
    out["duration_p95_ms"] = grouped["duration"].quantile(0.95)
    out["tables_set"] = grouped["tables"].agg(_union)
    ips = {key: frozenset(s.dropna()) for key, s in grouped["remote_host"]}
    out["ips_set"] = pd.Series(ips, dtype=object)
    ids = {key: frozenset(int(q) for q in s.dropna()) for key, s in grouped["qid"]}
    out["query_ids_set"] = pd.Series(ids, dtype=object)
    out = out.reset_index()
    for column in ("duration_mean_ms", "duration_max_ms", "duration_p95_ms"):
        out[column] = out[column].fillna(0.0)
    out["n_tables"] = out["tables_set"].map(len)
    out["n_ips"] = out["ips_set"].map(len)
    out["window_end_ms"] = out["window_start_ms"] + window_ms
    _add_time_features(out, settings)
    return out


def fit_baselines(
    windows: pd.DataFrame, settings: SaclaSettings, history_end_ms: int
) -> dict[str, UserBaseline]:
    """Histórico por usuário, usando só janelas que terminam até `history_end_ms`."""
    # A janela inteira precisa estar no histórico: uma janela que atravessa o fim do treino
    # traria eventos do período seguinte.
    history = windows[windows["window_end_ms"] <= history_end_ms]
    return {
        str(user): _baseline(frame, settings) for user, frame in history.groupby("user", sort=True)
    }


def _baseline(frame: pd.DataFrame, settings: SaclaSettings) -> UserBaseline:
    """Histórico de um usuário a partir das suas janelas."""
    alpha = settings.features.hour_smoothing
    floor = settings.features.robust_scale_floor
    commands = frame["n_commands"].to_numpy(dtype=float)
    rows = np.log1p(frame["rows_returned_sum"].to_numpy(dtype=float))
    hours = np.bincount(frame["local_hour"].to_numpy(dtype=int), minlength=24)
    total = hours.sum() + 24 * alpha
    return UserBaseline(
        windows=len(frame),
        commands_median=float(np.median(commands)),
        commands_scale=_robust_scale(commands, floor),
        rows_median=float(np.median(rows)),
        rows_scale=_robust_scale(rows, floor),
        hour_log_prob=tuple(math.log((h + alpha) / total) for h in hours),
        tables=_union(frame["tables_set"]),
        ips=_union(frame["ips_set"]),
        query_ids=_union(frame["query_ids_set"]),
    )


def add_relative_features(
    windows: pd.DataFrame, baselines: dict[str, UserBaseline], settings: SaclaSettings
) -> pd.DataFrame:
    floor = settings.features.robust_scale_floor
    unseen_hour = math.log(1 / 24)  # usuário sem histórico: todas as horas igualmente prováveis
    out = windows.copy()
    z_commands, z_rows, surprisal = [], [], []
    new_tables, new_ips, new_shapes = [], [], []
    rows: Iterable[Any] = out.itertuples(index=False)
    for row in rows:
        base = baselines.get(str(row.user))
        rows_log = math.log1p(float(row.rows_returned_sum))
        if base is None:
            z_commands.append(float(row.n_commands) / floor)
            z_rows.append(rows_log / floor)
            surprisal.append(-unseen_hour)
            seen_tables: frozenset[str] = frozenset()
            seen_ips: frozenset[str] = frozenset()
            seen_ids: frozenset[int] = frozenset()
        else:
            z_commands.append((float(row.n_commands) - base.commands_median) / base.commands_scale)
            z_rows.append((rows_log - base.rows_median) / base.rows_scale)
            surprisal.append(-base.hour_log_prob[int(row.local_hour)])
            seen_tables, seen_ips, seen_ids = base.tables, base.ips, base.query_ids
        new_tables.append(_unseen_fraction(row.tables_set, seen_tables))
        new_ips.append(_unseen_fraction(row.ips_set, seen_ips))
        new_shapes.append(_unseen_fraction(row.query_ids_set, seen_ids))
    out["z_commands"] = z_commands
    out["z_rows_returned"] = z_rows
    out["hour_surprisal"] = surprisal
    out["new_tables_frac"] = new_tables
    out["new_ips_frac"] = new_ips
    out["new_query_shapes_frac"] = new_shapes
    return out


def build_features(
    events: pd.DataFrame, settings: SaclaSettings, history_end_ms: int
) -> tuple[pd.DataFrame, dict[str, UserBaseline]]:
    """Janelas com todos os atributos (FEATURE_COLUMNS) e os históricos usados.

    Os históricos devolvidos são os do período inteiro (usados nas janelas posteriores e no
    uso real); as janelas do próprio histórico seguem `features.training_history`.
    """
    windows = aggregate_windows(events, settings)
    baselines = fit_baselines(windows, settings, history_end_ms)
    features = add_relative_features(windows, baselines, settings)
    if settings.features.training_history == LEAVE_ONE_DAY_OUT:
        in_history = (windows["window_end_ms"] <= history_end_ms).to_numpy()
        if in_history.any():
            own = out_of_day_relative(windows[in_history], settings)
            columns = list(RELATIVE_FEATURES)
            features.loc[in_history, columns] = own.loc[windows.index[in_history], columns]
    return _finish(features), baselines


def out_of_day_relative(history: pd.DataFrame, settings: SaclaSettings) -> pd.DataFrame:
    """Atributos relativos de cada janela do histórico contra os OUTROS dias do usuário.

    O dia é a data local do início da janela. Um usuário com histórico de um dia só fica, nesse
    dia, sem referência (tratado como usuário sem histórico).
    """
    parts = []
    for user, frame in history.groupby("user", sort=True):
        days = frame["window_start_local"].str[:10]
        for day in days.unique():
            others = frame[days != day]
            baselines = {str(user): _baseline(others, settings)} if len(others) else {}
            parts.append(add_relative_features(frame[days == day], baselines, settings))
    return pd.concat(parts).loc[history.index]


def features_with_baselines(
    events: pd.DataFrame, settings: SaclaSettings, baselines: dict[str, UserBaseline]
) -> pd.DataFrame:
    """Janelas de um novo período com históricos já ajustados (uso real: `sacla score`)."""
    return _finish(add_relative_features(aggregate_windows(events, settings), baselines, settings))


def _finish(features: pd.DataFrame) -> pd.DataFrame:
    features["tables"] = features["tables_set"].map(lambda t: "|".join(sorted(t)))
    features["ips"] = features["ips_set"].map(lambda t: "|".join(sorted(t)))
    columns = [
        "user",
        "window_start_ms",
        "window_end_ms",
        "window_start_local",
        *FEATURE_COLUMNS,
        "tables",
        "ips",
    ]
    return features[columns].reset_index(drop=True)


def _add_time_features(out: pd.DataFrame, settings: SaclaSettings) -> None:
    local = pd.to_datetime(out["window_start_ms"], unit="ms", utc=True).dt.tz_convert(
        settings.features.timezone
    )
    hour_frac = (local.dt.hour + local.dt.minute / 60).to_numpy(dtype=float)
    out["hour_sin"] = np.sin(2 * np.pi * hour_frac / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hour_frac / 24)
    out["weekend"] = local.dt.weekday.isin(list(settings.features.weekend_days)).astype(int)
    out["local_hour"] = local.dt.hour.astype(int)
    out["window_start_local"] = local.dt.strftime("%Y-%m-%dT%H:%M%z")


def _union(values: Iterable[Iterable[T]]) -> frozenset[T]:
    result: set[T] = set()
    for items in values:
        result.update(items)
    return frozenset(result)


def _robust_scale(values: np.ndarray, floor: float) -> float:
    median = np.median(values)
    mad = float(np.median(np.abs(values - median)))
    return max(MAD_TO_SIGMA * mad, floor)


def _unseen_fraction(items: frozenset[object], seen: frozenset[object]) -> float:
    if not items:
        return 0.0
    return len(items - seen) / len(items)

"""Dados do painel: configuração, estado do monitor e transformações (sem Streamlit).

As funções daqui são puras ou só leem o SQLite, para poderem ser testadas sem a interface.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from sacla.config import SaclaSettings
from sacla.explain import format_decimal, format_number
from sacla.monitor import LIVE, MonitorSettings, SourceConfig, clock_ms
from sacla.report import assign_episodes, display_time, local_stamp
from sacla.store import NEW, TRIAGE_STATUSES, Store

MS_PER_MIN = 60_000
MS_PER_DAY = 24 * 60 * MS_PER_MIN
NO_DATABASE = "(sem banco)"
# Status de triagem: ícone + rótulo (nunca só a cor).
STATUS_ICONS = {
    "novo": ":material/notification_important:",
    "em análise": ":material/manage_search:",
    "suspeito confirmado": ":material/report:",
    "falso positivo": ":material/check_circle:",
}
STATUS_COLORS = {
    "novo": "red",
    "em análise": "orange",
    "suspeito confirmado": "violet",
    "falso positivo": "green",
}
SOURCE_STATUS_COLORS = {"ativo": "green", "aguardando ajuste": "orange", "erro": "red"}
# Períodos oferecidos nos filtros (rótulo -> extensão em ms; None = tudo).
PERIODS: dict[str, int | None] = {
    "Último dia": MS_PER_DAY,
    "Últimos 7 dias": 7 * MS_PER_DAY,
    "Últimos 30 dias": 30 * MS_PER_DAY,
    "Tudo": None,
}
assert set(STATUS_ICONS) == set(TRIAGE_STATUSES)


@dataclass(frozen=True)
class Period:
    since_ms: int | None
    until_ms: int | None

    def label(self, timezone: str) -> str:
        if self.since_ms is None or self.until_ms is None:
            return "todo o período"
        return (
            f"{display_time(local_stamp(self.since_ms, timezone))} a "
            f"{display_time(local_stamp(self.until_ms, timezone))}"
        )


def window_ms(settings: SaclaSettings) -> int:
    return settings.features.window_minutes * MS_PER_MIN


def source_periods(store: Store, source_ids: list[str], preset: str) -> dict[str, Period]:
    """Período de cada fonte, contado a partir da janela mais recente DELA.

    Uma fonte ao vivo (relógio de hoje) e uma reprodução (relógio da simulação) têm "agoras"
    diferentes; um período único esconderia uma das duas.
    """
    span = PERIODS[preset]
    periods = {}
    for source_id in source_ids:
        latest = store.state(source_id).last_window_end_ms
        if span is None or latest is None:
            periods[source_id] = Period(None, None)
        else:
            periods[source_id] = Period(latest - span, latest)
    return periods


def periods_label(periods: dict[str, Period], names: dict[str, str], timezone: str) -> str:
    known = {s: p for s, p in periods.items() if p.since_ms is not None}
    if not known:
        return "todo o período"
    return " · ".join(f"{names.get(s, s)}: {p.label(timezone)}" for s, p in known.items())


def load_windows(store: Store, periods: dict[str, Period]) -> pd.DataFrame:
    parts = [store.windows(s, p.since_ms, p.until_ms) for s, p in periods.items()]
    return pd.concat(parts, ignore_index=True) if parts else store.windows(until_ms=0)


def load_alerts(store: Store, periods: dict[str, Period], limit: int) -> pd.DataFrame:
    parts = [store.alerts(s, p.since_ms, p.until_ms, limit) for s, p in periods.items()]
    return pd.concat(parts, ignore_index=True) if parts else store.alerts(limit=0)


def local_datetimes(ms: pd.Series, timezone: str) -> pd.Series:
    """ms UTC -> data e hora local sem fuso (os gráficos mostram o relógio do servidor)."""
    stamps = pd.to_datetime(ms, unit="ms", utc=True).dt.tz_convert(timezone)
    return stamps.dt.tz_localize(None)


def databases_of(value: Any) -> list[str]:
    """Bancos de uma janela; sem banco no log vira NO_DATABASE (nunca some dos filtros)."""
    items = [part for part in str(value or "").split("|") if part]
    return items or [NO_DATABASE]


def sources_table(
    monitor: MonitorSettings, store: Store, timezone: str, now: float | None = None
) -> pd.DataFrame:
    """Uma linha por fonte: relógio, status, cobertura, última análise, detector e erro."""
    now = time.time() if now is None else now
    rows = []
    for source in monitor.sources:
        source_state = store.state(source.id)
        info = source_state.detector_info or {}
        coverage = (
            display_time(local_stamp(source_state.last_window_end_ms, timezone))
            if source_state.last_window_end_ms is not None
            else "—"
        )
        rows.append(
            {
                "id": source.id,
                "fonte": source.name,
                "relógio": clock_label(source, source_state.replay_started_at, now, timezone),
                "status": source_state.status,
                "analisado até": coverage,
                "última análise": ago(source_state.last_scan_at, now),
                "detector": info.get("modelo", "—"),
                "histórico do detector": info.get("historico", "—"),
                "limiar": format_decimal(float(info["limiar"]), 4) if "limiar" in info else "—",
                "último erro": source_state.last_error or "",
                "logs": str(source.logs),
            }
        )
    return pd.DataFrame(rows)


def clock_label(
    source: SourceConfig, replay_started_at: float | None, now: float, timezone: str
) -> str:
    if source.clock_mode == LIVE:
        return "ao vivo"
    if replay_started_at is None:
        return f"reprodução ×{format_number(source.replay_speed)} (não iniciada)"
    current = clock_ms(source, replay_started_at, now)
    return (
        f"reprodução ×{format_number(source.replay_speed)} — "
        f"{display_time(local_stamp(current, timezone))}"
    )


def ago(timestamp: float | None, now: float) -> str:
    if timestamp is None:
        return "nunca"
    seconds = max(0, int(now - timestamp))
    if seconds < 60:
        return f"há {seconds} s"
    if seconds < 3600:
        return f"há {seconds // 60} min"
    return f"há {seconds // 3600} h"


def databases_table(store: Store, monitor: MonitorSettings) -> pd.DataFrame:
    """Fonte × banco: usuários, janelas, alertas, novos e última atividade."""
    counts = store.counts()
    columns = ["fonte", "banco", "usuários", "janelas", "alertas", "novos", "ultima_ms"]
    if counts.empty:
        return pd.DataFrame(columns=columns)
    exploded = counts.assign(banco=counts["dbs"].map(databases_of)).explode("banco")
    grouped = exploded.groupby(["source_id", "banco"]).agg(
        usuários=("user", "nunique"),
        janelas=("janelas", "sum"),
        alertas=("alertas", "sum"),
        novos=("alertas_novos", "sum"),
        ultima_ms=("ultima_atividade_ms", "max"),
    )
    names = {s.id: s.name for s in monitor.sources}
    table = grouped.reset_index()
    source_ids = table.pop("source_id")
    table.insert(0, "fonte", source_ids.map(names).fillna(source_ids))
    return table[columns]


def prepare_alerts(alerts: pd.DataFrame, settings: SaclaSettings) -> pd.DataFrame:
    """Alertas com episódio, chave estável do episódio, horário local e primeiro desvio."""
    if alerts.empty:
        return alerts.assign(episodio=[], chave=[], momento=[], desvio=[], bancos=[])
    gap = settings.report.episode_gap_windows
    ordered = alerts.sort_values(["source_id", "user", "window_start_ms"]).reset_index(drop=True)
    parts = []
    offset = 0
    for _, group in ordered.groupby("source_id", sort=True):
        frame = group.copy()
        episodes = assign_episodes(frame, window_ms(settings), gap)
        frame["episodio"] = [e + offset for e in episodes]
        offset += max(episodes, default=0)
        parts.append(frame)
    prepared = pd.concat(parts, ignore_index=True)
    # Chave do episódio: fonte, usuário e início da primeira janela. Não muda quando o monitor
    # acrescenta outros episódios (a numeração e a posição na tabela mudam).
    first = prepared.groupby("episodio")["window_start_ms"].transform("min")
    prepared["chave"] = (
        prepared["source_id"] + "|" + prepared["user"] + "|" + first.astype("int64").astype(str)
    )
    prepared["momento"] = local_datetimes(prepared["window_start_ms"], settings.features.timezone)
    prepared["desvio"] = prepared["explanation"].str.split(" | ", regex=False).str[0]
    prepared["bancos"] = prepared["dbs"].map(databases_of)
    return prepared


EPISODE_COLUMNS = [
    "chave",
    "source_id",
    "usuário",
    "bancos",
    "início",
    "janelas",
    "score_max",
    "status",
    "principal desvio",
    "ids",
    "window_start_ms",
    "window_end_ms",
]


def episodes_table(alerts: pd.DataFrame) -> pd.DataFrame:
    """Um episódio por linha: o status mais urgente entre os alertas dele."""
    if alerts.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    urgency = {status: i for i, status in enumerate(TRIAGE_STATUSES)}
    rows = []
    for key, group in alerts.groupby("chave", sort=False):
        top = group.sort_values("score", ascending=False).iloc[0]
        ordered = group.sort_values("window_start_ms")
        rows.append(
            {
                "chave": str(key),
                "source_id": top["source_id"],
                "usuário": top["user"],
                "bancos": "|".join(sorted({b for v in group["bancos"] for b in v})),
                "início": display_time(str(ordered["inicio"].iloc[0])),
                "janelas": len(group),
                "score_max": float(group["score"].max()),
                "status": min(group["status"], key=lambda s: urgency.get(str(s), len(urgency))),
                "principal desvio": top["desvio"],
                "ids": [int(i) for i in group["id"]],
                "window_start_ms": int(ordered["window_start_ms"].iloc[0]),
                "window_end_ms": int(group["window_end_ms"].max()),
            }
        )
    table = pd.DataFrame(rows, columns=EPISODE_COLUMNS)
    return table.sort_values("window_start_ms", ascending=False).reset_index(drop=True)


def hourly_bins(alerts: pd.DataFrame, max_days: float) -> bool:
    """Intervalos de 1 h se os alertas cobrem até `max_days` dias; senão, de 1 dia."""
    if alerts.empty:
        return True
    span = int(alerts["window_start_ms"].max()) - int(alerts["window_start_ms"].min())
    return span <= max_days * MS_PER_DAY


def alerts_over_time(alerts: pd.DataFrame, max_days: float) -> pd.DataFrame:
    """Alertas por banco em intervalos de 1 h ou de 1 dia (conforme a extensão dos dados)."""
    if alerts.empty:
        return pd.DataFrame(columns=["momento", "banco", "alertas"])
    hourly = hourly_bins(alerts, max_days)
    frame = alerts.assign(banco=alerts["bancos"]).explode("banco")
    frame["momento"] = frame["momento"].dt.floor("h" if hourly else "D")
    return frame.groupby(["momento", "banco"]).size().rename("alertas").reset_index()


def timeline_frame(windows: pd.DataFrame, timezone: str, window: int) -> pd.DataFrame:
    """Score e comandos por janela, com o trecho contínuo de cada uma (`segmento`).

    Só existem janelas com atividade: um intervalo sem janelas abre um novo trecho, para o
    gráfico não ligar o fim de um expediente ao começo do seguinte como se o score continuasse.
    """
    ordered = windows.sort_values("window_start_ms")
    gaps = ordered["window_start_ms"].diff().fillna(window) > window
    return pd.DataFrame(
        {
            "momento": local_datetimes(ordered["window_start_ms"], timezone),
            "score": ordered["score"],
            "comandos": ordered["n_commands"],
            "sinalizada": ordered["flagged"].astype(bool),
            "segmento": gaps.cumsum().astype(int),
        }
    ).reset_index(drop=True)


def summary_of(alert: pd.Series) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(str(alert["summary"]))
    return data


def count_new(alerts: pd.DataFrame) -> int:
    return int((alerts["status"] == NEW).sum()) if not alerts.empty else 0

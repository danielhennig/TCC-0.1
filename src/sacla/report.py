"""Módulo 4b — relatório de anomalias (CSV e HTML).

Uma linha por janela sinalizada (score >= limiar), com:
- o score, a posição entre todas as janelas analisadas e o percentil no histórico;
- os atributos mais fora do padrão daquele usuário (módulo `explain`);
- o resumo da janela.

Janelas sinalizadas do mesmo usuário separadas por até `report.episode_gap_windows` janelas
formam um episódio, que é a unidade que um analista examina.

O SACLA auxilia a identificação: um alerta indica comportamento atípico para o usuário, não um
ataque. O relatório não usa rótulos; a comparação com o ground truth fica na avaliação.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import jinja2
import numpy as np
import pandas as pd

from sacla import __version__
from sacla.config import SaclaSettings
from sacla.explain import UserReference, describe, explain, format_decimal, format_number
from sacla.features import MS_PER_MIN

DISCLAIMER = (
    "O SACLA auxilia a identificação de acessos anômalos: cada alerta indica comportamento "
    "atípico para aquele usuário em relação ao próprio histórico, não um ataque. A decisão é "
    "de quem analisa."
)
# Coluna do relatório -> atributo da janela.
SUMMARY_COLUMNS = {
    "comandos": "n_commands",
    "select": "n_select",
    "insert": "n_insert",
    "update": "n_update",
    "delete": "n_delete",
    "erros": "n_errors",
    "erros_permissao": "n_permission_denied",
    "falhas_autenticacao": "n_auth_failures",
    "conexoes": "n_connections",
    "linhas_retornadas": "rows_returned_sum",
    "linhas_afetadas": "rows_affected_sum",
}
TEMPLATES = Path(__file__).resolve().parent / "templates"


@dataclass(frozen=True)
class ReportContext:
    """Informações do cabeçalho do relatório."""

    title: str
    period: str
    window_minutes: int
    windows: int
    users: int
    threshold: float
    threshold_origin: str
    model: str
    history: str


@dataclass(frozen=True)
class Report:
    alerts: pd.DataFrame  # uma linha por janela sinalizada
    episodes: pd.DataFrame  # uma linha por episódio
    users: pd.DataFrame  # uma linha por usuário analisado
    context: ReportContext


def build_report(
    windows: pd.DataFrame,
    scores: np.ndarray[Any, Any],
    threshold: float,
    train_scores: np.ndarray[Any, Any],
    references: dict[str, UserReference],
    settings: SaclaSettings,
    context: ReportContext,
) -> Report:
    """Alertas, episódios e resumo por usuário de um período já pontuado."""
    frame = windows.reset_index(drop=True).assign(score=np.asarray(scores, dtype=float))
    frame["posicao"] = frame["score"].rank(ascending=False, method="first").astype(int)
    flagged = frame[frame["score"] >= threshold].sort_values(["user", "window_start_ms"])
    window_ms = settings.features.window_minutes * MS_PER_MIN
    flagged = flagged.assign(
        episodio=assign_episodes(flagged, window_ms, settings.report.episode_gap_windows)
    )
    alerts = _alerts_table(flagged, np.sort(train_scores), references, settings)
    return Report(
        alerts=alerts,
        episodes=_episodes_table(alerts),
        users=_users_table(frame, alerts, references),
        context=context,
    )


def assign_episodes(flagged: pd.DataFrame, window_ms: int, gap_windows: int) -> list[int]:
    """Número do episódio de cada janela (em ordem cronológica de início do episódio).

    `flagged` deve estar ordenado por usuário e início da janela.
    """
    starts: list[tuple[int, str, int]] = []  # (início, usuário, índice provisório)
    provisional: list[int] = []
    last: dict[str, int] = {}
    max_gap = gap_windows * window_ms
    for user, start, end in zip(
        flagged["user"], flagged["window_start_ms"], flagged["window_end_ms"], strict=True
    ):
        previous_end = last.get(str(user))
        if previous_end is None or int(start) - previous_end > max_gap:
            starts.append((int(start), str(user), len(starts)))
        provisional.append(starts[-1][2])
        last[str(user)] = int(end)
    order = {index: rank for rank, (_, _, index) in enumerate(sorted(starts), start=1)}
    return [order[index] for index in provisional]


def write_report(report: Report, out_dir: Path, settings: SaclaSettings) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "alertas": out_dir / "alertas.csv",
        "episodios": out_dir / "episodios.csv",
        "usuarios": out_dir / "usuarios.csv",
        "html": out_dir / "relatorio.html",
    }
    report.alerts.to_csv(paths["alertas"], index=False, lineterminator="\n")
    report.episodes.to_csv(paths["episodios"], index=False, lineterminator="\n")
    report.users.to_csv(paths["usuarios"], index=False, lineterminator="\n")
    paths["html"].write_text(render_html(report, settings), encoding="utf-8")
    return paths


def render_html(report: Report, settings: SaclaSettings) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES),
        autoescape=True,  # nomes de usuário, tabelas e IPs vêm do log: sempre escapados
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["num"] = format_number
    env.filters["dec"] = format_decimal
    env.filters["when"] = display_time
    top = report.episodes.sort_values(["score_max", "episodio"], ascending=[False, True]).head(
        settings.report.max_html_episodes
    )
    windows_by_episode = {
        int(str(e)): group.sort_values("window_start_ms").to_dict(orient="records")
        for e, group in report.alerts.groupby("episodio")
    }
    return env.get_template("relatorio.html.j2").render(
        context=asdict(report.context),
        disclaimer=DISCLAIMER,
        version=__version__,
        totals={
            "alerts": len(report.alerts),
            "episodes": len(report.episodes),
            "users_with_alerts": int((report.users["alertas"] > 0).sum()),
        },
        users=report.users.to_dict(orient="records"),
        episodes=top.to_dict(orient="records"),
        windows_by_episode=windows_by_episode,
        shown=len(top),
    )


def _alerts_table(
    flagged: pd.DataFrame,
    sorted_train: np.ndarray[Any, Any],
    references: dict[str, UserReference],
    settings: SaclaSettings,
) -> pd.DataFrame:
    rows = []
    timezone = settings.features.timezone
    for row in flagged.to_dict(orient="records"):
        reference = references.get(str(row["user"]))
        deviations = explain(row, reference, settings.report)
        percentile = float(np.searchsorted(sorted_train, row["score"], side="left")) / max(
            len(sorted_train), 1
        )
        rows.append(
            {
                "episodio": row["episodio"],
                "usuario": row["user"],
                "inicio": row["window_start_local"],
                "fim": local_stamp(int(row["window_end_ms"]), timezone),
                "score": round(float(row["score"]), 6),
                "posicao": row["posicao"],
                "percentil_historico": round(percentile, 4),
                "sem_historico": reference is None,
                "explicacao": " | ".join(describe(deviations, reference)),
                "atributos": "|".join(d.feature for d in deviations),
                **{column: int(row[feature]) for column, feature in SUMMARY_COLUMNS.items()},
                "tabelas": row["tables"],
                "ips": row["ips"],
                "window_start_ms": int(row["window_start_ms"]),
            }
        )
    columns = [
        "episodio",
        "usuario",
        "inicio",
        "fim",
        "score",
        "posicao",
        "percentil_historico",
        "sem_historico",
        "explicacao",
        "atributos",
        *SUMMARY_COLUMNS,
        "tabelas",
        "ips",
        "window_start_ms",
    ]
    table = pd.DataFrame(rows, columns=columns)
    return table.sort_values(["episodio", "window_start_ms"]).reset_index(drop=True)


def _episodes_table(alerts: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for episode, group in alerts.groupby("episodio", sort=True):
        top = group.sort_values("score", ascending=False).iloc[0]
        # Início e fim pelo horário em ms: o texto local não ordena bem se o fuso mudar (DST).
        ordered = group.sort_values("window_start_ms")
        rows.append(
            {
                "episodio": int(str(episode)),
                "usuario": top["usuario"],
                "inicio": ordered["inicio"].iloc[0],
                "fim": ordered["fim"].iloc[-1],
                "janelas": len(group),
                "score_max": float(group["score"].max()),
                "posicao": int(group["posicao"].min()),
                "explicacao": top["explicacao"],
                **{c: int(group[c].sum()) for c in SUMMARY_COLUMNS},
                "tabelas": "|".join(sorted(_union(group["tabelas"]))),
                "ips": "|".join(sorted(_union(group["ips"]))),
            }
        )
    columns = [
        "episodio",
        "usuario",
        "inicio",
        "fim",
        "janelas",
        "score_max",
        "posicao",
        "explicacao",
        *SUMMARY_COLUMNS,
        "tabelas",
        "ips",
    ]
    return pd.DataFrame(rows, columns=columns)


def _users_table(
    frame: pd.DataFrame, alerts: pd.DataFrame, references: dict[str, UserReference]
) -> pd.DataFrame:
    analysed = frame.groupby("user").size().rename("janelas")
    flagged = alerts.groupby("usuario").size().rename("alertas")
    episodes = alerts.groupby("usuario")["episodio"].nunique().rename("episodios")
    table = pd.concat([analysed, flagged, episodes], axis=1).fillna(0).astype(int)
    table.index.name = "usuario"
    table = table.reset_index()
    table["sem_historico"] = ~table["usuario"].isin(list(references))
    table["janelas_historico"] = [
        references[u].windows if u in references else 0 for u in table["usuario"]
    ]
    return table.sort_values(["alertas", "usuario"], ascending=[False, True]).reset_index(drop=True)


def display_time(stamp: str) -> str:
    """'2026-03-23T03:05-0300' -> '23/03/2026 03:05' (horário local gravado no relatório)."""
    return f"{stamp[8:10]}/{stamp[5:7]}/{stamp[0:4]} {stamp[11:16]}"


def local_stamp(ms: int, timezone: str) -> str:
    """ms UTC -> '2026-03-23T03:05-0300' (mesmo formato de window_start_local)."""
    stamp = pd.Timestamp(ms, unit="ms", tz="UTC").tz_convert(timezone)
    return stamp.strftime("%Y-%m-%dT%H:%M%z")


def _union(values: Iterable[Any]) -> set[str]:
    result: set[str] = set()
    for value in values:
        result.update(part for part in str(value or "").split("|") if part)
    return result

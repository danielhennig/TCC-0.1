"""Gráficos do painel (Altair), com a paleta validada da skill de visualização (D49).

Cores por modo (claro/escuro), sempre na ordem fixa dos slots; texto nunca na cor da série.
Os passos escuros foram validados no fundo escuro do Streamlit; no claro, amarelo e água têm
contraste abaixo de 3:1, por isso todo gráfico do painel tem a tabela dos dados ao lado.
"""

from __future__ import annotations

from typing import Any

import altair as alt
import pandas as pd

TOKENS = {
    "light": {
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"],
        "ink": "#0b0b0b",
        "muted": "#898781",
        "surface": "#ffffff",
    },
    "dark": {
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"],
        "ink": "#f0efec",
        "muted": "#898781",
        "surface": "#0e1117",
    },
}


def tokens(mode: str | None) -> dict[str, Any]:
    return TOKENS["dark" if mode == "dark" else "light"]


def alerts_over_time(data: pd.DataFrame, mode: str | None, hourly: bool) -> Any:
    """Barras empilhadas de alertas por banco (identidade de banco = cor, em ordem fixa)."""
    t = tokens(mode)
    banks = sorted(data["banco"].unique())
    bins = max(data["momento"].nunique(), 1)
    return (
        alt.Chart(data)
        # Barras finas (no máximo 24 px), com 1 px de fundo separando os segmentos.
        .mark_bar(stroke=t["surface"], strokeWidth=1, size=max(4, min(24, 640 // bins)))
        .encode(
            x=alt.X(
                "momento:T",
                title=None,
                timeUnit="yearmonthdatehours" if hourly else "yearmonthdate",
                axis=alt.Axis(format="%d/%m %Hh" if hourly else "%d/%m", labelAngle=0),
            ),
            y=alt.Y("alertas:Q", title="Alertas", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color(
                "banco:N",
                title="Banco",
                scale=alt.Scale(domain=banks, range=t["series"][: len(banks)]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("alertas:Q", title="Alertas"),
                alt.Tooltip("banco:N", title="Banco"),
                alt.Tooltip(
                    "momento:T", title="Início", format="%d/%m/%Y %H:%M" if hourly else "%d/%m/%Y"
                ),
            ],
        )
        .properties(height=240)
    )


def users_with_alerts(data: pd.DataFrame, mode: str | None) -> Any:
    """Barras horizontais (uma série) com o valor na ponta."""
    t = tokens(mode)
    base = alt.Chart(data).encode(
        y=alt.Y(
            "usuário:N", sort="-x", title=None, axis=alt.Axis(labelOverlap=False, labelLimit=180)
        ),
        x=alt.X("alertas:Q", title="Alertas", axis=alt.Axis(tickMinStep=1)),
        tooltip=[alt.Tooltip("alertas:Q", title="Alertas"), alt.Tooltip("usuário:N")],
    )
    bars = base.mark_bar(color=t["series"][0], cornerRadiusEnd=4, size=16)
    labels = base.mark_text(align="left", dx=4, color=t["muted"]).encode(text="alertas:Q")
    return (bars + labels).properties(height=max(120, 32 * len(data)))


def user_timeline(
    data: pd.DataFrame,
    threshold: float | None,
    mode: str | None,
    highlight: tuple[Any, Any] | None,
    domain: tuple[Any, Any] | None = None,
) -> Any:
    """Score do usuário por janela, com o limiar, as janelas sinalizadas e uma mira de leitura.

    `data`: momento, score, comandos, sinalizada. `highlight`: (início, fim) do episódio.
    `domain`: (início, fim) fixos do eixo do tempo (um episódio de uma janela só não vira um
    gráfico de um ponto). A escala vertical sempre inclui o limiar.
    """
    t = tokens(mode)
    values = [*data["score"].tolist(), *([threshold] if threshold is not None else [])]
    pad = max((max(values) - min(values)) * 0.1, 0.01) if values else 0.01
    y_scale = alt.Scale(domain=[min(values) - pad, max(values) + pad]) if values else alt.Scale()
    x_scale = (
        alt.Scale(domain=[pd.Timestamp(domain[0]).isoformat(), pd.Timestamp(domain[1]).isoformat()])
        if domain is not None
        else alt.Scale()
    )
    hover = alt.selection_point(
        nearest=True, on="pointerover", fields=["momento"], empty=False, clear="pointerout"
    )
    base = alt.Chart(data).encode(
        x=alt.X("momento:T", title=None, axis=alt.Axis(format="%d/%m %H:%M"), scale=x_scale)
    )
    layers: list[Any] = []
    if highlight is not None:
        band = pd.DataFrame({"inicio": [highlight[0]], "fim": [highlight[1]]})
        layers.append(
            alt.Chart(band)
            .mark_rect(color=t["series"][1], opacity=0.12)
            .encode(x=alt.X("inicio:T", scale=x_scale), x2="fim:T")
        )
    layers.append(
        base.mark_line(color=t["series"][0], strokeWidth=2, interpolate="step-after").encode(
            y=alt.Y("score:Q", title="Score de anomalia", scale=y_scale),
            detail="segmento:N",  # sem atividade, sem linha
        )
    )
    # Cada janela também como ponto: um trecho de uma janela só não desenharia linha.
    layers.append(
        base.mark_point(filled=True, size=12, color=t["series"][0], opacity=0.8).encode(
            y=alt.Y("score:Q", scale=y_scale)
        )
    )
    if threshold is not None:
        layers.append(
            alt.Chart(pd.DataFrame({"limiar": [threshold]}))
            .mark_rule(color=t["ink"], strokeWidth=1, opacity=0.6)
            .encode(y="limiar:Q")
        )
    layers.append(
        base.transform_filter(alt.datum.sinalizada)
        .mark_point(filled=True, size=70, color=t["series"][1], stroke=t["surface"], strokeWidth=2)
        .encode(y="score:Q")
    )
    layers.append(
        base.mark_rule(color=t["muted"], strokeWidth=1)
        .encode(
            opacity=alt.condition(hover, alt.value(0.8), alt.value(0)),
            tooltip=[
                alt.Tooltip("score:Q", title="Score", format=".3f"),
                alt.Tooltip("comandos:Q", title="Comandos"),
                alt.Tooltip("momento:T", title="Janela", format="%d/%m/%Y %H:%M"),
            ],
        )
        .add_params(hover)
    )
    return alt.layer(*layers).properties(height=260)


def hours_profile(data: pd.DataFrame, mode: str | None) -> Any:
    """Fração das janelas por hora local: período monitorado × histórico do detector."""
    t = tokens(mode)
    series = ["Período monitorado", "Histórico (treino)"]
    return (
        alt.Chart(data)
        .mark_bar(stroke=t["surface"], strokeWidth=1)
        .encode(
            x=alt.X("hora:O", title="Hora local", axis=alt.Axis(labelAngle=0)),
            xOffset=alt.XOffset("série:N", sort=series),
            y=alt.Y("fração:Q", title="Janelas", axis=alt.Axis(format="%")),
            color=alt.Color(
                "série:N",
                title=None,
                scale=alt.Scale(domain=series, range=t["series"][:2]),
                legend=alt.Legend(orient="top"),
            ),
            tooltip=[
                alt.Tooltip("fração:Q", title="Janelas", format=".1%"),
                alt.Tooltip("série:N", title="Série"),
                alt.Tooltip("hora:O", title="Hora"),
            ],
        )
        .properties(height=220)
    )

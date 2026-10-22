"""Relatório de anomalias: episódios, posição, ausência de rótulos e HTML seguro."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from sacla.config import load_settings
from sacla.explain import fit_reference
from sacla.features import FEATURE_COLUMNS
from sacla.report import DISCLAIMER, ReportContext, assign_episodes, build_report, write_report

SETTINGS = load_settings()
MIN = 60_000
WINDOW = SETTINGS.features.window_minutes * MIN
START = 1_774_252_800_000  # 2026-03-23T08:00Z


def windows(rows: list[tuple[str, int]], **values: float) -> pd.DataFrame:
    """Janelas (usuário, índice da janela a partir de START) com atributos constantes."""
    frame = pd.DataFrame(0.0, index=range(len(rows)), columns=list(FEATURE_COLUMNS))
    frame["n_commands"] = 10.0
    for column, value in values.items():
        frame[column] = value
    frame["user"] = [user for user, _ in rows]
    frame["window_start_ms"] = [START + i * WINDOW for _, i in rows]
    frame["window_end_ms"] = frame["window_start_ms"] + WINDOW
    frame["window_start_local"] = [
        pd.Timestamp(ms, unit="ms", tz="UTC")
        .tz_convert(SETTINGS.features.timezone)
        .strftime("%Y-%m-%dT%H:%M%z")
        for ms in frame["window_start_ms"]
    ]
    frame["tables"] = "film"
    frame["ips"] = "10.0.0.1"
    return frame


CONTEXT = ReportContext(
    title="teste",
    period="p",
    window_minutes=5,
    windows=0,
    users=0,
    threshold=0.5,
    threshold_origin="o",
    model="m",
    history="h",
)


def test_episodes_join_close_windows_of_the_same_user_in_chronological_order() -> None:
    flagged = windows([("u1", 0), ("u1", 1), ("u1", 3), ("u1", 10), ("u2", 2)])
    flagged = flagged.sort_values(["user", "window_start_ms"])
    episodes = assign_episodes(flagged, WINDOW, gap_windows=1)
    # u1: janelas 0, 1 e 3 (uma janela de intervalo) juntas; a 10 é outro episódio.
    # Numeração pelo início: u1@0 -> 1, u2@2 -> 2, u1@10 -> 3.
    assert episodes == [1, 1, 1, 3, 2]
    # Sem intervalo: u1@3 vira episódio próprio (u1@0 -> 1, u2@2 -> 2, u1@3 -> 3, u1@10 -> 4).
    assert assign_episodes(flagged, WINDOW, gap_windows=0) == [1, 1, 3, 4, 2]


def test_alerts_have_position_percentile_and_no_label_columns() -> None:
    frame = windows([("u1", i) for i in range(6)])
    scores = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.4])
    train_scores = np.arange(100) / 100
    references = fit_reference(
        windows([("u1", i) for i in range(50)]), SETTINGS.report.explain_features
    )
    report = build_report(frame, scores, 0.75, train_scores, references, SETTINGS, CONTEXT)
    assert list(report.alerts["posicao"]) == [1, 2]
    assert list(report.alerts["percentil_historico"]) == [0.9, 0.8]
    assert list(report.episodes["janelas"]) == [2]  # janelas 1 e 3: uma de intervalo
    forbidden = {"label", "anomaly_type", "intensity", "scenario_ids", "split", "novel"}
    for table in (report.alerts, report.episodes, report.users):
        assert not forbidden & set(table.columns)


def test_html_escapes_log_content_and_carries_the_disclaimer(tmp_path: Path) -> None:
    frame = windows([("<script>alert(1)</script>", 0)], n_commands=500.0)
    frame["tables"] = "<img src=x onerror=alert(2)>"
    report = build_report(frame, np.array([0.9]), 0.5, np.array([0.1]), {}, SETTINGS, CONTEXT)
    paths = write_report(report, tmp_path, SETTINGS)
    html = paths["html"].read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    assert "<img src=x" not in html
    assert DISCLAIMER in html
    assert "usuário sem histórico" in html
    assert pd.read_csv(paths["alertas"]).shape[0] == 1


def test_episode_bounds_follow_time_not_text_across_a_dst_change() -> None:
    # Fim do horário de verão em Nova York (2026-11-01, 06:00Z): a janela das 01:30 EDT (-0400)
    # é seguida pela das 01:00 EST (-0500). Em texto, a segunda "vem antes"; o episódio tem de
    # começar na primeira.
    settings = replace(
        SETTINGS,
        features=replace(SETTINGS.features, timezone="America/New_York", window_minutes=30),
    )
    first = 1_793_511_000_000  # 2026-11-01T05:30Z
    frame = windows([("u1", 0), ("u1", 0)])
    frame["window_start_ms"] = [first, first + 30 * MIN]
    frame["window_end_ms"] = frame["window_start_ms"] + 30 * MIN
    frame["window_start_local"] = ["2026-11-01T01:30-0400", "2026-11-01T01:00-0500"]
    report = build_report(frame, np.array([0.9, 0.8]), 0.5, np.array([0.1]), {}, settings, CONTEXT)
    (episode,) = report.episodes.to_dict(orient="records")
    assert episode["inicio"] == "2026-11-01T01:30-0400"
    assert episode["fim"] == "2026-11-01T01:30-0500"


def test_period_without_alerts_still_renders(tmp_path: Path) -> None:
    frame = windows([("u1", 0)])
    report = build_report(frame, np.array([0.1]), 0.5, np.array([0.1]), {}, SETTINGS, CONTEXT)
    html = write_report(report, tmp_path, SETTINGS)["html"].read_text(encoding="utf-8")
    assert report.alerts.empty and report.episodes.empty
    assert "Nenhuma janela passou do limiar" in html

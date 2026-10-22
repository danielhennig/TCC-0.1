"""Uso real do SACLA: fit no histórico, score num novo período, detector salvo em arquivo."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from sacla.config import ConfigError, load_settings
from sacla.events import EVENT_COLUMNS
from sacla.pipeline import Detector, fit_detector, parse_instant, score_events

BASE = load_settings()
SETTINGS = replace(BASE, detector=replace(BASE.detector, n_estimators=100, max_samples=256))
MIN = 60_000
DAY_START = parse_instant("2026-03-02T12:00:00Z")  # 09:00 em São Paulo
assert DAY_START is not None


def event(ts_ms: int, user: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ts_ms": ts_ms,
        "user": user,
        "session_id": f"{user}-{ts_ms // (60 * MIN)}",
        "remote_host": "10.0.0.1",
        "application_name": "app",
        "kind": "query",
        "sql_class": "SELECT",
        "duration_ms": 1.0,
        "rows_returned": 10,
        "rows_affected": None,
        "tables": ("film",),
        "state_code": None,
        "query_id": 1,
    }
    base.update(fields)
    return base


def frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=list(EVENT_COLUMNS))
    for column in ("rows_returned", "rows_affected", "query_id"):
        df[column] = df[column].astype("Int64")
    df["duration_ms"] = df["duration_ms"].astype("Float64")
    return df.sort_values("ts_ms", kind="stable").reset_index(drop=True)


def office_hours(first_day: int, days: int, seed: int) -> list[dict[str, Any]]:
    """Dois usuários com 2–4 consultas por janela de 5 min, das 9h às 17h."""
    rng = np.random.default_rng(seed)
    rows = []
    for day in range(first_day, first_day + days):
        for window in range(8 * 12):
            start = DAY_START + day * 24 * 60 * MIN + window * 5 * MIN
            for user in ("u1", "u2"):
                for k in range(int(rng.integers(2, 5))):
                    rows.append(
                        event(start + k * 1000, user, rows_returned=int(rng.integers(5, 20)))
                    )
    return rows


def test_iso_instants_need_a_timezone() -> None:
    assert parse_instant("2026-03-16T03:00:00Z") == parse_instant("2026-03-16T00:00:00-03:00")
    with pytest.raises(ConfigError, match="fuso"):
        parse_instant("2026-03-16T03:00:00")


def test_fit_then_score_flags_the_unusual_window_and_explains_it(tmp_path: Path) -> None:
    history = frame(office_hours(0, 5, seed=1))
    until = DAY_START + 5 * 24 * 60 * MIN
    detector = fit_detector(history, SETTINGS, until)
    assert detector.history_windows == 2 * 5 * 8 * 12
    expected = np.quantile(detector.train_scores, 1 - SETTINGS.detector.train_alert_rate)
    assert detector.model.threshold == pytest.approx(expected)

    detector.save(tmp_path / "detector.joblib")
    loaded = Detector.load(tmp_path / "detector.joblib")
    # Histórico de segunda (2/3) a sexta; pontua a segunda seguinte (9/3), também dia útil, para
    # que o fim de semana inédito não concorra com a anomalia. Rajada às 03:00 locais.
    night = DAY_START + 7 * 24 * 60 * MIN - 6 * 60 * MIN
    burst = [
        event(night + k * 500, "u1", rows_returned=5_000, tables=("staff",)) for k in range(300)
    ]
    report = score_events(loaded, frame(office_hours(7, 1, seed=2) + burst), "teste")
    assert report.context.windows == 2 * 8 * 12 + 1
    alert = report.alerts[report.alerts["inicio"] == "2026-03-09T03:00-0300"].iloc[0]
    # Sinalizada e entre as primeiras, mas não necessariamente a 1ª: fora da faixa do treino o
    # score do Isolation Forest satura (os cortes das árvores ficam dentro dessa faixa).
    assert alert["usuario"] == "u1" and alert["posicao"] <= 10
    assert "linhas retornadas (soma): 1.500.000 (acima do máximo" in alert["explicacao"]
    assert alert["atributos"].split("|")[0] == "rows_returned_sum"


def test_score_uses_the_settings_saved_with_the_detector(tmp_path: Path) -> None:
    detector = fit_detector(frame(office_hours(0, 2, seed=3)), SETTINGS)
    detector.settings = replace(
        detector.settings, features=replace(detector.settings.features, window_minutes=15)
    )
    report = score_events(detector, frame(office_hours(2, 1, seed=4)), "teste")
    assert report.context.window_minutes == 15


def test_score_takes_presentation_settings_but_keeps_the_fitted_explanation_features() -> None:
    detector = fit_detector(frame(office_hours(0, 2, seed=3)), SETTINGS)
    current = replace(
        SETTINGS.report, top_features=1, explain_features=("n_commands", "inexistente")
    )
    report = score_events(detector, frame(office_hours(2, 1, seed=4)), "teste", current)
    explained = [a.split("|") for a in report.alerts["atributos"] if a]
    assert all(len(features) <= 1 for features in explained)  # top_features atual


def test_detector_from_another_version_is_rejected(tmp_path: Path) -> None:
    detector = fit_detector(frame(office_hours(0, 2, seed=5)), SETTINGS)
    detector.version = "0.0.1"
    detector.save(tmp_path / "antigo.joblib")
    with pytest.raises(ConfigError, match=r"versão 0.0.1"):
        Detector.load(tmp_path / "antigo.joblib")
    (tmp_path / "corrompido.joblib").write_bytes(b"isto nao e um joblib")
    with pytest.raises(ConfigError, match="ilegível"):
        Detector.load(tmp_path / "corrompido.joblib")


def test_empty_history_is_rejected() -> None:
    with pytest.raises(ConfigError, match="nenhum evento"):
        fit_detector(frame([]), SETTINGS)

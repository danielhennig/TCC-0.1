"""Estado do monitor (SQLite): esquema, gravações idempotentes, triagem e travas."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sacla.config import ConfigError
from sacla.report import DESCRIBED_COLUMNS, SUMMARY_COLUMNS
from sacla.store import NEW, WAITING_FIT, Store

MIN = 60_000


def described(starts: list[int], user: str = "u1", score: float = 0.9) -> pd.DataFrame:
    rows = []
    for start in starts:
        row = dict.fromkeys(DESCRIBED_COLUMNS, 0)
        row.update(
            usuario=user,
            inicio=f"2026-03-23T{start // 60:02d}:{start % 60:02d}-0300",
            fim="x",
            score=score,
            percentil_historico=0.99,
            sem_historico=False,
            explicacao="comandos: 300",
            atributos="n_commands",
            tabelas="staff",
            ips="10.0.0.1",
            bancos="pagila",
            bancos_novos="",
            window_start_ms=start * MIN,
            window_end_ms=(start + 5) * MIN,
        )
        rows.append(row)
    return pd.DataFrame(rows, columns=DESCRIBED_COLUMNS)


def test_schema_is_created_once_and_new_source_waits_for_a_detector(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    Store(tmp_path / "s.sqlite")  # reabrir não recria nem falha
    state = store.state("lab")
    assert state.status == WAITING_FIT and state.last_window_end_ms is None


def test_saving_alerts_twice_does_not_duplicate_and_keeps_triage(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    assert store.save_alerts("lab", described([0, 5]), list(SUMMARY_COLUMNS)) == 2
    first = store.alerts("lab")
    store.set_triage([int(first["id"].iloc[0])], "falso positivo", "rotina do batch")
    # Reprocessar o mesmo período (outro score) atualiza o alerta e preserva a triagem.
    assert store.save_alerts("lab", described([0, 5], score=0.8), list(SUMMARY_COLUMNS)) == 0
    again = store.alerts("lab").set_index("id")
    assert len(again) == 2 and set(again["score"]) == {0.8}
    triaged = again.loc[int(first["id"].iloc[0])]
    assert (triaged["status"], triaged["note"]) == ("falso positivo", "rotina do batch")
    assert NEW in set(again["status"])


def test_windows_are_upserted_and_counted_by_database_and_user(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.save_windows("lab", described([0, 5, 10]), [True, False, False])
    store.save_windows("lab", described([10]), [True])  # mesma janela: substitui
    store.save_alerts("lab", described([0, 10]), list(SUMMARY_COLUMNS))
    counts = store.counts().iloc[0]
    assert (counts["janelas"], counts["sinalizadas"], counts["alertas"]) == (3, 2, 2)
    assert counts["alertas_novos"] == 2 and counts["dbs"] == "pagila"


def test_invalid_triage_status_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="triagem"):
        Store(tmp_path / "s.sqlite").set_triage([1], "talvez")


def test_lock_blocks_a_second_scan_until_released_or_expired(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    assert store.try_lock("lab", 60, now=1000.0)
    assert not store.try_lock("lab", 60, now=1010.0)
    assert store.try_lock("lab", 60, now=1061.0)  # expirou
    store.unlock("lab")
    assert store.try_lock("lab", 60, now=1062.0)


def test_reset_deletes_and_rewind_only_moves_the_pointer(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.save_windows("lab", described([0, 5, 10]), [True, True, True])
    store.save_alerts("lab", described([0, 5, 10]), list(SUMMARY_COLUMNS))
    store.update_state("lab", last_window_end_ms=15 * MIN)
    assert not store.rewind("lab", 20 * MIN)  # para a frente pularia janelas: recusado
    assert store.rewind("lab", 5 * MIN)
    assert store.state("lab").last_window_end_ms == 5 * MIN and len(store.alerts("lab")) == 3
    store.reset_source("lab", from_ms=5 * MIN)
    assert list(store.alerts("lab")["window_start_ms"]) == [0]
    store.reset_source("lab")
    assert store.alerts("lab").empty and store.state("lab").last_window_end_ms is None


def test_pointer_only_advances_if_nobody_changed_it_meanwhile(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.update_state("lab", last_window_end_ms=10 * MIN)
    assert store.advance("lab", 10 * MIN, 20 * MIN, now=1.0)
    store.reset_source("lab")  # o painel limpou a fonte durante uma análise
    assert not store.advance("lab", 20 * MIN, 30 * MIN, now=2.0)
    assert store.state("lab").last_window_end_ms is None


def test_panel_actions_wait_for_the_scan_lock(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    assert store.try_lock("lab", 60)  # uma análise em curso
    with pytest.raises(ConfigError, match="ocupada"), store.locked("lab", 60, wait=0.3, poll=0.05):
        pass
    store.unlock("lab")
    with store.locked("lab", 60):
        assert not store.try_lock("lab", 60)
    assert store.try_lock("lab", 60)


def test_prune_removes_untriaged_alerts_the_detector_no_longer_raises(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.save_alerts("lab", described([0, 5, 10]), list(SUMMARY_COLUMNS))
    triaged = int(store.alerts("lab").set_index("window_start_ms").loc[5 * MIN, "id"])
    store.set_triage([triaged], "suspeito confirmado")
    removed = store.prune_alerts("lab", 0, 15 * MIN, keep={("u1", 10 * MIN)})
    assert removed == 1  # o de 0 min; o de 5 min foi triado e fica, o de 10 min continua
    assert sorted(store.alerts("lab")["window_start_ms"]) == [5 * MIN, 10 * MIN]
    assert store.has_windows("lab") is False

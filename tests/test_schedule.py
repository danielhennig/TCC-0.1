"""Agenda da simulação: determinismo, consistência das sessões e posicionamento das anomalias.

Usa a configuração real do projeto e pools de ids sintéticos (não precisa de banco).
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from functools import cache
from itertools import pairwise

from simlab.anomalies import INTENSITIES, load_anomalies
from simlab.behavior import load_behaviors
from simlab.catalog import TYPO_TO, load_catalog
from simlab.lab import load_experiment
from simlab.profiles import load_profiles
from simlab.schedule import Schedule, SimulationSettings, build_schedule, events_frame
from simlab.timeline import CONNECT, DISCONNECT, FAILED_LOGIN, STATEMENT

DAYS = 14
SIZES = {
    "customer": 999,
    "film": 1000,
    "inventory": 4581,
    "staff": 1500,
    "store": 500,
    "rental": 51805,
    "payment": 51061,
    "category": 16,
    "city": 600,
}
IDS = {pool: list(range(1, n + 1)) for pool, n in SIZES.items()}


@cache
def _inputs() -> tuple:  # type: ignore[type-arg]
    profiles = load_profiles()
    catalog = load_catalog()
    behaviors = load_behaviors(profiles, catalog)
    anomalies = load_anomalies(profiles, behaviors, catalog)
    settings = SimulationSettings.from_experiment(load_experiment(), days=DAYS)
    return settings, profiles, behaviors, catalog, anomalies


def _build() -> Schedule:
    settings, profiles, behaviors, catalog, anomalies = _inputs()
    return build_schedule(settings, profiles, behaviors, catalog, anomalies, IDS)


@cache
def _schedule() -> Schedule:
    return _build()


def test_same_seed_produces_identical_schedule() -> None:
    first, second = _schedule(), _build()
    assert events_frame(first.events).equals(events_frame(second.events))
    assert [vars(s) for s in first.scenarios] == [vars(s) for s in second.scenarios]


def test_exported_events_carry_no_label_information() -> None:
    columns = set(events_frame(_schedule().events[:10]).columns)
    assert not columns & {"scenario", "scenario_id", "type", "intensity", "label", "split"}


def test_events_are_sorted_and_sessions_are_well_formed() -> None:
    events = _schedule().events
    assert [e.ts_ms for e in events] == sorted(e.ts_ms for e in events)
    per_session = defaultdict(list)
    for e in events:
        if e.kind != FAILED_LOGIN:
            per_session[e.session].append(e)
    for session_events in per_session.values():
        kinds = [e.kind for e in session_events]
        assert kinds[0] == CONNECT and kinds[-1] == DISCONNECT
        assert set(kinds[1:-1]) <= {STATEMENT}
        assert len({e.user for e in session_events}) == 1


def test_transactions_are_never_interleaved_within_a_session() -> None:
    open_tx: dict[int, bool] = defaultdict(bool)
    for e in _schedule().events:
        if e.kind != STATEMENT or e.statement is None:
            continue
        if e.statement.sql == "BEGIN":
            assert not open_tx[e.session], "BEGIN dentro de transação aberta"
            open_tx[e.session] = True
        elif e.statement.sql == "COMMIT":
            open_tx[e.session] = False
    assert not any(open_tx.values())


def test_typos_only_for_interactive_users() -> None:
    _, profiles, behaviors, *_ = _inputs()
    kinds = {u.name: behaviors.behaviors[u.role].kind for u in profiles.users}
    typo_users = {
        e.user
        for e in _schedule().events
        if e.statement is not None and e.statement.sql.startswith(TYPO_TO)
    }
    assert typo_users and all(kinds[u] == "interactive" for u in typo_users)


def test_scenario_counts_follow_config_scaled_by_duration() -> None:
    settings, _, _, _, anomalies = _inputs()
    scale = DAYS / settings.nominal_days
    cells = Counter((s.split, s.type, s.intensity) for s in _schedule().scenarios if not s.novel)
    for split in ("validation", "test"):
        expected = max(1, math.ceil(anomalies.per_cell[split] * scale))
        for name, atype in anomalies.types.items():
            if not atype.novel:
                assert all(cells[(split, name, i)] == expected for i in INTENSITIES)
    train = sum(n for (split, *_), n in cells.items() if split == "train")
    assert train == math.ceil(anomalies.train_total * scale)


def test_novel_type_only_in_test_split() -> None:
    novel = [s for s in _schedule().scenarios if s.novel]
    assert novel and {s.split for s in novel} == {"test"}


def test_scenarios_fit_split_and_keep_distance_per_user() -> None:
    schedule = _schedule()
    _, _, _, _, anomalies = _inputs()
    gap = anomalies.min_gap_min * 60_000
    by_user = defaultdict(list)
    for s in schedule.scenarios:
        split = schedule.splits[s.split]
        assert split.start_ms <= s.start_ms < s.end_ms <= split.end_ms
        by_user[s.user].append((s.start_ms, s.end_ms))
    for intervals in by_user.values():
        intervals.sort()
        for (_, end), (start, _) in pairwise(intervals):
            assert start - end >= gap


def test_off_hours_scenarios_do_not_touch_normal_sessions() -> None:
    schedule = _schedule()
    anomaly_sessions = {e.session for e in schedule.events if e.scenario is not None}
    sessions = defaultdict(list)
    starts: dict[int, int] = {}
    for e in schedule.events:
        if e.kind == CONNECT and e.session not in anomaly_sessions:
            starts[e.session] = e.ts_ms
        elif e.kind == DISCONNECT and e.session in starts:
            sessions[e.user].append((starts[e.session], e.ts_ms))
    for s in schedule.scenarios:
        if s.type == "fora_horario":
            assert not any(a < s.end_ms and s.start_ms < b for a, b in sessions[s.user])


def test_failed_login_events_match_scenario_attempts() -> None:
    schedule = _schedule()
    per_scenario = Counter(
        e.scenario for e in schedule.events if e.kind == FAILED_LOGIN and e.scenario is not None
    )
    for s in schedule.scenarios:
        if s.type == "falha_auth":
            assert per_scenario[s.scenario_id] == s.failed_logins == s.params["attempts"]

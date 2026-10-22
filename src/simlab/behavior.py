"""Comportamento normal dos perfis (config/profiles.yaml) e geração do tráfego normal.

Três tipos de comportamento:
- interactive: turnos em dias úteis, divididos em sessões; ações chegam como processo de
  Poisson com taxa diária variável;
- service: 24 h por dia, conexões recicladas periodicamente; taxa modulada por hora do dia;
- batch: uma rotina por dia em horário fixo.
Cada usuário usa um gerador aleatório próprio, derivado da semente e do nome, para que mudar
um usuário (ou as anomalias) não altere o tráfego dos demais.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sacla.config import CONFIG_DIR, ConfigError, load_yaml
from simlab.catalog import Catalog, IdPools
from simlab.profiles import Profiles, User
from simlab.timeline import SimSession, Timeline

MS_PER_MIN = 60_000
MS_PER_HOUR = 3_600_000


@dataclass(frozen=True)
class WeeklyTask:
    weekday: int
    time: time
    jitter_min: float
    action: str
    repeat: tuple[int, int]
    spacing_min: tuple[float, float]


@dataclass(frozen=True)
class Behavior:
    role: str
    kind: str
    application_name: str
    networks: dict[str, float]
    think_time_s: tuple[float, float]
    actions: dict[str, float]
    # interactive
    weekdays: frozenset[int] = frozenset()
    shifts: dict[str, tuple[time, time]] | None = None
    shift_jitter_min: float = 0.0
    sessions_per_shift: tuple[int, int] = (1, 1)
    session_gap_min: tuple[float, float] = (0.0, 0.0)
    actions_per_hour: float = 0.0
    daily_rate_sigma: float = 0.0
    overtime_probability: float = 0.0
    overtime_extra_min: tuple[float, float] = (0.0, 0.0)
    off_hours_margin_min: float = 0.0
    weekly: tuple[WeeklyTask, ...] = ()
    # service
    session_minutes: float = 60.0
    hourly_profile: tuple[float, ...] = ()
    # batch
    batch_time: time | None = None
    jitter_min: float = 0.0
    job: str | None = None
    session_padding_s: float = 0.0

    def rate_at(self, local_hour: int) -> float:
        """Ações por hora esperadas na hora local (antes da variação diária)."""
        if self.kind == "service":
            return self.actions_per_hour * self.hourly_profile[local_hour]
        return self.actions_per_hour


@dataclass(frozen=True)
class Noise:
    typo_probability: float
    wrong_password_probability: float
    wrong_password_delay_s: tuple[float, float]
    permission_mistake_per_day: float
    permission_mistakes: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class SessionRules:
    min_session_min: float
    min_segment_before_gap_min: float


@dataclass(frozen=True)
class BehaviorConfig:
    behaviors: dict[str, Behavior]
    forbidden_actions: dict[str, tuple[str, ...]]
    noise: Noise
    sessions: SessionRules


@dataclass(frozen=True)
class Period:
    start_date: date
    days: int
    tz: ZoneInfo

    @property
    def start_ms(self) -> int:
        return self.local_ms(self.start_date, time(0, 0))

    @property
    def end_ms(self) -> int:
        return self.local_ms(self.start_date + timedelta(days=self.days), time(0, 0))

    def dates(self) -> list[date]:
        return [self.start_date + timedelta(days=i) for i in range(self.days)]

    def local_ms(self, day: date, at: time) -> int:
        return int(datetime.combine(day, at, tzinfo=self.tz).timestamp() * 1000)

    def local_hour(self, ts_ms: int) -> int:
        return datetime.fromtimestamp(ts_ms / 1000, tz=self.tz).hour

    def local_date(self, ts_ms: int) -> date:
        return datetime.fromtimestamp(ts_ms / 1000, tz=self.tz).date()


# --- Leitura da configuração --------------------------------------------------------------


def load_behaviors(
    profiles: Profiles, catalog: Catalog, path: Path | None = None
) -> BehaviorConfig:
    """Lê as seções de comportamento de profiles.yaml (behaviors, sessions, noise...)."""
    raw = load_yaml(path or CONFIG_DIR / "profiles.yaml")
    try:
        behaviors = {role: _parse_behavior(role, spec) for role, spec in raw["behaviors"].items()}
        forbidden = {r: tuple(a) for r, a in (raw.get("forbidden_actions") or {}).items()}
        noise_raw = raw["noise"]
        noise = Noise(
            typo_probability=float(noise_raw["typo_probability"]),
            wrong_password_probability=float(noise_raw["wrong_password_probability"]),
            wrong_password_delay_s=_pair(noise_raw["wrong_password_delay_s"], float),
            permission_mistake_per_day=float(noise_raw["permission_mistake_per_day"]),
            permission_mistakes={
                r: tuple(a) for r, a in (noise_raw.get("permission_mistakes") or {}).items()
            },
        )
        sessions = SessionRules(
            min_session_min=float(raw["sessions"]["min_session_min"]),
            min_segment_before_gap_min=float(raw["sessions"]["min_segment_before_gap_min"]),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"comportamentos em profiles.yaml malformados: {exc!r}") from exc

    for user in profiles.users:
        behavior = behaviors.get(user.role)
        if behavior is None:
            raise ConfigError(f"papel {user.role} (usuário {user.name}) sem comportamento")
        if behavior.kind == "interactive" and (
            behavior.shifts is None or user.shift not in behavior.shifts
        ):
            raise ConfigError(f"usuário {user.name}: turno '{user.shift}' inexistente")
    for behavior in behaviors.values():
        names = list(behavior.actions) + [w.action for w in behavior.weekly]
        names += [behavior.job] if behavior.job else []
        catalog.require(names, f"comportamento {behavior.role}")
    for role, actions in [*forbidden.items(), *noise.permission_mistakes.items()]:
        catalog.require(actions, f"ações negadas do papel {role}")
    return BehaviorConfig(
        behaviors=behaviors, forbidden_actions=forbidden, noise=noise, sessions=sessions
    )


def _parse_behavior(role: str, spec: dict[str, Any]) -> Behavior:
    kind = str(spec["kind"])
    common: dict[str, Any] = {
        "role": role,
        "kind": kind,
        "application_name": str(spec["application_name"]),
        "networks": {str(k): float(v) for k, v in spec["networks"].items()},
        "think_time_s": _pair(spec["think_time_s"], float),
        "actions": {str(k): float(v) for k, v in (spec.get("actions") or {}).items()},
    }
    if kind == "interactive":
        overtime = spec.get("overtime") or {}
        return Behavior(
            **common,
            weekdays=frozenset(int(d) for d in spec["weekdays"]),
            shifts={str(k): (_time(v[0]), _time(v[1])) for k, v in spec["shifts"].items()},
            shift_jitter_min=float(spec["shift_jitter_min"]),
            sessions_per_shift=_pair(spec["sessions_per_shift"], int),
            session_gap_min=_pair(spec["session_gap_min"], float),
            actions_per_hour=float(spec["actions_per_hour"]),
            daily_rate_sigma=float(spec["daily_rate_sigma"]),
            overtime_probability=float(overtime.get("probability", 0.0)),
            overtime_extra_min=_pair(overtime.get("extra_min", [0, 0]), float),
            off_hours_margin_min=float(spec["off_hours_margin_min"]),
            weekly=tuple(
                WeeklyTask(
                    weekday=int(w["weekday"]),
                    time=_time(w["time"]),
                    jitter_min=float(w["jitter_min"]),
                    action=str(w["action"]),
                    repeat=_pair(w["repeat"], int),
                    spacing_min=_pair(w["spacing_min"], float),
                )
                for w in spec.get("weekly") or []
            ),
        )
    if kind == "service":
        profile = tuple(float(x) for x in spec["hourly_profile"])
        if len(profile) != 24:
            raise ConfigError(f"hourly_profile de {role} precisa de 24 valores")
        return Behavior(
            **common,
            session_minutes=float(spec["session_minutes"]),
            actions_per_hour=float(spec["actions_per_hour"]),
            daily_rate_sigma=float(spec["daily_rate_sigma"]),
            hourly_profile=profile,
        )
    if kind == "batch":
        return Behavior(
            **common,
            batch_time=_time(spec["time"]),
            jitter_min=float(spec["jitter_min"]),
            job=str(spec["job"]),
            session_padding_s=float(spec["session_padding_s"]),
        )
    raise ConfigError(f"kind desconhecido para {role}: {kind}")


def _time(value: Any) -> time:
    hours, minutes = str(value).split(":")
    return time(int(hours), int(minutes))


def _pair(value: Any, cast: Any) -> Any:
    low, high = value
    return cast(low), cast(high)


# --- Geração do tráfego normal --------------------------------------------------------------


@dataclass
class NormalActivity:
    """Sessões normais de cada usuário, usadas depois para posicionar as anomalias."""

    sessions: dict[str, list[SimSession]]
    statements: int = 0


def generate_normal(
    timeline: Timeline,
    profiles: Profiles,
    config: BehaviorConfig,
    catalog: Catalog,
    ids: IdPools,
    period: Period,
    seed: int,
) -> NormalActivity:
    activity = NormalActivity(sessions={u.name: [] for u in profiles.users})
    for user in profiles.users:
        rng = random.Random(f"{seed}:normal:{user.name}")
        behavior = config.behaviors[user.role]
        generator = _UserGenerator(timeline, user, behavior, config, catalog, ids, period, rng)
        if behavior.kind == "interactive":
            for day in period.dates():
                generator.interactive_day(day)
        elif behavior.kind == "service":
            generator.service_period()
        else:
            for day in period.dates():
                generator.batch_day(day)
        activity.sessions[user.name] = generator.sessions
        activity.statements += generator.statements
    return activity


def pick_weighted(rng: random.Random, weights: dict[str, float]) -> str:
    names = list(weights)
    return rng.choices(names, weights=[weights[n] for n in names], k=1)[0]


class _UserGenerator:
    def __init__(
        self,
        timeline: Timeline,
        user: User,
        behavior: Behavior,
        config: BehaviorConfig,
        catalog: Catalog,
        ids: IdPools,
        period: Period,
        rng: random.Random,
    ) -> None:
        self.tl = timeline
        self.user = user
        self.b = behavior
        self.config = config
        self.catalog = catalog
        self.ids = ids
        self.period = period
        self.rng = rng
        self.sessions: list[SimSession] = []
        self.statements = 0

    # interactive -----------------------------------------------------------------------------

    def interactive_day(self, day: date) -> None:
        b = self.b
        if day.weekday() not in b.weekdays or b.shifts is None or self.user.shift is None:
            return
        shift_start, shift_end = b.shifts[self.user.shift]
        jitter = b.shift_jitter_min * MS_PER_MIN
        start = self.period.local_ms(day, shift_start) + round(self.rng.gauss(0, jitter))
        end = self.period.local_ms(day, shift_end) + round(self.rng.gauss(0, jitter))
        if self.rng.random() < b.overtime_probability:
            end += round(self.rng.uniform(*b.overtime_extra_min) * MS_PER_MIN)
        network = pick_weighted(self.rng, b.networks)
        rate = b.actions_per_hour * self.rng.lognormvariate(0, b.daily_rate_sigma)

        day_sessions = []
        for s_start, s_end in self._split_shift(start, end):
            noise = self.config.noise
            if self.rng.random() < noise.wrong_password_probability:
                before = round(self.rng.uniform(*noise.wrong_password_delay_s) * 1000)
                self.tl.failed_login(self.user.name, network, b.application_name, s_start - before)
            session = self.tl.open_session(
                self.user.name, network, b.application_name, s_start, s_end
            )
            day_sessions.append(session)
            self._poisson_actions(session, lambda _t, r=rate: r)
        self.sessions.extend(day_sessions)

        if day_sessions and self.rng.random() < self.config.noise.permission_mistake_per_day:
            mistakes = self.config.noise.permission_mistakes.get(self.user.role, ())
            if mistakes:
                session = self.rng.choice(day_sessions)
                at = round(self.rng.uniform(session.start_ms, session.end_ms))
                self._action(session, at, self.rng.choice(mistakes))
        for task in b.weekly:
            if day.weekday() == task.weekday and day_sessions:
                self._weekly_task(day, task, day_sessions)

    def _split_shift(self, start: int, end: int) -> list[tuple[int, int]]:
        rules = self.config.sessions
        count = self.rng.randint(*self.b.sessions_per_shift)
        cuts = sorted(self.rng.uniform(start, end) for _ in range(count - 1))
        sessions, cursor = [], start
        for cut in cuts:
            gap = self.rng.uniform(*self.b.session_gap_min) * MS_PER_MIN
            gap_start, gap_end = round(cut - gap / 2), round(cut + gap / 2)
            if gap_start - cursor > rules.min_segment_before_gap_min * MS_PER_MIN:
                sessions.append((cursor, gap_start))
                cursor = gap_end
        sessions.append((cursor, end))
        return [(s, e) for s, e in sessions if e - s > rules.min_session_min * MS_PER_MIN]

    def _weekly_task(self, day: date, task: WeeklyTask, sessions: list[SimSession]) -> None:
        at = self.period.local_ms(day, task.time)
        at += round(self.rng.gauss(0, task.jitter_min * MS_PER_MIN))
        for _ in range(self.rng.randint(*task.repeat)):
            session = next((s for s in sessions if s.start_ms <= at < s.end_ms), None)
            if session is not None:
                self._action(session, at, task.action)
            at += round(self.rng.uniform(*task.spacing_min) * MS_PER_MIN)

    # service ---------------------------------------------------------------------------------

    def service_period(self) -> None:
        b = self.b
        network = pick_weighted(self.rng, b.networks)
        daily = {day: self.rng.lognormvariate(0, b.daily_rate_sigma) for day in self.period.dates()}

        def rate(ts: int) -> float:
            local_day = self.period.local_date(ts)
            return b.rate_at(self.period.local_hour(ts)) * daily.get(local_day, 1.0)

        length = round(b.session_minutes * MS_PER_MIN)
        t = self.period.start_ms
        while t < self.period.end_ms:
            # A próxima conexão abre 1 s depois do fechamento da anterior (reciclagem do pool).
            end = min(t + length, self.period.end_ms) - 1000
            session = self.tl.open_session(self.user.name, network, b.application_name, t, end)
            self.sessions.append(session)
            self._poisson_actions(session, rate)
            t += length

    # batch -----------------------------------------------------------------------------------

    def batch_day(self, day: date) -> None:
        b = self.b
        if b.batch_time is None or b.job is None:
            return
        start = self.period.local_ms(day, b.batch_time)
        start += round(self.rng.gauss(0, b.jitter_min * MS_PER_MIN))
        statements = self.catalog.render(b.job, self.rng, self.ids)
        longest = len(statements) * b.think_time_s[1] * 1000
        end = round(start + longest + b.session_padding_s * 1000)
        network = pick_weighted(self.rng, b.networks)
        session = self.tl.open_session(self.user.name, network, b.application_name, start, end)
        self.sessions.append(session)
        if self.tl.add_action(session, start, statements, self.rng, b.think_time_s) is None:
            raise ConfigError(f"rotina de {self.user.name} não coube na sessão")
        self.statements += len(statements)

    # comum -----------------------------------------------------------------------------------

    def _poisson_actions(self, session: SimSession, rate_per_hour: Any) -> None:
        """Ações em processo de Poisson não homogêneo (por afinamento) dentro da sessão."""
        peak = max(
            rate_per_hour(t) for t in range(session.start_ms, session.end_ms, MS_PER_HOUR // 4)
        )
        if peak <= 0:
            return
        t = float(session.start_ms)
        while True:
            t += self.rng.expovariate(peak / MS_PER_HOUR)
            if t >= session.end_ms:
                return
            if self.rng.random() <= rate_per_hour(int(t)) / peak:
                self._action(session, int(t), pick_weighted(self.rng, self.b.actions))

    def _action(self, session: SimSession, at: int, name: str) -> None:
        typo = self.config.noise.typo_probability if self.b.kind == "interactive" else 0.0
        statements = self.catalog.render(name, self.rng, self.ids, typo_probability=typo)
        if self.tl.add_action(session, at, statements, self.rng, self.b.think_time_s):
            self.statements += len(statements)

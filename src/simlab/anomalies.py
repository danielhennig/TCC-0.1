"""Injeção de anomalias (config/anomalies.yaml) e manifesto de ground truth.

Cada cenário é posicionado respeitando:
- o split a que pertence (o intervalo inteiro fica dentro do split);
- `when`: dentro de uma sessão normal do usuário, fora do horário dele ou a qualquer hora;
- distância mínima de outros cenários do mesmo usuário.
Os eventos do cenário entram na linha do tempo marcados com o id do cenário, que só serve
para calcular o intervalo do manifesto; a marca nunca é exportada junto com os eventos.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import time, timedelta
from pathlib import Path
from typing import Any

from sacla.config import CONFIG_DIR, ConfigError, load_yaml
from simlab.behavior import (
    MS_PER_HOUR,
    MS_PER_MIN,
    BehaviorConfig,
    NormalActivity,
    Period,
    pick_weighted,
)
from simlab.catalog import Catalog, IdPools
from simlab.profiles import Profiles, User
from simlab.timeline import SimSession, Timeline

WHEN = ("active", "off_hours", "any")
INTENSITIES = ("baixa", "media", "alta")
SPLITS = ("train", "validation", "test")
MIN_BURST_FILL = 0.9  # fração mínima das ações de uma rajada que precisa caber na sessão


class PlacementError(Exception):
    """Não foi possível posicionar um cenário respeitando as restrições."""


@dataclass(frozen=True)
class Split:
    name: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class AnomalyType:
    name: str
    description: str
    roles: tuple[str, ...]
    when: str
    intensities: dict[str, dict[str, Any]]
    origins: dict[str, float] | None = None
    actions: dict[str, str] | None = None
    novel: bool = False
    novel_splits: tuple[str, ...] = ()
    novel_per_role_intensity: int = 0  # desenho balanceado: cenários por papel × intensidade
    page_size: int = 0
    first_page: tuple[int, int] = (0, 0)
    page_pools: dict[str, str] | None = None  # papel -> pool de ids da tabela paginada


@dataclass(frozen=True)
class AnomalyConfig:
    types: dict[str, AnomalyType]
    train_total: int
    per_cell: dict[str, int]
    min_gap_min: float
    max_attempts: int
    host_margin_ms: int
    single_action_window_ms: int


@dataclass
class Scenario:
    scenario_id: int
    type: str
    intensity: str
    novel: bool
    split: str
    user: str = ""
    role: str = ""
    network: str = ""
    start_ms: int = 0
    end_ms: int = 0
    statements: int = 0
    failed_logins: int = 0
    params: dict[str, Any] = field(default_factory=dict)


# --- Configuração ---------------------------------------------------------------------------


def load_anomalies(
    profiles: Profiles, behaviors: BehaviorConfig, catalog: Catalog, path: Path | None = None
) -> AnomalyConfig:
    raw = load_yaml(path or CONFIG_DIR / "anomalies.yaml")
    try:
        types = {name: _parse_type(name, spec, False) for name, spec in raw["types"].items()}
        types.update(
            {name: _parse_type(name, spec, True) for name, spec in (raw.get("novel") or {}).items()}
        )
        counts = raw["counts"]
        config = AnomalyConfig(
            types=types,
            train_total=int(counts["train"]["total"]),
            per_cell={s: int(counts[s]["per_cell"]) for s in ("validation", "test")},
            min_gap_min=float(raw["min_gap_min"]),
            max_attempts=int(raw["max_placement_attempts"]),
            host_margin_ms=round(float(raw["host_margin_min"]) * MS_PER_MIN),
            single_action_window_ms=round(float(raw["single_action_window_min"]) * MS_PER_MIN),
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ConfigError(f"anomalies.yaml malformado: {exc!r}") from exc
    _validate(config, profiles, behaviors, catalog)
    return config


def _parse_type(name: str, spec: dict[str, Any], novel: bool) -> AnomalyType:
    intensities = {str(k): dict(v) for k, v in spec["intensities"].items()}
    if tuple(intensities) != INTENSITIES:
        raise ConfigError(f"anomalia {name}: intensidades devem ser {INTENSITIES}")
    when = str(spec["when"])
    if when not in WHEN:
        raise ConfigError(f"anomalia {name}: when deve ser um de {WHEN}")
    return AnomalyType(
        name=name,
        description=str(spec.get("description", "")),
        roles=tuple(str(r) for r in spec["roles"]),
        when=when,
        intensities=intensities,
        origins={str(k): float(v) for k, v in spec["origins"].items()}
        if "origins" in spec
        else None,
        actions={str(k): str(v) for k, v in spec["actions"].items()} if "actions" in spec else None,
        novel=novel,
        novel_splits=tuple(str(s) for s in spec.get("splits", ())),
        novel_per_role_intensity=int(spec.get("count_per_role_intensity", 0)),
        page_size=int(spec.get("page_size", 0)),
        first_page=(int(spec.get("first_page", [0, 0])[0]), int(spec.get("first_page", [0, 0])[1])),
        page_pools={str(k): str(v) for k, v in spec["page_pools"].items()}
        if "page_pools" in spec
        else None,
    )


def _validate(
    config: AnomalyConfig, profiles: Profiles, behaviors: BehaviorConfig, catalog: Catalog
) -> None:
    roles_with_users = {u.role for u in profiles.users}
    for atype in config.types.values():
        missing = set(atype.roles) - roles_with_users
        if missing:
            raise ConfigError(f"anomalia {atype.name}: papéis sem usuário {sorted(missing)}")
        if atype.when == "off_hours":
            for role in atype.roles:
                if behaviors.behaviors[role].kind != "interactive":
                    raise ConfigError(f"anomalia {atype.name}: off_hours exige papel interativo")
        if atype.actions is not None:
            if set(atype.roles) - atype.actions.keys():
                raise ConfigError(f"anomalia {atype.name}: falta ação para algum papel")
            catalog.require(list(atype.actions.values()), f"anomalia {atype.name}")
        for spec in atype.intensities.values():
            if "action" in spec:
                catalog.require([spec["action"]], f"anomalia {atype.name}")
        if atype.name == "sem_permissao":
            for role in atype.roles:
                if not behaviors.forbidden_actions.get(role):
                    raise ConfigError(f"sem_permissao: papel {role} sem forbidden_actions")
        if atype.novel and set(atype.novel_splits) - set(SPLITS):
            raise ConfigError(f"anomalia {atype.name}: split inválido em {atype.novel_splits}")
        if atype.page_pools is not None:
            if set(atype.roles) - atype.page_pools.keys():
                raise ConfigError(f"anomalia {atype.name}: falta page_pools para algum papel")
            unknown = set(atype.page_pools.values()) - catalog.id_pools.keys()
            if unknown:
                raise ConfigError(f"anomalia {atype.name}: pools desconhecidos {sorted(unknown)}")


# --- Plano e posicionamento -----------------------------------------------------------------


PlanItem = tuple[str, str, str, str | None]  # split, tipo, intensidade, papel (ou qualquer)


def plan_scenarios(config: AnomalyConfig, scale: float, rng: random.Random) -> list[PlanItem]:
    """Cenários (split, tipo, intensidade, papel), já escalados pela duração da execução.

    O tipo inédito usa um desenho balanceado (cada papel com cada intensidade), para que
    intensidade e perfil do usuário não se confundam com poucos cenários.
    """
    regular = [t for t in config.types.values() if not t.novel]
    cells = [(t.name, i) for t in regular for i in INTENSITIES]
    plan: list[PlanItem] = [
        ("train", *rng.choice(cells), None) for _ in range(_scaled(config.train_total, scale))
    ]
    for split in ("validation", "test"):
        repeats = _scaled(config.per_cell[split], scale)
        plan += [(split, n, i, None) for n, i in cells for _ in range(repeats)]
    for novel in (t for t in config.types.values() if t.novel):
        full = [
            (role, i)
            for role in novel.roles
            for i in INTENSITIES
            for _ in range(novel.novel_per_role_intensity)
        ]
        wanted = _scaled(len(full), scale)
        if wanted < len(full):
            # Execução curta: amostra em diagonal (papel e intensidade avançam juntos), que não
            # repete papel nem intensidade enquanto houver opção nova.
            n_roles, n_int = len(novel.roles), len(INTENSITIES)
            full = [(novel.roles[k % n_roles], INTENSITIES[k % n_int]) for k in range(wanted)]
        for split in novel.novel_splits:
            plan += [(split, novel.name, i, role) for role, i in full]
    rng.shuffle(plan)
    return plan


def _scaled(count: int, scale: float) -> int:
    return max(1, math.ceil(count * scale)) if count > 0 else 0


class AnomalyInjector:
    def __init__(
        self,
        timeline: Timeline,
        profiles: Profiles,
        behaviors: BehaviorConfig,
        catalog: Catalog,
        ids: IdPools,
        period: Period,
        activity: NormalActivity,
        config: AnomalyConfig,
        splits: dict[str, Split],
        seed: int,
    ) -> None:
        self.tl = timeline
        self.profiles = profiles
        self.behaviors = behaviors
        self.catalog = catalog
        self.ids = ids
        self.period = period
        self.activity = activity
        self.config = config
        self.splits = splits
        self.rng = random.Random(f"{seed}:anomalies")
        self.taken: dict[str, list[tuple[int, int]]] = {u.name: [] for u in profiles.users}
        for atype in config.types.values():
            for role, pool in (atype.page_pools or {}).items():
                if len(ids[pool]) <= atype.page_size:
                    raise ConfigError(
                        f"anomalia {atype.name}: tabela do pool {pool} ({len(ids[pool])} linhas, "
                        f"papel {role}) é menor que uma página de {atype.page_size}"
                    )

    def inject(self, scale: float) -> list[Scenario]:
        plan = plan_scenarios(self.config, scale, self.rng)
        return [
            self._place(i, split, name, intensity, role)
            for i, (split, name, intensity, role) in enumerate(plan, start=1)
        ]

    def _place(
        self, scenario_id: int, split_name: str, type_name: str, intensity: str, role: str | None
    ) -> Scenario:
        atype = self.config.types[type_name]
        spec = atype.intensities[intensity]
        roles = (role,) if role is not None else atype.roles
        candidates = [u for u in self.profiles.users if u.role in roles]
        split = self.splits[split_name]
        for _ in range(self.config.max_attempts):
            user = self.rng.choice(candidates)
            duration = self._duration_ms(spec)
            slot = self._slot(user, atype.when, split, duration)
            if slot is None or not self._is_free(user.name, slot[0], slot[0] + duration):
                continue
            mark = self.tl.mark()
            sc = Scenario(scenario_id, type_name, intensity, atype.novel, split_name)
            sc.user, sc.role = user.name, user.role
            if self._build(sc, atype, spec, user, slot[0], duration, slot[1]):
                own = [e.ts_ms for e in self.tl.events[mark.events :] if e.scenario == scenario_id]
                sc.start_ms, sc.end_ms = min(own), max(own) + 1000
                inside = split.start_ms <= sc.start_ms and sc.end_ms <= split.end_ms
                if inside and self._is_free(user.name, sc.start_ms, sc.end_ms):
                    self.taken[user.name].append((sc.start_ms, sc.end_ms))
                    return sc
            self.tl.rollback(mark)
        raise PlacementError(
            f"cenário {scenario_id} ({type_name}/{intensity}, {split_name}) não coube após "
            f"{self.config.max_attempts} tentativas"
        )

    def _duration_ms(self, spec: dict[str, Any]) -> int:
        if "duration_min" in spec:
            return round(self.rng.uniform(*spec["duration_min"]) * MS_PER_MIN)
        if "pages" in spec:
            return round(int(spec["pages"]) * float(spec["interval_s"][1]) * 1000)
        return self.config.single_action_window_ms  # ação única (volume_alto, dml_incomum)

    def _slot(
        self, user: User, when: str, split: Split, duration: int
    ) -> tuple[int, SimSession | None] | None:
        if when == "active":
            sessions = [
                s
                for s in self.activity.sessions[user.name]
                if s.end_ms > split.start_ms and s.start_ms < split.end_ms
            ]
            if not sessions:
                return None
            session = self.rng.choice(sessions)
            low = max(session.start_ms, split.start_ms) + self.config.host_margin_ms
            high = min(session.end_ms, split.end_ms) - self.config.host_margin_ms - duration
            return (round(self.rng.uniform(low, high)), session) if high > low else None
        if when == "off_hours":
            windows = self._off_hours(user, split, duration)
            if not windows:
                return None
            low, high = self.rng.choice(windows)
            start = round(self.rng.uniform(low, high))
            # Confere contra as sessões reais (o turno tem variação aleatória de horário).
            end = start + duration
            sessions = self.activity.sessions[user.name]
            if any(
                s.start_ms - self.config.host_margin_ms < end
                and start < s.end_ms + self.config.host_margin_ms
                for s in sessions
            ):
                return None
            return start, None
        low, high = (
            split.start_ms + self.config.host_margin_ms,
            split.end_ms - self.config.host_margin_ms - duration,
        )
        return (round(self.rng.uniform(low, high)), None) if high > low else None

    def _off_hours(self, user: User, split: Split, duration: int) -> list[tuple[int, int]]:
        """Intervalos de início possíveis em horários em que o usuário nunca trabalha.

        Em dias de trabalho, o período ocupado vai de (início do turno - margem) até
        (fim do turno + hora extra máxima + margem); dias de folga são inteiros livres.
        """
        behavior = self.behaviors.behaviors[user.role]
        if behavior.shifts is None or user.shift is None:
            return []
        shift_start, shift_end = behavior.shifts[user.shift]
        margin = round(behavior.off_hours_margin_min * MS_PER_MIN)
        overtime = round(behavior.overtime_extra_min[1] * MS_PER_MIN)
        windows = []
        for day in self.period.dates():
            day_start = self.period.local_ms(day, time(0, 0))
            day_end = self.period.local_ms(day + timedelta(days=1), time(0, 0))
            if day.weekday() in behavior.weekdays:
                busy_start = self.period.local_ms(day, shift_start) - margin
                busy_end = self.period.local_ms(day, shift_end) + overtime + margin
                windows += [(day_start, busy_start), (busy_end, day_end)]
            else:
                windows.append((day_start, day_end))
        result = []
        for start, end in windows:
            low = max(start, split.start_ms) + self.config.host_margin_ms
            high = min(end, split.end_ms) - self.config.host_margin_ms - duration
            if high > low:
                result.append((low, high))
        return result

    def _is_free(self, user: str, start: int, end: int) -> bool:
        gap = self.config.min_gap_min * MS_PER_MIN
        return all(end + gap <= s or start >= e + gap for s, e in self.taken[user])

    # --- Construção dos eventos de cada tipo -----------------------------------------------

    def _build(
        self,
        sc: Scenario,
        atype: AnomalyType,
        spec: dict[str, Any],
        user: User,
        start: int,
        duration: int,
        host: SimSession | None,
    ) -> bool:
        behavior = self.behaviors.behaviors[user.role]
        think = behavior.think_time_s
        rng = self.rng

        def act(
            session: SimSession, at: int, action: str, context: dict[str, Any] | None = None
        ) -> int | None:
            statements = self.catalog.render(action, rng, self.ids, context)
            placed = self.tl.add_action(session, at, statements, rng, think, sc.scenario_id)
            if placed is None:
                return None
            sc.statements += len(statements)
            return placed[1]

        if atype.when == "active":
            assert host is not None
            sc.network = host.network

        if sc.type == "rajada":
            assert host is not None
            # Quantidade fixa (taxa vezes duração, mínimo 2) espalhada pela janela: com chegadas de
            # Poisson, um usuário de taxa baixa às vezes teria uma "rajada" de um comando só.
            rate = float(spec["rate_multiplier"]) * behavior.rate_at(self.period.local_hour(start))
            count = max(2, round(rate * duration / MS_PER_HOUR))
            placed = 0
            for at in sorted(rng.uniform(start, start + duration) for _ in range(count)):
                if act(host, round(at), pick_weighted(rng, behavior.actions)) is not None:
                    placed += 1
            sc.params = {
                "rate_multiplier": spec["rate_multiplier"],
                "actions_per_hour": round(rate, 1),
                "actions_requested": count,
                "actions": placed,
            }
            # O manifesto registra o que de fato entrou; abaixo do limiar a vaga é rejeitada.
            return placed >= max(2, math.ceil(MIN_BURST_FILL * count))

        if sc.type == "fora_horario":
            assert atype.origins is not None
            sc.network = pick_weighted(rng, atype.origins)
            target = int(spec["statements"])
            session = self.tl.open_session(
                user.name,
                sc.network,
                behavior.application_name,
                start,
                start + duration,
                sc.scenario_id,
            )
            spacing = duration / (target + 1)
            t: float = start + 2000
            while sc.statements < target:
                end = act(session, round(t), pick_weighted(rng, behavior.actions))
                if end is None:
                    break
                t = end + spacing * rng.uniform(0.5, 1.5)
            sc.params = {"target_statements": target}
            return sc.statements > 0

        if sc.type == "volume_alto":
            assert host is not None
            sc.params = {"action": spec["action"]}
            return act(host, start, str(spec["action"])) is not None

        if sc.type == "sem_permissao":
            assert host is not None
            attempts = rng.randint(*spec["attempts"])
            forbidden = self.behaviors.forbidden_actions[user.role]
            for at in sorted(rng.uniform(start, start + duration) for _ in range(attempts)):
                act(host, round(at), rng.choice(forbidden))
            sc.params = {"attempts": attempts}
            return sc.statements == attempts

        if sc.type == "falha_auth":
            assert atype.origins is not None
            sc.network = pick_weighted(rng, atype.origins)
            attempts = rng.randint(*spec["attempts"])
            for at in sorted(rng.uniform(start, start + duration) for _ in range(attempts)):
                self.tl.failed_login(
                    user.name, sc.network, behavior.application_name, round(at), sc.scenario_id
                )
            sc.failed_logins = attempts
            sc.params = {"attempts": attempts}
            return True

        if sc.type == "dml_incomum":
            assert host is not None and atype.actions is not None
            sc.params = {"rows": int(spec["rows"]), "action": atype.actions[user.role]}
            return (
                act(host, start, atype.actions[user.role], {"rows": int(spec["rows"])}) is not None
            )

        if atype.novel:
            assert host is not None and atype.actions is not None
            pages = int(spec["pages"])
            first_page = rng.randint(*atype.first_page)
            # O deslocamento dá a volta na tabela: sem isso, páginas além do fim voltariam vazias
            # e a intensidade alta pareceria mais fraca que a baixa.
            rows = len(self.ids[atype.page_pools[user.role]]) if atype.page_pools else 0
            span = max(rows - atype.page_size, 1)
            t = start
            for page in range(pages):
                offset = ((first_page + page) * atype.page_size) % span
                context = {"offset": offset, "page_size": atype.page_size}
                end = act(host, t, atype.actions[user.role], context)
                if end is None:
                    return False
                t = end + round(rng.uniform(*spec["interval_s"]) * 1000)
            sc.params = {
                "pages": pages,
                "page_size": atype.page_size,
                "action": atype.actions[user.role],
            }
            return True

        raise ConfigError(f"tipo de anomalia sem gerador: {sc.type}")

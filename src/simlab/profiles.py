"""Perfis de acesso (papéis, privilégios e usuários) definidos em config/profiles.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sacla.config import CONFIG_DIR, ConfigError, load_yaml

ALLOWED_PRIVILEGES = frozenset(
    {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"}
)


@dataclass(frozen=True)
class Grant:
    privileges: tuple[str, ...]
    objects: tuple[str, ...] | None  # None = todas as tabelas e views do schema public


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    grants: tuple[Grant, ...]
    sequences: tuple[str, ...]


@dataclass(frozen=True)
class User:
    name: str
    role: str
    shift: str | None = None


@dataclass(frozen=True)
class Profiles:
    roles: dict[str, Role]
    users: tuple[User, ...]

    def first_user(self, role: str) -> User:
        for user in self.users:
            if user.role == role:
                return user
        raise ConfigError(f"nenhum usuário com o papel {role}")

    def user(self, name: str) -> User:
        for user in self.users:
            if user.name == name:
                return user
        raise ConfigError(f"usuário desconhecido: {name}")


def load_profiles(path: Path | None = None) -> Profiles:
    raw = load_yaml(path or CONFIG_DIR / "profiles.yaml")
    try:
        roles = {name: _parse_role(name, spec) for name, spec in raw["roles"].items()}
        users = tuple(
            User(
                name=str(u["name"]),
                role=str(u["role"]),
                shift=str(u["shift"]) if u.get("shift") is not None else None,
            )
            for u in raw["users"]
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise ConfigError(f"profiles.yaml malformado: {exc!r}") from exc
    for user in users:
        if user.role not in roles:
            raise ConfigError(f"usuário {user.name} usa papel inexistente {user.role}")
    if len({u.name for u in users}) != len(users):
        raise ConfigError("profiles.yaml tem usuários repetidos")
    return Profiles(roles=roles, users=users)


def _parse_role(name: str, spec: dict[str, Any]) -> Role:
    grants = []
    for grant in spec["grants"]:
        privileges = tuple(str(p).upper() for p in grant["privileges"])
        unknown = set(privileges) - ALLOWED_PRIVILEGES
        if unknown:
            raise ConfigError(f"privilégio(s) inválido(s) no papel {name}: {sorted(unknown)}")
        grants.append(Grant(privileges=privileges, objects=_parse_objects(name, grant["objects"])))
    sequences = spec.get("sequences") or []
    if not isinstance(sequences, list):
        raise ConfigError(f"sequences do papel {name} deve ser uma lista")
    return Role(
        name=name,
        description=str(spec.get("description", "")),
        grants=tuple(grants),
        sequences=tuple(str(s) for s in sequences),
    )


def _parse_objects(role: str, spec: Any) -> tuple[str, ...] | None:
    """`ALL` (todas as tabelas e views do schema public) ou uma lista não vazia de nomes."""
    if spec == "ALL":
        return None
    if not isinstance(spec, list) or not spec:
        raise ConfigError(
            f"objects do papel {role} deve ser ALL ou uma lista de tabelas, não {spec!r}"
        )
    return tuple(str(o) for o in spec)

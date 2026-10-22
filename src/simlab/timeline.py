"""Linha do tempo simulada: sessões, comandos e tentativas de login (milissegundos UTC).

Dentro de uma sessão, cada ação (sequência de comandos) ocupa um intervalo exclusivo: uma
ação nunca começa no meio de outra. Isso impede, por exemplo, que um comando de anomalia
caia dentro de uma transação aberta por uma ação normal na mesma conexão.
"""

from __future__ import annotations

import bisect
import random
from dataclasses import dataclass, field

from simlab.catalog import Statement

CONNECT = "connect"
STATEMENT = "statement"
DISCONNECT = "disconnect"
FAILED_LOGIN = "failed_login"

MIN_ACTION_GAP_MS = 200  # folga entre o fim de uma ação e o início da próxima na sessão
EDGE_MS = 1000  # comandos ficam a pelo menos 1 s da conexão e da desconexão


@dataclass(frozen=True)
class Event:
    ts_ms: int
    seq: int  # desempate estável entre eventos no mesmo milissegundo
    kind: str
    session: int
    user: str
    network: str
    application_name: str
    statement: Statement | None = None
    scenario: int | None = None  # só para montar o manifesto; nunca exportado com os eventos


@dataclass
class SimSession:
    key: int
    user: str
    network: str
    application_name: str
    start_ms: int
    end_ms: int
    scenario: int | None = None  # sessão aberta por uma anomalia
    busy: list[tuple[int, int]] = field(default_factory=list)  # intervalos ocupados, ordenados

    def covers(self, start_ms: int, end_ms: int) -> bool:
        return self.start_ms + EDGE_MS <= start_ms and end_ms <= self.end_ms - EDGE_MS

    def free_slot(self, start_ms: int, length_ms: int) -> int:
        """Primeiro instante >= start_ms em que cabe uma ação de `length_ms` sem sobreposição."""
        t = start_ms
        i = bisect.bisect_left(self.busy, (t, t))
        if i > 0 and self.busy[i - 1][1] + MIN_ACTION_GAP_MS > t:
            t = self.busy[i - 1][1] + MIN_ACTION_GAP_MS
        while i < len(self.busy) and self.busy[i][0] < t + length_ms + MIN_ACTION_GAP_MS:
            t = max(t, self.busy[i][1] + MIN_ACTION_GAP_MS)
            i += 1
        return t


@dataclass(frozen=True)
class Mark:
    events: int
    next_session: int
    journal: int


class Timeline:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.sessions: dict[int, SimSession] = {}
        self._seq = 0
        self._next_session = 1
        self._journal: list[tuple[int, tuple[int, int]]] = []  # intervalos ocupados, em ordem

    def mark(self) -> Mark:
        """Ponto de restauração para desfazer uma tentativa de agendamento que falhou."""
        return Mark(len(self.events), self._next_session, len(self._journal))

    def rollback(self, mark: Mark) -> None:
        del self.events[mark.events :]
        for key in range(mark.next_session, self._next_session):
            self.sessions.pop(key, None)
        for key, interval in self._journal[mark.journal :]:
            if key in self.sessions:
                self.sessions[key].busy.remove(interval)
        del self._journal[mark.journal :]
        self._next_session = mark.next_session

    def open_session(
        self,
        user: str,
        network: str,
        application_name: str,
        start_ms: int,
        end_ms: int,
        scenario: int | None = None,
    ) -> SimSession:
        session = SimSession(
            key=self._next_session,
            user=user,
            network=network,
            application_name=application_name,
            start_ms=start_ms,
            end_ms=end_ms,
            scenario=scenario,
        )
        self._next_session += 1
        self.sessions[session.key] = session
        self._add(start_ms, CONNECT, session, scenario=scenario)
        self._add(end_ms, DISCONNECT, session, scenario=scenario)
        return session

    def add_action(
        self,
        session: SimSession,
        start_ms: int,
        statements: list[Statement],
        rng: random.Random,
        think_time_s: tuple[float, float],
        scenario: int | None = None,
    ) -> tuple[int, int] | None:
        """Agenda os comandos de uma ação no primeiro intervalo livre a partir de `start_ms`.

        Devolve (início, fim) da ação ou None se ela não couber antes do fim da sessão.
        """
        gaps = [round(rng.uniform(*think_time_s) * 1000) for _ in statements[1:]]
        length = sum(gaps)
        begin = session.free_slot(max(start_ms, session.start_ms + EDGE_MS), length)
        end = begin + length
        if not session.covers(begin, end):
            return None
        t = begin
        for statement, gap in zip(statements, [0, *gaps], strict=True):
            t += gap
            self._add(t, STATEMENT, session, statement, scenario)
        bisect.insort(session.busy, (begin, end))
        self._journal.append((session.key, (begin, end)))
        return begin, end

    def failed_login(
        self,
        user: str,
        network: str,
        application_name: str,
        ts_ms: int,
        scenario: int | None = None,
    ) -> None:
        # Cada tentativa é uma conexão própria que nunca chega a abrir sessão.
        key = self._next_session
        self._next_session += 1
        self.events.append(
            Event(
                ts_ms,
                self._next_seq(),
                FAILED_LOGIN,
                key,
                user,
                network,
                application_name,
                scenario=scenario,
            )
        )

    def sorted_events(self) -> list[Event]:
        return sorted(self.events, key=lambda e: (e.ts_ms, e.seq))

    def _add(
        self,
        ts_ms: int,
        kind: str,
        session: SimSession,
        statement: Statement | None = None,
        scenario: int | None = None,
    ) -> None:
        self.events.append(
            Event(
                ts_ms=ts_ms,
                seq=self._next_seq(),
                kind=kind,
                session=session.key,
                user=session.user,
                network=session.network,
                application_name=session.application_name,
                statement=statement,
                scenario=scenario,
            )
        )

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

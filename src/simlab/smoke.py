"""Teste de fumaça da Fase 1: gera eventos conhecidos e confere as linhas reais do jsonlog.

Cada passo usa um usuário simulado, uma rede de origem e um `application_name` próprio.
As linhas de log são atribuídas aos passos pelo PID do backend da conexão; as linhas da
falha de autenticação (conexão que nunca chega a ter PID no cliente) pelo SQLSTATE 28P01.
O que os passos gravam no banco é desfeito ao fim, numa sessão administrativa silenciosa,
para que o teste possa ser repetido sem alterar o Pagila.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from sacla.config import PROJECT_ROOT, ConfigError
from simlab.lab import LabConfig, admin_connection, connect
from simlab.logtail import LogSnapshot, parse_auto_explain, read_new_records, wait_until_quiet
from simlab.profiles import Profiles

log = logging.getLogger(__name__)

OTHER = "outros"
MAX_FIELD_CHARS = 700
SMOKE_ID_KEYS = ("customer_id", "film_id_update", "film_id_extended", "inventory_id", "staff_id")


@dataclass(frozen=True)
class SmokeSettings:
    wrong_password: str
    payment_amount: Decimal
    ids: dict[str, int]
    fixture_path: Path
    steps_path: Path
    report_path: Path

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> SmokeSettings:
        try:
            ids = {key: int(raw["ids"][key]) for key in SMOKE_ID_KEYS}
            return cls(
                wrong_password=str(raw["wrong_password"]),
                payment_amount=Decimal(str(raw["payment_amount"])),
                ids=ids,
                fixture_path=PROJECT_ROOT / raw["fixture_path"],
                steps_path=PROJECT_ROOT / raw["steps_path"],
                report_path=PROJECT_ROOT / raw["report_path"],
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            raise ConfigError(f"seção smoke inválida: {exc!r}") from exc


@dataclass
class SmokeContext:
    """Parâmetros dos passos e comandos de limpeza acumulados durante a execução."""

    settings: SmokeSettings
    cleanup: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def id(self, key: str) -> int:
        return self.settings.ids[key]


Action = Callable[[psycopg.Connection[Any], SmokeContext], int]


@dataclass(frozen=True)
class Step:
    name: str
    description: str
    role: str
    network: str
    expected: tuple[str, ...]  # resultados aceitos no cliente: "ok" ou SQLSTATE
    action: Action
    client_side_binding: bool = True
    wrong_password: bool = False


@dataclass
class StepResult:
    name: str
    description: str
    user: str
    network: str
    client_ip: str
    application_name: str
    expected: tuple[str, ...]
    outcome: str = "nao_executado"
    backend_pid: int | None = None
    client_rows: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class Check:
    name: str
    description: str
    passed: bool | None  # None = apenas informativo
    observed: str


# --- Ações de cada passo ------------------------------------------------------------


def _select_customer(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT customer_id, first_name, last_name, email FROM customer WHERE customer_id = %s",
            (ctx.id("customer_id"),),
        )
        return len(cur.fetchall())


def _select_all_rentals(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT rental_id, rental_date, inventory_id, customer_id FROM rental")
        return len(cur.fetchall())


def _update_film(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    # Atribui o próprio valor: a linha é "afetada" sem mudar o dado.
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE film SET rental_rate = rental_rate WHERE film_id = %s",
            (ctx.id("film_id_update"),),
        )
        return cur.rowcount


def _select_staff(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT staff_id, username FROM staff LIMIT 1")
        return len(cur.fetchall())


def _syntax_error(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    with conn.cursor() as cur:
        cur.execute("SELEC 1")
        return 0


def _rental_with_payment(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    customer, staff = ctx.id("customer_id"), ctx.id("staff_id")
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rental (rental_date, inventory_id, customer_id, staff_id) "
            "VALUES (now(), %s, %s, %s) RETURNING rental_id",
            (ctx.id("inventory_id"), customer, staff),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError("INSERT ... RETURNING não devolveu rental_id")
        rental_id = row[0]
        ctx.cleanup.append(("DELETE FROM rental WHERE rental_id = %s", (rental_id,)))
        cur.execute(
            "INSERT INTO payment (customer_id, staff_id, rental_id, amount, payment_date) "
            "VALUES (%s, %s, %s, %s, now())",
            (customer, staff, rental_id, ctx.settings.payment_amount),
        )
        # payment referencia rental: o pagamento precisa ser apagado antes.
        ctx.cleanup.insert(0, ("DELETE FROM payment WHERE rental_id = %s", (rental_id,)))
        return 2


def _select_film_extended(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT title FROM film WHERE film_id = %s", (ctx.id("film_id_extended"),))
        return len(cur.fetchall())


def _no_action(conn: psycopg.Connection[Any], ctx: SmokeContext) -> int:
    return 0


STEPS: tuple[Step, ...] = (
    Step(
        "consulta_normal",
        "SELECT de um cliente pela chave",
        "atendente",
        "lan",
        ("ok",),
        _select_customer,
    ),
    Step(
        "leitura_grande",
        "SELECT de todas as locações",
        "atendente",
        "lan",
        ("ok",),
        _select_all_rentals,
    ),
    Step(
        "update",
        "UPDATE de um filme, saindo pela rede externa",
        "gerente",
        "externa",
        ("ok",),
        _update_film,
    ),
    Step(
        "sem_permissao", "SELECT em staff sem GRANT", "atendente", "lan", ("42501",), _select_staff
    ),
    Step(
        "erro_sintaxe", "comando com erro de sintaxe", "atendente", "lan", ("42601",), _syntax_error
    ),
    Step(
        "transacao_app",
        "BEGIN; INSERT rental; INSERT payment; COMMIT",
        "servico_app",
        "app",
        ("ok",),
        _rental_with_payment,
    ),
    Step(
        "protocolo_estendido",
        "SELECT com parâmetro pelo protocolo estendido",
        "servico_app",
        "app",
        ("ok",),
        _select_film_extended,
        client_side_binding=False,
    ),
    Step(
        "falha_auth",
        "login com senha errada",
        "atendente",
        "externa",
        ("28P01", "falha_conexao"),
        _no_action,
        wrong_password=True,
    ),
)


# --- Execução ---------------------------------------------------------------------------


def run_smoke(lab: LabConfig, profiles: Profiles, settings: SmokeSettings) -> bool:
    # Sem esta espera, o ponto de partida poderia cair no meio de uma linha ainda em gravação.
    wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
    snapshot = LogSnapshot.take(lab.log_dir, lab.log_glob)
    ctx = SmokeContext(settings)
    try:
        results = [_run_step(lab, profiles, step, ctx) for step in STEPS]
        wait_until_quiet(lab.log_dir, lab.log_glob, lab.log_flush_timeout_s)
        records = read_new_records(lab.log_dir, lab.log_glob, snapshot)
    finally:
        _undo(lab, ctx)
    log.info("%d linhas novas no jsonlog", len(records))

    by_step = _assign_records(records, results)
    for result in results:
        _print_step(result, by_step.get(result.name, []))
    print(f"\n=== {OTHER}: {len(by_step.get(OTHER, []))} linha(s) de outros processos")
    _print_samples(by_step)

    checks = _evaluate(results, by_step)
    _print_checks(checks)
    _save(settings, records, results, by_step, checks)
    return all(c.passed is not False for c in checks)


def _undo(lab: LabConfig, ctx: SmokeContext) -> None:
    if not ctx.cleanup:
        return
    with admin_connection(lab, "sacla-smoke-cleanup") as conn, conn.transaction():
        for query, params in ctx.cleanup:
            conn.execute(query, params)
    log.info("alterações do teste desfeitas (%d comandos)", len(ctx.cleanup))


def _run_step(lab: LabConfig, profiles: Profiles, step: Step, ctx: SmokeContext) -> StepResult:
    user = profiles.first_user(step.role).name
    result = StepResult(
        name=step.name,
        description=step.description,
        user=user,
        network=step.network,
        client_ip=lab.network(step.network).client_ip,
        application_name=f"smoke:{step.name}",
        expected=step.expected,
    )
    password = ctx.settings.wrong_password if step.wrong_password else lab.sim_password
    try:
        conn = connect(
            lab,
            user,
            password,
            step.network,
            result.application_name,
            client_side_binding=step.client_side_binding,
        )
    except psycopg.OperationalError as exc:
        result.outcome = exc.sqlstate or "falha_conexao"
        result.error = _first_line(exc)
        return result
    with conn:
        result.backend_pid = conn.info.backend_pid
        try:
            result.client_rows = step.action(conn, ctx)
            result.outcome = "ok"
        except psycopg.Error as exc:
            result.outcome = exc.sqlstate or type(exc).__name__
            result.error = _first_line(exc)
    return result


def _assign_records(
    records: list[dict[str, Any]], results: list[StepResult]
) -> dict[str, list[dict[str, Any]]]:
    pid_to_step = {r.backend_pid: r.name for r in results if r.backend_pid is not None}
    auth_failure_pids = {rec.get("pid") for rec in records if rec.get("state_code") == "28P01"}
    by_step: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        pid = rec.get("pid")
        if pid in pid_to_step:
            name = pid_to_step[pid]
        elif pid in auth_failure_pids:
            name = "falha_auth"
        else:
            name = OTHER
        by_step.setdefault(name, []).append(rec)
    return by_step


# --- Verificações -----------------------------------------------------------------------


def _evaluate(results: list[StepResult], by_step: dict[str, list[dict[str, Any]]]) -> list[Check]:
    res = {r.name: r for r in results}
    lines = {name: by_step.get(name, []) for name in res}
    checks = [
        Check(
            "resultados_no_cliente",
            "cada passo terminou com o resultado esperado no cliente",
            all(r.outcome in r.expected for r in results),
            ", ".join(f"{r.name}={r.outcome}" for r in results),
        )
    ]

    fatal = [x for x in lines["falha_auth"] if x.get("state_code") == "28P01"]
    checks.append(
        Check(
            "falha_auth_com_usuario",
            "a linha FATAL 28P01 traz o campo user com o usuário atacado",
            bool(fatal) and fatal[0].get("user") == res["falha_auth"].user,
            f"user={fatal[0].get('user')!r}" if fatal else "linha FATAL 28P01 não encontrada",
        )
    )
    checks.append(
        Check(
            "falha_auth_application_name",
            "presença de application_name na linha FATAL 28P01 (informativo)",
            None,
            f"application_name={fatal[0].get('application_name', 'ausente')!r}" if fatal else "-",
        )
    )

    durations = _duration_lines(lines["consulta_normal"])
    checks.append(
        Check(
            "uma_linha_por_comando",
            "protocolo simples: uma única linha 'duration ... statement' por comando",
            len(durations) == 1 and "FROM customer" in durations[0]["message"],
            f"{len(durations)} linha(s) de duração com texto",
        )
    )

    plans = _plan_lines(lines["consulta_normal"])
    order_ok = (
        bool(plans) and bool(durations) and plans[0][0]["line_num"] < durations[0]["line_num"]
    )
    checks.append(
        Check(
            "plano_antes_da_duracao",
            "na mesma sessão, a linha do auto_explain vem antes da linha de duração",
            order_ok,
            f"plano line_num={plans[0][0]['line_num'] if plans else None}, "
            f"duração line_num={durations[0]['line_num'] if durations else None}",
        )
    )

    big_plans = _plan_lines(lines["leitura_grande"])
    big_rows = _root_rows(big_plans[0][1]) if big_plans else None
    checks.append(
        Check(
            "linhas_retornadas_auto_explain",
            "Actual Rows do nó raiz = linhas recebidas pelo cliente",
            big_rows is not None and big_rows == res["leitura_grande"].client_rows,
            f"auto_explain={big_rows}, cliente={res['leitura_grande'].client_rows}",
        )
    )

    upd_plans = _plan_lines(lines["update"])
    upd_rows = _modify_table_rows(upd_plans[0][1]) if upd_plans else None
    checks.append(
        Check(
            "linhas_afetadas_update",
            "Actual Rows do filho do ModifyTable = linhas afetadas no cliente",
            upd_rows is not None and upd_rows == res["update"].client_rows,
            f"auto_explain={upd_rows}, cliente={res['update'].client_rows}",
        )
    )

    perm = [x for x in lines["sem_permissao"] if x.get("state_code") == "42501"]
    checks.append(
        Check(
            "erro_permissao_42501",
            "ERROR 42501 com o comando no campo statement e a tabela na mensagem",
            bool(perm)
            and "staff" in perm[0].get("statement", "")
            and "staff" in perm[0]["message"],
            perm[0]["message"] if perm else "linha 42501 não encontrada",
        )
    )

    syntax = [x for x in lines["erro_sintaxe"] if x.get("state_code") == "42601"]
    checks.append(
        Check(
            "erro_sintaxe_42601",
            "ERROR 42601 com o comando no campo statement",
            bool(syntax) and syntax[0].get("statement") == "SELEC 1",
            f"statement={syntax[0].get('statement')!r}" if syntax else "linha 42601 não encontrada",
        )
    )

    ext_messages = [x["message"] for x in lines["protocolo_estendido"] if _is_duration(x)]
    phases = [p for p in ("parse", "bind", "execute") if any(f"  {p} " in m for m in ext_messages)]
    checks.append(
        Check(
            "protocolo_estendido_em_3_linhas",
            "protocolo estendido gera linhas separadas de parse, bind e execute",
            phases == ["parse", "bind", "execute"],
            f"fases encontradas: {phases}",
        )
    )

    tx_statements = [
        m["message"].split("statement: ", 1)[-1].strip()
        for m in _duration_lines(lines["transacao_app"])
    ]
    checks.append(
        Check(
            "transacao_begin_commit",
            "BEGIN e COMMIT aparecem como comandos próprios",
            bool(tx_statements) and tx_statements[0] == "BEGIN" and tx_statements[-1] == "COMMIT",
            " | ".join(s[:40] for s in tx_statements),
        )
    )

    wrong_hosts = sorted(
        {
            f"{name}:{rec.get('remote_host')}"
            for name, r in res.items()
            for rec in lines[name]
            if rec.get("backend_type") == "client backend" and rec.get("remote_host") != r.client_ip
        }
    )
    checks.append(
        Check(
            "remote_host_por_rede",
            "remote_host de cada linha = IP do cliente na rede escolhida",
            not wrong_hosts,
            "todas as linhas conferem" if not wrong_hosts else ", ".join(wrong_hosts),
        )
    )

    all_durations = [x for name in res for x in lines[name] if _is_duration(x)]
    with_query_id = sum(1 for x in all_durations if x.get("query_id"))
    checks.append(
        Check(
            "query_id_nas_linhas_de_duracao",
            "linhas de duração com query_id preenchido (informativo)",
            None,
            f"{with_query_id} de {len(all_durations)}",
        )
    )
    return checks


def _is_duration(rec: dict[str, Any]) -> bool:
    message = rec.get("message", "")
    return message.startswith("duration:") and parse_auto_explain(message) is None


def _duration_lines(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [x for x in records if _is_duration(x) and "statement: " in x["message"]]


def _plan_lines(records: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    plans = []
    for rec in records:
        plan = parse_auto_explain(rec.get("message", ""))
        if plan is not None:
            plans.append((rec, plan))
    return plans


def _root_rows(plan: dict[str, Any]) -> int | None:
    rows = plan.get("Plan", {}).get("Actual Rows")
    return None if rows is None else round(rows)


def _modify_table_rows(plan: dict[str, Any]) -> int | None:
    root = plan.get("Plan", {})
    if root.get("Node Type") != "ModifyTable" or not root.get("Plans"):
        return None
    rows = root["Plans"][0].get("Actual Rows")
    return None if rows is None else round(rows)


# --- Saída ------------------------------------------------------------------------------


def _print_step(result: StepResult, records: list[dict[str, Any]]) -> None:
    print(f"\n=== {result.name}: {result.description}")
    print(
        f"    usuário={result.user}  rede={result.network} ({result.client_ip})  "
        f"resultado={result.outcome}  linhas_no_cliente={result.client_rows}"
    )
    if result.error:
        print(f"    erro no cliente: {result.error}")
    for rec in records:
        print("    " + _summarize(rec))


def _summarize(rec: dict[str, Any]) -> str:
    message = rec.get("message", "")
    plan = parse_auto_explain(message)
    if plan is not None:
        root = plan.get("Plan", {})
        message = (
            f"[auto_explain] {message.split('  plan:')[0]}; raiz={root.get('Node Type')} "
            f"Actual Rows={root.get('Actual Rows')}"
        )
    message = message.replace("\n", " ")
    if len(message) > 110:
        message = message[:107] + "..."
    return (
        f"#{rec.get('line_num', '?'):>2} {rec.get('error_severity', ''):<5} "
        f"{rec.get('state_code', ''):<5} host={rec.get('remote_host', '-')} :: {message}"
    )


SAMPLES: tuple[tuple[str, str, Callable[[dict[str, Any]], bool]], ...] = (
    (
        "conexão recebida",
        "consulta_normal",
        lambda r: r.get("message", "").startswith("connection received"),
    ),
    (
        "autenticação",
        "consulta_normal",
        lambda r: r.get("message", "").startswith("connection authenticated"),
    ),
    (
        "autorização",
        "consulta_normal",
        lambda r: r.get("message", "").startswith("connection authorized"),
    ),
    (
        "plano do auto_explain",
        "consulta_normal",
        lambda r: parse_auto_explain(r.get("message", "")) is not None,
    ),
    ("comando concluído", "consulta_normal", lambda r: "statement: " in r.get("message", "")),
    ("desconexão", "consulta_normal", lambda r: r.get("message", "").startswith("disconnection")),
    ("erro de permissão", "sem_permissao", lambda r: r.get("state_code") == "42501"),
    ("erro de sintaxe", "erro_sintaxe", lambda r: r.get("state_code") == "42601"),
    ("falha de autenticação", "falha_auth", lambda r: r.get("state_code") == "28P01"),
    (
        "protocolo estendido (execute)",
        "protocolo_estendido",
        lambda r: "  execute " in r.get("message", ""),
    ),
)


def _print_samples(by_step: dict[str, list[dict[str, Any]]]) -> None:
    print("\n=== Linhas completas do jsonlog (amostras reais)")
    for label, step, predicate in SAMPLES:
        match = next((r for r in by_step.get(step, []) if predicate(r)), None)
        print(f"\n--- {label} ({step})")
        print(json.dumps(_truncate(match), ensure_ascii=False) if match else "(não encontrada)")


def _print_checks(checks: list[Check]) -> None:
    print("\n=== Verificações")
    for check in checks:
        mark = {True: "OK  ", False: "FALHOU", None: "INFO"}[check.passed]
        print(f"[{mark}] {check.name}: {check.description}\n         -> {check.observed}")


def _save(
    settings: SmokeSettings,
    records: list[dict[str, Any]],
    results: list[StepResult],
    by_step: dict[str, list[dict[str, Any]]],
    checks: list[Check],
) -> None:
    for path in (settings.fixture_path, settings.steps_path, settings.report_path):
        path.parent.mkdir(parents=True, exist_ok=True)
    with settings.fixture_path.open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    steps = {
        r.name: {**asdict(r), "pids": sorted({x["pid"] for x in by_step.get(r.name, [])})}
        for r in results
    }
    settings.steps_path.write_text(
        json.dumps(steps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {"steps": steps, "checks": [asdict(c) for c in checks]}
    settings.report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.info("fixture salva em %s", settings.fixture_path.relative_to(PROJECT_ROOT))
    log.info("relatório salvo em %s", settings.report_path.relative_to(PROJECT_ROOT))


def _truncate(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v[:MAX_FIELD_CHARS] + "...[truncado]")
        if isinstance(v, str) and len(v) > MAX_FIELD_CHARS
        else v
        for k, v in rec.items()
    }


def _first_line(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__

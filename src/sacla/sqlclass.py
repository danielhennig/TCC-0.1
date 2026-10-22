"""Classificação do tipo de comando SQL a partir do texto registrado no log.

Classes: SELECT, INSERT, UPDATE, DELETE, DDL, TCL (controle de transação) e OTHER
(manutenção como REFRESH/VACUUM, SET/SHOW, COPY, comandos inválidos...). Um `WITH` é
classificado pelo comando principal que vem depois das CTEs (ex.: WITH ... UPDATE).
"""

from __future__ import annotations

from collections.abc import Iterator

SELECT, INSERT, UPDATE, DELETE, DDL, TCL, OTHER = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DDL",
    "TCL",
    "OTHER",
)
SQL_CLASSES = (SELECT, INSERT, UPDATE, DELETE, DDL, TCL, OTHER)
DATA_CLASSES = frozenset({SELECT, INSERT, UPDATE, DELETE})

_FIRST_WORD = {
    "SELECT": SELECT,
    "VALUES": SELECT,
    "TABLE": SELECT,
    "INSERT": INSERT,
    "UPDATE": UPDATE,
    "DELETE": DELETE,
    "CREATE": DDL,
    "ALTER": DDL,
    "DROP": DDL,
    "TRUNCATE": DDL,
    "COMMENT": DDL,
    "GRANT": DDL,
    "REVOKE": DDL,
    "BEGIN": TCL,
    "START": TCL,
    "COMMIT": TCL,
    "END": TCL,
    "ROLLBACK": TCL,
    "ABORT": TCL,
    "SAVEPOINT": TCL,
    "RELEASE": TCL,
}
_MAIN_AFTER_WITH = {"SELECT": SELECT, "INSERT": INSERT, "UPDATE": UPDATE, "DELETE": DELETE}


def classify(sql: str) -> str:
    words = _top_level_words(_strip_prefix(sql))
    first = next(words, None)
    if first is None:
        return OTHER
    if first == "WITH":
        # O comando principal é a primeira palavra-chave de DML fora de parênteses.
        for word in words:
            if word in _MAIN_AFTER_WITH:
                return _MAIN_AFTER_WITH[word]
        return OTHER
    return _FIRST_WORD.get(first, OTHER)


def _strip_prefix(sql: str) -> str:
    """Remove espaços, comentários e parênteses iniciais: "(SELECT ...) UNION ..." é SELECT."""
    i, n = 0, len(sql)
    while i < n:
        if sql[i].isspace() or sql[i] == "(":
            i += 1
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        else:
            break
    return sql[i:]


def _top_level_words(sql: str) -> Iterator[str]:
    """Palavras fora de comentários, literais e parênteses, em maiúsculas, sob demanda.

    É um gerador: a classificação para de ler o texto assim que decide a classe.
    """
    depth, i, n = 0, 0, len(sql)
    current: list[str] = []
    while i < n:
        ch = sql[i]
        if ch.isalpha() or ch == "_":
            current.append(ch)
            i += 1
            continue
        if current:
            if depth == 0:
                yield "".join(current).upper()
            current.clear()
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end + 1
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch in ("'", '"'):
            end = sql.find(ch, i + 1)
            while end >= 0 and end + 1 < n and sql[end + 1] == ch:  # aspas duplicadas
                end = sql.find(ch, end + 2)
            i = n if end < 0 else end + 1
        else:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            i += 1
    if current and depth == 0:
        yield "".join(current).upper()

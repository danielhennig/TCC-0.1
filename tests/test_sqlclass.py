"""Classificação do tipo de comando SQL."""

from __future__ import annotations

import pytest

from sacla.sqlclass import classify


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1", "SELECT"),
        ("  select * from film", "SELECT"),
        ("/* comentário */ SELECT 1", "SELECT"),
        ("-- linha\nSELECT 1", "SELECT"),
        ("(SELECT 1) UNION (SELECT 2)", "SELECT"),
        ("VALUES (1), (2)", "SELECT"),
        ("INSERT INTO rental (x) VALUES (1) RETURNING rental_id", "INSERT"),
        ("update customer set activebool = false", "UPDATE"),
        ("DELETE FROM payment WHERE payment_id = 1", "DELETE"),
        ("WITH x AS (SELECT 1) SELECT * FROM x", "SELECT"),
        ("WITH x AS (SELECT id FROM t) UPDATE t SET a = 1 FROM x", "UPDATE"),
        (
            "WITH a AS (DELETE FROM t RETURNING *), b AS (SELECT 1) INSERT INTO u SELECT * FROM a",
            "INSERT",
        ),
        ("SELECT 'DELETE FROM x' AS texto", "SELECT"),
        ("CREATE TABLE t (id int)", "DDL"),
        ("ALTER TABLE t ADD c int", "DDL"),
        ("TRUNCATE payment", "DDL"),
        ("GRANT SELECT ON t TO u", "DDL"),
        ("BEGIN", "TCL"),
        ("COMMIT", "TCL"),
        ("ROLLBACK", "TCL"),
        ("REFRESH MATERIALIZED VIEW rental_by_category", "OTHER"),
        ("SELCT 1", "OTHER"),
        ("SET search_path = public", "OTHER"),
        ("", "OTHER"),
    ],
)
def test_classify(sql: str, expected: str) -> None:
    assert classify(sql) == expected


def test_with_many_ctes_finds_the_main_statement() -> None:
    ctes = ", ".join(f"c{i} AS MATERIALIZED (SELECT {i})" for i in range(100))
    assert classify(f"WITH {ctes} DELETE FROM t") == "DELETE"

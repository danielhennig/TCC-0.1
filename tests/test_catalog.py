"""Catálogo de ações: validação dos templates e sorteio dos parâmetros."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from sacla.config import ConfigError
from simlab.catalog import TYPO_TO, load_catalog

POOLS = "id_pools:\n  customer: [customer, customer_id]\n"
IDS = {"customer": [7, 8, 9]}


def _catalog(tmp_path: Path, actions: str) -> Path:
    path = tmp_path / "queries.yaml"
    path.write_text(POOLS + "actions:\n" + actions, encoding="utf-8")
    return path


def test_transaction_is_wrapped_and_capture_is_deferred(tmp_path: Path) -> None:
    catalog = load_catalog(
        _catalog(
            tmp_path,
            """
  locar:
    transaction: true
    params: {customer_id: {id: customer}}
    statements:
      - sql: INSERT INTO rental (customer_id) VALUES (%(customer_id)s) RETURNING rental_id
        capture: rental_id
      - INSERT INTO payment (rental_id) VALUES (%(rental_id)s)
""",
        )
    )
    statements = catalog.render("locar", random.Random(1), IDS)

    assert [s.sql.split()[0] for s in statements] == ["BEGIN", "INSERT", "INSERT", "COMMIT"]
    assert statements[1].capture == "rental_id"
    assert (
        statements[1].params is not None and statements[1].params["customer_id"] in IDS["customer"]
    )
    assert statements[2].params == {"rental_id": {"$capture": "rental_id"}}


def test_render_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    catalog = load_catalog(
        _catalog(
            tmp_path,
            """
  buscar:
    params: {since: {month: ["2025-01", "2026-06"]}, n: {int: [1, 100]}}
    statements: ["SELECT %(n)s WHERE x >= %(since)s::date"]
""",
        )
    )
    first = catalog.render("buscar", random.Random(42), IDS)
    second = catalog.render("buscar", random.Random(42), IDS)

    assert first == second
    assert first[0].params is not None
    assert (
        first[0].params["since"][:4] in {"2025", "2026"} and first[0].params["since"][-3:] == "-01"
    )


def test_typos_only_outside_transactions(tmp_path: Path) -> None:
    catalog = load_catalog(
        _catalog(
            tmp_path,
            """
  ler:
    statements: [SELECT 1]
  tx:
    transaction: true
    statements: [SELECT 1]
""",
        )
    )
    assert catalog.render("ler", random.Random(0), IDS, typo_probability=1.0)[0].sql.startswith(
        TYPO_TO
    )
    assert catalog.render("tx", random.Random(0), IDS, typo_probability=1.0)[1].sql == "SELECT 1"


@pytest.mark.parametrize(
    ("statements", "message"),
    [
        ("[\"SELECT 1 WHERE a LIKE 'x%'\"]", "literal"),
        ('["SELECT %(desconhecido)s"]', "não é parâmetro"),
    ],
)
def test_invalid_templates_are_rejected(tmp_path: Path, statements: str, message: str) -> None:
    path = _catalog(tmp_path, f"  ruim:\n    statements: {statements}\n")
    with pytest.raises(ConfigError, match=message):
        load_catalog(path)


def test_missing_context_value_is_an_error(tmp_path: Path) -> None:
    catalog = load_catalog(
        _catalog(
            tmp_path,
            """
  dml:
    params: {rows: {context: rows}}
    statements: ["UPDATE t SET x = 1 WHERE id IN (SELECT id FROM t LIMIT %(rows)s)"]
""",
        )
    )
    assert catalog.render("dml", random.Random(0), IDS, {"rows": 5})[0].params == {"rows": 5}
    with pytest.raises(ConfigError, match="contexto"):
        catalog.render("dml", random.Random(0), IDS)

"""O SACLA não pode depender da bancada experimental nem da avaliação.

Se dependesse, ele poderia usar informação da simulação (rótulos, agenda) que não existe
em um log real, o que invalidaria os resultados.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
SACLA_DIR = SRC / "sacla"
FORBIDDEN = ("simlab", "evaluation")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _offenders(package: str, forbidden: tuple[str, ...]) -> set[str]:
    folder = SRC / package
    return {
        f"{path.relative_to(folder)}: {module}"
        for path in folder.rglob("*.py")
        for module in _imported_modules(path)
        if module.split(".")[0] in forbidden
    }


def test_sacla_does_not_import_simlab_or_evaluation() -> None:
    offenders = _offenders("sacla", FORBIDDEN)
    assert not offenders, f"imports proibidos no pacote sacla: {sorted(offenders)}"


def test_sacla_does_not_depend_on_the_panel() -> None:
    offenders = _offenders("sacla", ("dashboard", "streamlit", "altair"))
    assert not offenders, f"o SACLA não pode depender da interface: {sorted(offenders)}"


def test_panel_never_sees_labels() -> None:
    """O painel só lê o estado do monitor: nada da simulação nem da avaliação (rótulos)."""
    offenders = _offenders("dashboard", FORBIDDEN)
    assert not offenders, f"imports proibidos no painel: {sorted(offenders)}"

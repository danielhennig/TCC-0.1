"""Reaproveitamento do conjunto rotulado: a impressão digital da configuração."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace

from evaluation.dataset import settings_fingerprint, with_window
from sacla.config import load_settings

SETTINGS = load_settings()


def test_fingerprint_changes_with_what_changes_the_dataset() -> None:
    base = settings_fingerprint(SETTINGS)
    assert settings_fingerprint(load_settings()) == base
    other_mode = replace(SETTINGS.features, training_history="in_sample")
    assert settings_fingerprint(replace(SETTINGS, features=other_mode)) != base
    assert settings_fingerprint(with_window(SETTINGS, 15)) != base
    # Detector e relatório não mudam o conjunto.
    other_rate = replace(SETTINGS.detector, train_alert_rate=0.5)
    assert settings_fingerprint(replace(SETTINGS, detector=other_rate)) == base


def test_fingerprint_does_not_depend_on_the_hash_seed() -> None:
    """Os conjuntos (frozenset) entram ordenados: a impressão é a mesma entre processos."""
    code = (
        "from evaluation.dataset import settings_fingerprint;"
        "from sacla.config import load_settings;"
        "print(settings_fingerprint(load_settings()))"
    )
    prints = {
        subprocess.run(
            [sys.executable, "-c", code],
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": ":".join(sys.path)},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2")
    }
    assert prints == {settings_fingerprint(SETTINGS)}

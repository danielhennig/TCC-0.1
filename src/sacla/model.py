"""Módulo 3 — Isolation Forest (scikit-learn), o único algoritmo do SACLA.

O score de anomalia é o oposto de `score_samples` (quanto MAIOR, mais anômala a janela). O
parâmetro `contamination` fica em 'auto' porque, no scikit-learn, ele só define o limiar
(`offset_`) e não muda as árvores; o SACLA usa um limiar de score explícito, escolhido fora do
modelo (na validação, ou por quantil do treino quando não há rótulos).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from sacla.features import FEATURE_COLUMNS


@dataclass(frozen=True)
class ModelParams:
    n_estimators: int
    max_samples: int | float | str
    max_features: float
    log1p: bool

    def slug(self) -> str:
        """Identificador curto para nomes de arquivo (ex.: ne200-ms256-mf0.75-log1)."""
        return (
            f"ne{self.n_estimators}-ms{self.max_samples}-mf{self.max_features:g}"
            f"-log{int(self.log1p)}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "max_samples": self.max_samples,
            "max_features": self.max_features,
            "log1p": self.log1p,
        }


def feature_matrix(
    frame: pd.DataFrame, log1p: bool, log1p_features: tuple[str, ...]
) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Matriz na ordem de FEATURE_COLUMNS, com log(1 + x) nas colunas indicadas."""
    data = frame.loc[:, list(FEATURE_COLUMNS)].astype(float).copy()
    if log1p:
        columns = [c for c in log1p_features if c in data.columns]
        if (data[columns] < 0).any().any():
            raise ValueError("log1p aplicado a atributo com valor negativo")
        data[columns] = np.log1p(data[columns])
    return data.to_numpy(dtype=np.float64)


@dataclass
class SaclaModel:
    params: ModelParams
    seed: int
    log1p_features: tuple[str, ...]
    forest: IsolationForest = field(repr=False)
    threshold: float | None = None

    @classmethod
    def fit(
        cls,
        train: pd.DataFrame,
        params: ModelParams,
        seed: int,
        log1p_features: tuple[str, ...],
        n_jobs: int = 1,
    ) -> SaclaModel:
        forest = IsolationForest(
            n_estimators=params.n_estimators,
            max_samples=params.max_samples,
            max_features=params.max_features,
            contamination="auto",
            random_state=seed,
            n_jobs=n_jobs,
        )
        forest.fit(feature_matrix(train, params.log1p, log1p_features))
        return cls(params=params, seed=seed, log1p_features=log1p_features, forest=forest)

    def score(self, frame: pd.DataFrame) -> np.ndarray[Any, np.dtype[np.float64]]:
        """Score de anomalia por janela (maior = mais anômala)."""
        matrix = feature_matrix(frame, self.params.log1p, self.log1p_features)
        return np.asarray(-self.forest.score_samples(matrix), dtype=np.float64)

    def flag(self, frame: pd.DataFrame) -> np.ndarray[Any, np.dtype[np.bool_]]:
        if self.threshold is None:
            raise ValueError("limiar não definido: use with_threshold() antes de sinalizar")
        return np.asarray(self.score(frame) >= self.threshold, dtype=np.bool_)

    def with_threshold(self, threshold: float) -> SaclaModel:
        self.threshold = float(threshold)
        return self

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> SaclaModel:
        model = joblib.load(path)
        if not isinstance(model, SaclaModel):
            raise TypeError(f"{path} não contém um SaclaModel")
        return model

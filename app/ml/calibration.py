"""Calibracao de probabilidades (Fase 8).

Um classificador pode discriminar bem (ROC-AUC alto) mas ter
probabilidades mal calibradas (ex.: prever 0.9 quando a frequencia real
de acerto e 0.6) — o prompt mestre exige avaliar isso explicitamente
antes de aprovar um modelo.

API usada: `sklearn.frozen.FrozenEstimator` + `CalibratedClassifierCV`.
A API antiga `CalibratedClassifierCV(cv="prefit")` foi REMOVIDA no
sklearn 1.9 (confirmado por inspecao do pacote instalado); o
substituto oficial e envolver o estimador ja treinado com
`FrozenEstimator` antes de passar para `CalibratedClassifierCV`.

O modelo base e treinado em `x_fit`/`y_fit` (uma fatia do treino) e a
calibracao e ajustada separadamente em `x_calib`/`y_calib` (uma fatia
DIFERENTE, nunca usada no treino do modelo) para nao vazar dados.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.frozen import FrozenEstimator
from sklearn.pipeline import Pipeline

CalibrationMethod = str  # "sigmoid" ou "isotonic"


@dataclass(frozen=True, slots=True)
class FitCalibrationSplit:
    x_fit: pd.DataFrame
    y_fit: pd.Series
    x_calib: pd.DataFrame
    y_calib: pd.Series


def split_fit_calibration(
    x_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    calibration_fraction: float = 0.2,
) -> FitCalibrationSplit:
    """Divide o treino (ja ordenado cronologicamente por `temporal_train_test_split`)
    reservando a fatia final para calibracao — mantem a ordem temporal,
    nunca embaralha."""
    if not 0.0 < calibration_fraction < 1.0:
        raise ValueError("calibration_fraction deve estar entre 0 e 1 (exclusive).")

    n = len(x_train)
    calib_size = max(1, int(round(n * calibration_fraction)))
    fit_size = n - calib_size
    if fit_size <= 0:
        raise ValueError(
            f"treino pequeno demais para reservar calibracao (n={n}, "
            f"calibration_fraction={calibration_fraction})."
        )

    return FitCalibrationSplit(
        x_fit=x_train.iloc[:fit_size].reset_index(drop=True),
        y_fit=y_train.iloc[:fit_size].reset_index(drop=True),
        x_calib=x_train.iloc[fit_size:].reset_index(drop=True),
        y_calib=y_train.iloc[fit_size:].reset_index(drop=True),
    )


def calibrate_model(
    fitted_pipeline: Pipeline,
    x_calib: pd.DataFrame,
    y_calib: pd.Series,
    *,
    method: CalibrationMethod = "sigmoid",
) -> CalibratedClassifierCV:
    """`fitted_pipeline` deve ja estar treinado (via `app.ml.train.train_model`)
    em dados que NAO incluem `x_calib`/`y_calib`.

    Com um `FrozenEstimator`, o `cv` do `CalibratedClassifierCV` nao controla
    reajuste algum (o estimador congelado ignora `fit`) — ele so precisa ser
    um numero de dobras que a classe minoritaria de `y_calib` consiga
    satisfazer, senao o `StratifiedKFold` interno soa um aviso (ou falha) por
    dobras maiores que a propria classe minoritaria. Por isso calculamos um
    `cv` seguro em vez de usar o padrao fixo (5) as cegas."""
    min_class_count = int(y_calib.value_counts().min())
    if min_class_count < 2:
        raise ValueError(
            "dados de calibracao insuficientes: a classe minoritaria de y_calib "
            f"tem apenas {min_class_count} amostra(s) (minimo exigido: 2). "
            "Aumente o dataset ou 'calibration_fraction'."
        )
    cv = min(5, min_class_count)
    calibrated = CalibratedClassifierCV(FrozenEstimator(fitted_pipeline), method=method, cv=cv)
    calibrated.fit(x_calib, y_calib)
    return calibrated


@dataclass(frozen=True, slots=True)
class CalibrationCurve:
    prob_true: np.ndarray
    """Frequencia observada de label=1 em cada bin de probabilidade prevista."""
    prob_pred: np.ndarray
    """Media das probabilidades previstas em cada bin."""
    n_bins: int


def compute_calibration_curve(
    y_true: np.ndarray, y_prob: np.ndarray, *, n_bins: int = 10
) -> CalibrationCurve:
    prob_true, prob_pred = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy="uniform")
    return CalibrationCurve(prob_true=prob_true, prob_pred=prob_pred, n_bins=n_bins)

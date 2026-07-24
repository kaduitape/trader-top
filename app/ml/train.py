"""Treinamento de modelos (Fase 8).

Modelos disponiveis: regressao logistica (linear, interpretavel via
coeficientes), Random Forest, HistGradientBoosting e XGBoost (arvores,
capturam nao-linearidades, interpretaveis via SHAP em `explainability.py`).

Todas as classes tratam o desbalanceamento entre TARGET_FIRST (label=1) e
STOP_FIRST/TIME_BARRIER (label=0): regressao logistica e Random Forest
aceitam `class_weight="balanced"` nativamente; HistGradientBoosting e
XGBoost nao aceitam esse parametro, entao o peso balanceado e passado via
`sample_weight` no `fit()`.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils.class_weight import compute_sample_weight
from xgboost import XGBClassifier

from app.ml.preprocessing import build_preprocessor

MODEL_NAMES: tuple[str, ...] = (
    "logistic_regression",
    "random_forest",
    "hist_gradient_boosting",
    "xgboost",
)

# Modelos que nao aceitam class_weight nativamente e por isso precisam de
# sample_weight explicito no fit para lidar com o desbalanceamento de classes.
_NEEDS_SAMPLE_WEIGHT: frozenset[str] = frozenset({"hist_gradient_boosting", "xgboost"})


def build_pipeline(model_name: str, *, random_state: int = 42) -> Pipeline:
    if model_name not in MODEL_NAMES:
        raise ValueError(f"modelo desconhecido: {model_name!r}. Opcoes: {MODEL_NAMES}")

    if model_name == "logistic_regression":
        preprocessor = build_preprocessor(scale_numeric=True)
        model = LogisticRegression(
            class_weight="balanced", max_iter=1000, random_state=random_state
        )
    elif model_name == "random_forest":
        preprocessor = build_preprocessor(scale_numeric=False)
        model = RandomForestClassifier(
            n_estimators=300,
            class_weight="balanced",
            random_state=random_state,
            n_jobs=-1,
        )
    elif model_name == "hist_gradient_boosting":
        preprocessor = build_preprocessor(scale_numeric=False)
        model = HistGradientBoostingClassifier(random_state=random_state)
    else:
        preprocessor = build_preprocessor(scale_numeric=False)
        model = XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            eval_metric="logloss",
            random_state=random_state,
        )

    return Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])


def train_model(
    model_name: str,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    *,
    random_state: int = 42,
) -> Pipeline:
    """Ajusta (`fit`) um pipeline completo (pre-processamento + modelo)
    APENAS nos dados de treino recebidos — quem decide o que e treino
    e o chamador (via `app.ml.splits.temporal_train_test_split`)."""
    pipeline = build_pipeline(model_name, random_state=random_state)

    if model_name in _NEEDS_SAMPLE_WEIGHT:
        sample_weight = compute_sample_weight(class_weight="balanced", y=y_train)
        pipeline.fit(x_train, y_train, model__sample_weight=sample_weight)
    else:
        pipeline.fit(x_train, y_train)

    return pipeline


def predict_proba_positive(pipeline: Pipeline, x: pd.DataFrame) -> np.ndarray:
    """Probabilidade prevista da classe positiva (label=1, TARGET_FIRST)."""
    return pipeline.predict_proba(x)[:, 1]

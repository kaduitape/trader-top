"""Explicabilidade (Fase 8): SHAP para modelos em arvore, coeficientes
para regressao logistica.

O prompt mestre (secao 12) exige que o motivo de uma previsao seja
auditavel, nao uma caixa-preta. Para arvores usamos
`shap.TreeExplainer` (exato e rapido para esses modelos); para a
regressao logistica os proprios coeficientes ja sao a explicacao exata
(nao ha aproximacao a fazer).

Observacao empirica (shap 0.52, verificada por inspecao direta antes de
escrever este modulo): `TreeExplainer.shap_values` retorna um array 3D
`(n_amostras, n_features, n_classes)` para `RandomForestClassifier`, mas
um array 2D `(n_amostras, n_features)`, ja referente a classe positiva,
para `HistGradientBoostingClassifier` e `XGBClassifier`. O formato varia
por modelo, entao a normalizacao abaixo trata os dois casos
explicitamente em vez de assumir um formato fixo.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import shap
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

_TREE_MODEL_STEP_TYPES = (
    "RandomForestClassifier",
    "HistGradientBoostingClassifier",
    "XGBClassifier",
)


@dataclass(frozen=True, slots=True)
class FeatureImportance:
    feature: str
    importance: float


def _transformed_feature_names(pipeline: Pipeline) -> list[str]:
    preprocessor = pipeline.named_steps["preprocessor"]
    return list(preprocessor.get_feature_names_out())


def is_tree_model(pipeline: Pipeline) -> bool:
    model = pipeline.named_steps["model"]
    return type(model).__name__ in _TREE_MODEL_STEP_TYPES


def compute_tree_shap_importance(
    pipeline: Pipeline, x: pd.DataFrame, *, max_samples: int = 2000
) -> list[FeatureImportance]:
    """Importancia media |SHAP| por feature, calculada sobre a classe
    positiva (label=1, TARGET_FIRST). Amostra `x` ate `max_samples` linhas
    para manter o custo computacional previsivel em datasets grandes."""
    preprocessor = pipeline.named_steps["preprocessor"]
    model = pipeline.named_steps["model"]
    feature_names = _transformed_feature_names(pipeline)

    sample = x if len(x) <= max_samples else x.sample(n=max_samples, random_state=42)
    transformed = preprocessor.transform(sample)

    explainer = shap.TreeExplainer(model)
    shap_values = np.asarray(explainer.shap_values(transformed))

    # (n_amostras, n_features, n_classes) -> mantem apenas a classe positiva
    # (ultimo indice, label=1); modelos que ja retornam 2D ficam como estao.
    positive_class_values = shap_values[:, :, -1] if shap_values.ndim == 3 else shap_values

    mean_abs = np.abs(positive_class_values).mean(axis=0)
    ranked = sorted(
        (
            FeatureImportance(name, float(value))
            for name, value in zip(feature_names, mean_abs, strict=True)
        ),
        key=lambda item: item.importance,
        reverse=True,
    )
    return ranked


def compute_logistic_regression_coefficients(pipeline: Pipeline) -> list[FeatureImportance]:
    model = pipeline.named_steps["model"]
    if not isinstance(model, LogisticRegression):
        raise TypeError(f"esperado LogisticRegression, recebido {type(model).__name__}")

    feature_names = _transformed_feature_names(pipeline)
    coefficients = model.coef_[0]
    ranked = sorted(
        (
            FeatureImportance(name, float(value))
            for name, value in zip(feature_names, coefficients, strict=True)
        ),
        key=lambda item: abs(item.importance),
        reverse=True,
    )
    return ranked


def explain_model(pipeline: Pipeline, x: pd.DataFrame) -> list[FeatureImportance]:
    """Ponto de entrada unico: escolhe SHAP ou coeficientes conforme o
    tipo do modelo dentro do pipeline."""
    if is_tree_model(pipeline):
        return compute_tree_shap_importance(pipeline, x)
    return compute_logistic_regression_coefficients(pipeline)

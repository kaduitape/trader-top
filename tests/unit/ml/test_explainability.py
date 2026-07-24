import numpy as np
import pandas as pd

from app.ml.datasets import ML_NUMERIC_FEATURE_COLUMNS
from app.ml.explainability import (
    compute_logistic_regression_coefficients,
    compute_tree_shap_importance,
    explain_model,
    is_tree_model,
)
from app.ml.train import train_model

_N_FEATURES = len(ML_NUMERIC_FEATURE_COLUMNS)


def _synthetic_xy(n: int = 150) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.RandomState(0)
    data = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    x = pd.DataFrame(data)
    # A primeira feature numerica determina o rotulo -> deve aparecer com
    # alta importancia no topo do ranking, tanto para SHAP quanto para
    # coeficientes lineares.
    first_column = ML_NUMERIC_FEATURE_COLUMNS[0]
    y = pd.Series((x[first_column] > 0.5).astype(int))
    return x, y


def test_is_tree_model_detects_tree_based_models() -> None:
    x, y = _synthetic_xy()
    assert is_tree_model(train_model("random_forest", x, y)) is True
    assert is_tree_model(train_model("hist_gradient_boosting", x, y)) is True
    assert is_tree_model(train_model("xgboost", x, y)) is True
    assert is_tree_model(train_model("logistic_regression", x, y)) is False


def test_logistic_regression_coefficients_ranked_by_absolute_value() -> None:
    x, y = _synthetic_xy()
    pipeline = train_model("logistic_regression", x, y)

    ranked = compute_logistic_regression_coefficients(pipeline)

    assert len(ranked) == _N_FEATURES + 2  # + 2 colunas one-hot de "session"
    importances = [abs(item.importance) for item in ranked]
    assert importances == sorted(importances, reverse=True)


def test_tree_shap_importance_ranked_and_non_negative() -> None:
    x, y = _synthetic_xy()
    pipeline = train_model("random_forest", x, y)

    ranked = compute_tree_shap_importance(pipeline, x, max_samples=50)

    assert len(ranked) == _N_FEATURES + 2
    importances = [item.importance for item in ranked]
    assert all(value >= 0.0 for value in importances)
    assert importances == sorted(importances, reverse=True)


def test_explain_model_dispatches_correctly_for_each_model_type() -> None:
    x, y = _synthetic_xy()
    for model_name in ("logistic_regression", "random_forest", "hist_gradient_boosting", "xgboost"):
        pipeline = train_model(model_name, x, y)
        ranked = explain_model(pipeline, x)
        assert len(ranked) == _N_FEATURES + 2

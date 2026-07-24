import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from app.ml.datasets import ML_CATEGORICAL_FEATURE_COLUMNS, ML_NUMERIC_FEATURE_COLUMNS
from app.ml.train import MODEL_NAMES, build_pipeline, predict_proba_positive, train_model


def _synthetic_xy(n: int = 200, *, imbalance: bool = True) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.RandomState(0)
    data = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    x = pd.DataFrame(data)
    if imbalance:
        # Minoria explicita (~20%) para exercitar o balanceamento de classes.
        y = pd.Series([1 if i % 5 == 0 else 0 for i in range(n)])
    else:
        y = pd.Series([i % 2 for i in range(n)])
    return x, y


def test_build_pipeline_rejects_unknown_model_name() -> None:
    with pytest.raises(ValueError):
        build_pipeline("not_a_real_model")


@pytest.mark.parametrize("model_name", list(MODEL_NAMES))
def test_build_pipeline_returns_two_step_pipeline(model_name: str) -> None:
    pipeline = build_pipeline(model_name)
    assert isinstance(pipeline, Pipeline)
    assert list(pipeline.named_steps.keys()) == ["preprocessor", "model"]


@pytest.mark.parametrize("model_name", list(MODEL_NAMES))
def test_train_model_fits_and_predicts_probabilities(model_name: str) -> None:
    x, y = _synthetic_xy()
    pipeline = train_model(model_name, x, y)

    probabilities = predict_proba_positive(pipeline, x)
    assert len(probabilities) == len(x)
    assert np.all(probabilities >= 0.0) and np.all(probabilities <= 1.0)


def test_train_model_handles_categorical_column_present() -> None:
    x, y = _synthetic_xy()
    assert "session" in x.columns
    assert set(ML_CATEGORICAL_FEATURE_COLUMNS) == {"session"}
    pipeline = train_model("logistic_regression", x, y)
    assert pipeline.predict(x) is not None

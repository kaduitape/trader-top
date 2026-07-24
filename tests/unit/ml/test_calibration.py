import numpy as np
import pandas as pd
import pytest

from app.ml.calibration import (
    calibrate_model,
    compute_calibration_curve,
    split_fit_calibration,
)
from app.ml.datasets import ML_NUMERIC_FEATURE_COLUMNS
from app.ml.train import train_model


def _synthetic_xy(n: int = 200) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.RandomState(0)
    data = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    x = pd.DataFrame(data)
    y = pd.Series([1 if i % 3 == 0 else 0 for i in range(n)])
    return x, y


def test_split_fit_calibration_preserves_row_count_and_order() -> None:
    x, y = _synthetic_xy(100)
    split = split_fit_calibration(x, y, calibration_fraction=0.2)

    assert len(split.x_fit) == 80
    assert len(split.x_calib) == 20
    assert len(split.x_fit) + len(split.x_calib) == len(x)
    pd.testing.assert_frame_equal(split.x_fit, x.iloc[:80].reset_index(drop=True))
    pd.testing.assert_frame_equal(split.x_calib, x.iloc[80:].reset_index(drop=True))


def test_split_fit_calibration_raises_for_invalid_fraction() -> None:
    x, y = _synthetic_xy(10)
    with pytest.raises(ValueError):
        split_fit_calibration(x, y, calibration_fraction=0.0)
    with pytest.raises(ValueError):
        split_fit_calibration(x, y, calibration_fraction=1.0)


def test_split_fit_calibration_raises_when_too_small() -> None:
    x, y = _synthetic_xy(2)
    with pytest.raises(ValueError):
        split_fit_calibration(x, y, calibration_fraction=0.99)


@pytest.mark.parametrize("method", ["sigmoid", "isotonic"])
def test_calibrate_model_produces_valid_probabilities(method: str) -> None:
    x, y = _synthetic_xy(200)
    split = split_fit_calibration(x, y, calibration_fraction=0.3)

    base_pipeline = train_model("logistic_regression", split.x_fit, split.y_fit)
    calibrated = calibrate_model(base_pipeline, split.x_calib, split.y_calib, method=method)

    probabilities = calibrated.predict_proba(x)[:, 1]
    assert len(probabilities) == len(x)
    assert np.all(probabilities >= 0.0) and np.all(probabilities <= 1.0)


def test_compute_calibration_curve_returns_bins() -> None:
    rng = np.random.RandomState(0)
    y_true = rng.randint(0, 2, size=200)
    y_prob = rng.rand(200)

    curve = compute_calibration_curve(y_true, y_prob, n_bins=5)

    assert curve.n_bins == 5
    assert len(curve.prob_true) == len(curve.prob_pred)
    assert np.all(curve.prob_true >= 0.0) and np.all(curve.prob_true <= 1.0)

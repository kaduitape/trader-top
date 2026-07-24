import numpy as np
import pandas as pd
import pytest

from app.backtesting.costs import CostModel
from app.ml.datasets import ML_CATEGORICAL_FEATURE_COLUMNS, ML_NUMERIC_FEATURE_COLUMNS
from app.ml.walk_forward import expanding_window_bounds, run_ml_walk_forward

_FEATURE_COLUMNS = list(ML_NUMERIC_FEATURE_COLUMNS) + list(ML_CATEGORICAL_FEATURE_COLUMNS)
_POINT = 0.0001


def _synthetic_dataset(n: int = 300) -> pd.DataFrame:
    rng = np.random.RandomState(0)
    data: dict[str, object] = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    data["label"] = [i % 2 for i in range(n)]
    data["signal_time"] = pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC")
    data["direction"] = ["LONG" if i % 2 == 0 else "SHORT" for i in range(n)]
    data["entry_price"] = [1.1000] * n
    data["entry_spread"] = [2] * n
    data["exit_price"] = [1.1000 + 0.0005 * (1 if i % 2 == 0 else -1) for i in range(n)]
    data["regime_trend"] = ["UP"] * n
    return pd.DataFrame(data)


def test_expanding_window_bounds_divides_into_growing_train_blocks() -> None:
    bounds = expanding_window_bounds(300, n_windows=4)
    assert bounds == [(60, 60, 120), (120, 120, 180), (180, 180, 240), (240, 240, 300)]


def test_expanding_window_bounds_rejects_invalid_n_windows() -> None:
    with pytest.raises(ValueError):
        expanding_window_bounds(100, n_windows=0)


def test_expanding_window_bounds_rejects_insufficient_rows() -> None:
    with pytest.raises(ValueError):
        expanding_window_bounds(3, n_windows=5)


def test_run_ml_walk_forward_produces_one_report_per_window() -> None:
    dataset = _synthetic_dataset(300)
    report = run_ml_walk_forward(
        dataset,
        model_name="logistic_regression",
        n_windows=4,
        feature_columns=_FEATURE_COLUMNS,
        cost_model=CostModel(use_recorded_spread=False),
        point=_POINT,
    )

    assert len(report.windows) == 4
    for window in report.windows:
        assert window.test_rows > 0
        assert window.train_rows > 0
        assert window.classification_metrics.num_samples == window.test_rows

    assert isinstance(report.profitable_window_ratio, float)
    assert isinstance(report.mean_expectancy_after_costs, float)
    assert isinstance(report.std_expectancy_after_costs, float)


def test_run_ml_walk_forward_windows_are_chronological_and_non_overlapping() -> None:
    dataset = _synthetic_dataset(300)
    report = run_ml_walk_forward(
        dataset,
        model_name="logistic_regression",
        n_windows=3,
        feature_columns=_FEATURE_COLUMNS,
        cost_model=CostModel(use_recorded_spread=False),
        point=_POINT,
    )

    for previous, current in zip(report.windows, report.windows[1:], strict=False):
        assert previous.test_end < current.test_start


def test_run_ml_walk_forward_skips_windows_with_insufficient_data() -> None:
    dataset = _synthetic_dataset(300)
    # Pede janelas demais para o tamanho do dataset combinado com um
    # embargo grande — algumas janelas ficam pequenas demais para reservar
    # calibracao e devem ser puladas, nao fabricadas.
    report = run_ml_walk_forward(
        dataset,
        model_name="logistic_regression",
        n_windows=8,
        feature_columns=_FEATURE_COLUMNS,
        embargo_samples=5,
        calibration_fraction=0.4,
        cost_model=CostModel(use_recorded_spread=False),
        point=_POINT,
    )
    assert len(report.windows) <= 8


def test_run_ml_walk_forward_returns_neutral_report_when_windows_too_small_to_train() -> None:
    # 10 linhas passam pela checagem de tamanho minimo de `expanding_window_bounds`
    # (n >= n_windows + 1), mas cada janela individual fica pequena demais
    # para reservar treino + calibracao — todas sao puladas internamente,
    # nunca fabricadas, resultando num relatorio vazio (nao um erro).
    dataset = _synthetic_dataset(10)
    report = run_ml_walk_forward(
        dataset,
        model_name="logistic_regression",
        n_windows=8,
        feature_columns=_FEATURE_COLUMNS,
        cost_model=CostModel(use_recorded_spread=False),
        point=_POINT,
    )
    assert report.windows == []
    assert report.profitable_window_ratio == 0.0


def test_run_ml_walk_forward_raises_when_dataset_smaller_than_n_windows_plus_one() -> None:
    dataset = _synthetic_dataset(5)
    with pytest.raises(ValueError):
        run_ml_walk_forward(
            dataset,
            model_name="logistic_regression",
            n_windows=8,
            feature_columns=_FEATURE_COLUMNS,
            cost_model=CostModel(use_recorded_spread=False),
            point=_POINT,
        )

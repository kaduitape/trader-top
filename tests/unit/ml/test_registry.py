import numpy as np
import pandas as pd
import pytest

from app.ml.datasets import ML_NUMERIC_FEATURE_COLUMNS
from app.ml.registry import ModelRegistry, ModelRegistryError
from app.ml.train import train_model


def _synthetic_xy(n: int = 60) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.RandomState(0)
    data = {column: rng.rand(n) for column in ML_NUMERIC_FEATURE_COLUMNS}
    data["session"] = ["LONDON" if i % 2 == 0 else "NEW_YORK" for i in range(n)]
    x = pd.DataFrame(data)
    y = pd.Series([i % 2 for i in range(n)])
    return x, y


def _fitted_pipeline():
    x, y = _synthetic_xy()
    return train_model("logistic_regression", x, y), x


def test_register_creates_manifest_and_artifact(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    pipeline, x = _fitted_pipeline()

    version = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        feature_columns=list(x.columns),
        metrics={"accuracy": 0.5},
    )

    assert (tmp_path / f"{version}.joblib").exists()
    assert (tmp_path / "manifest.json").exists()
    assert registry.current_version() == version

    entry = registry.get_entry(version)
    assert entry.model_name == "logistic_regression"
    assert entry.symbol == "EURUSD"
    assert entry.approved is False


def test_load_returns_a_usable_pipeline(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    pipeline, x = _fitted_pipeline()
    version = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="ema_crossover_baseline",
        feature_columns=list(x.columns),
        metrics={},
    )

    loaded = registry.load(version)
    predictions = loaded.predict(x)
    assert len(predictions) == len(x)


def test_load_uses_current_pointer_when_version_omitted(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    pipeline, x = _fitted_pipeline()
    version = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="s",
        feature_columns=list(x.columns),
        metrics={},
    )

    loaded = registry.load()
    assert loaded is not None
    assert registry.current_version() == version


def test_load_raises_when_no_current_version_set(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    with pytest.raises(ModelRegistryError):
        registry.load()


def test_rollback_repoints_current_without_deleting_artifacts(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    pipeline, x = _fitted_pipeline()

    v1 = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="s",
        feature_columns=list(x.columns),
        metrics={},
    )
    v2 = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="s",
        feature_columns=list(x.columns),
        metrics={},
    )
    assert registry.current_version() == v2

    registry.set_current(v1)
    assert registry.current_version() == v1
    # Ambos os artefatos continuam disponiveis apos o rollback.
    assert registry.load(v1) is not None
    assert registry.load(v2) is not None
    assert len(registry.list_versions()) == 2


def test_set_current_raises_for_unknown_version(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    with pytest.raises(ModelRegistryError):
        registry.set_current("does-not-exist")


def test_test_set_round_trip(tmp_path) -> None:
    registry = ModelRegistry(tmp_path)
    pipeline, x = _fitted_pipeline()
    version = registry.register(
        pipeline,
        model_name="logistic_regression",
        symbol="EURUSD",
        timeframe="M1",
        strategy_name="s",
        feature_columns=list(x.columns),
        metrics={},
    )

    test_set = x.copy()
    test_set["signal_time"] = pd.date_range("2026-01-01", periods=len(x), freq="min", tz="UTC")
    test_set["label"] = 0

    registry.save_test_set(version, test_set)
    reloaded = registry.load_test_set(version)

    assert len(reloaded) == len(test_set)
    assert "signal_time" in reloaded.columns

import pandas as pd
import pytest

from app.ml.splits import temporal_train_test_split


def _dataset(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "signal_time": pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC"),
            "label": [i % 2 for i in range(n)],
        }
    )


def test_split_preserves_chronological_order_and_sizes() -> None:
    dataset = _dataset(100)
    split = temporal_train_test_split(dataset, test_fraction=0.3, embargo_samples=5)

    assert len(split.test) == 30
    assert split.train_end < split.test_start
    assert list(split.train["signal_time"]) == sorted(split.train["signal_time"])
    assert list(split.test["signal_time"]) == sorted(split.test["signal_time"])


def test_embargo_drops_rows_immediately_before_test_split() -> None:
    dataset = _dataset(100)
    split = temporal_train_test_split(dataset, test_fraction=0.3, embargo_samples=5)

    # 100 - 30 (test) = split_point 70; embargo remove as 5 anteriores -> treino tem 65.
    assert len(split.train) == 65
    assert split.embargo_samples_dropped == 5


def test_shuffled_input_is_sorted_before_splitting() -> None:
    dataset = _dataset(20).sample(frac=1.0, random_state=1).reset_index(drop=True)
    split = temporal_train_test_split(dataset, test_fraction=0.25, embargo_samples=2)

    assert list(split.train["signal_time"]) == sorted(split.train["signal_time"])
    assert split.train["signal_time"].iloc[-1] < split.test["signal_time"].iloc[0]


def test_raises_for_empty_dataset() -> None:
    with pytest.raises(ValueError):
        temporal_train_test_split(pd.DataFrame(columns=["signal_time", "label"]))


def test_raises_for_invalid_test_fraction() -> None:
    with pytest.raises(ValueError):
        temporal_train_test_split(_dataset(10), test_fraction=0.0)
    with pytest.raises(ValueError):
        temporal_train_test_split(_dataset(10), test_fraction=1.0)


def test_raises_when_dataset_too_small_for_embargo() -> None:
    with pytest.raises(ValueError):
        temporal_train_test_split(_dataset(5), test_fraction=0.5, embargo_samples=10)

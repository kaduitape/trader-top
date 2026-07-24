import numpy as np
import pandas as pd
import pytest

from app.monitoring.drift import (
    DriftSeverity,
    classify_psi,
    compute_psi,
    detect_feature_drift,
    detect_metric_drift,
)


def test_identical_distributions_have_near_zero_psi() -> None:
    rng = np.random.RandomState(0)
    reference = rng.normal(0, 1, 2000)
    current = rng.normal(0, 1, 2000)

    psi = compute_psi(reference, current)

    assert psi < 0.05
    assert classify_psi(psi) == DriftSeverity.NONE


def test_shifted_distribution_has_high_psi() -> None:
    rng = np.random.RandomState(0)
    reference = rng.normal(0, 1, 2000)
    shifted = rng.normal(2.0, 1, 2000)

    psi = compute_psi(reference, shifted)

    assert psi > 0.25
    assert classify_psi(psi) == DriftSeverity.CRITICAL


def test_moderate_shift_is_warning_level() -> None:
    rng = np.random.RandomState(0)
    reference = rng.normal(0, 1, 3000)
    moderately_shifted = rng.normal(0.3, 1, 3000)

    psi = compute_psi(reference, moderately_shifted)

    assert 0.10 <= psi < 0.25
    assert classify_psi(psi) == DriftSeverity.WARNING


def test_empty_samples_return_zero_psi() -> None:
    assert compute_psi(np.array([]), np.array([1.0, 2.0])) == 0.0
    assert compute_psi(np.array([1.0, 2.0]), np.array([])) == 0.0


def test_reference_without_variance_returns_zero_psi() -> None:
    reference = np.array([5.0, 5.0, 5.0, 5.0])
    current = np.array([1.0, 2.0, 3.0, 4.0])
    assert compute_psi(reference, current) == 0.0


def test_nan_values_are_ignored() -> None:
    rng = np.random.RandomState(0)
    reference = rng.normal(0, 1, 1000)
    current = np.concatenate([rng.normal(0, 1, 1000), [np.nan, np.nan]])

    psi = compute_psi(reference, current)
    assert psi < 0.05


def test_classify_psi_boundaries() -> None:
    assert classify_psi(0.0) == DriftSeverity.NONE
    assert classify_psi(0.099) == DriftSeverity.NONE
    assert classify_psi(0.10) == DriftSeverity.WARNING
    assert classify_psi(0.249) == DriftSeverity.WARNING
    assert classify_psi(0.25) == DriftSeverity.CRITICAL


def test_detect_feature_drift_returns_one_result_per_shared_column() -> None:
    rng = np.random.RandomState(0)
    reference_df = pd.DataFrame(
        {"feature_a": rng.normal(0, 1, 500), "feature_b": rng.normal(0, 1, 500)}
    )
    current_df = pd.DataFrame(
        {
            "feature_a": rng.normal(0, 1, 500),  # sem drift
            "feature_b": rng.normal(3, 1, 500),  # com drift forte
            "feature_c": rng.normal(0, 1, 500),  # nao existe na referencia
        }
    )

    results = detect_feature_drift(
        reference_df, current_df, feature_columns=["feature_a", "feature_b", "feature_c"]
    )

    by_name = {r.feature: r for r in results}
    assert set(by_name) == {"feature_a", "feature_b"}  # feature_c pulada
    assert by_name["feature_a"].severity == DriftSeverity.NONE
    assert by_name["feature_b"].severity == DriftSeverity.CRITICAL


@pytest.mark.parametrize(
    ("higher_is_better", "baseline", "current", "expected_severity"),
    [
        (True, 0.10, 0.10, DriftSeverity.NONE),  # expectativa igual
        (True, 0.10, 0.05, DriftSeverity.CRITICAL),  # expectativa caiu 50%
        (True, 0.10, 0.12, DriftSeverity.NONE),  # expectativa melhorou
        (False, 0.05, 0.05, DriftSeverity.NONE),  # brier igual
        (False, 0.05, 0.10, DriftSeverity.CRITICAL),  # brier piorou 100%
        (False, 0.05, 0.04, DriftSeverity.NONE),  # brier melhorou
    ],
)
def test_detect_metric_drift_classifies_correctly(
    higher_is_better: bool, baseline: float, current: float, expected_severity: DriftSeverity
) -> None:
    result = detect_metric_drift(
        "test_metric", baseline, current, higher_is_better=higher_is_better
    )
    assert result.severity == expected_severity


def test_detect_metric_drift_handles_zero_baseline() -> None:
    result = detect_metric_drift("m", 0.0, 0.0, higher_is_better=True)
    assert result.degradation_pct == 0.0
    assert result.severity == DriftSeverity.NONE

    result_nonzero = detect_metric_drift("m", 0.0, 5.0, higher_is_better=True)
    assert result_nonzero.degradation_pct == 100.0


def test_detect_metric_drift_degradation_direction_is_always_positive_when_worse() -> None:
    higher_is_better_worse = detect_metric_drift("m", 100.0, 50.0, higher_is_better=True)
    lower_is_better_worse = detect_metric_drift("m", 0.05, 0.10, higher_is_better=False)

    assert higher_is_better_worse.degradation_pct > 0
    assert lower_is_better_worse.degradation_pct > 0

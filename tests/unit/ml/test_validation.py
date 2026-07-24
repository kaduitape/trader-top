import numpy as np
import pandas as pd
import pytest

from app.backtesting.costs import CostModel
from app.ml.validation import compute_classification_metrics, compute_trading_metrics

_POINT = 0.0001


def _trade_dataset() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "direction": "LONG",
                "entry_price": 1.1000,
                "entry_spread": 0,
                "exit_price": 1.1050,  # +50 pontos
                "regime_trend": "UP",
            },
            {
                "direction": "LONG",
                "entry_price": 1.1000,
                "entry_spread": 0,
                "exit_price": 1.0980,  # -20 pontos
                "regime_trend": "DOWN",
            },
            {
                "direction": "SHORT",
                "entry_price": 1.1000,
                "entry_spread": 0,
                "exit_price": 1.0950,  # +50 pontos (short lucra quando preco cai)
                "regime_trend": "DOWN",
            },
        ]
    )


def test_classification_metrics_perfect_separation() -> None:
    y_true = np.array([0, 0, 1, 1])
    y_prob = np.array([0.05, 0.1, 0.9, 0.95])

    metrics = compute_classification_metrics(y_true, y_prob, threshold=0.5)

    assert metrics.num_samples == 4
    assert metrics.num_positive == 2
    assert metrics.roc_auc == 1.0
    assert metrics.pr_auc == 1.0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0


def test_classification_metrics_empty_input_returns_neutral_values() -> None:
    metrics = compute_classification_metrics(np.array([]), np.array([]))
    assert metrics.num_samples == 0
    assert metrics.num_positive == 0
    assert metrics.roc_auc is None
    assert metrics.pr_auc is None


def test_classification_metrics_single_class_has_no_auc() -> None:
    y_true = np.array([0, 0, 0, 0])
    y_prob = np.array([0.1, 0.2, 0.3, 0.4])

    metrics = compute_classification_metrics(y_true, y_prob)

    assert metrics.roc_auc is None
    assert metrics.pr_auc is None
    assert metrics.log_loss_value is None
    assert metrics.brier_score is not None


def test_trading_metrics_computes_net_pnl_without_costs() -> None:
    dataset = _trade_dataset()
    probabilities = np.array([0.9, 0.9, 0.9])  # todas selecionadas
    zero_cost = CostModel(use_recorded_spread=False, slippage_points=0.0)

    metrics = compute_trading_metrics(
        dataset, probabilities, threshold=0.5, cost_model=zero_cost, point=_POINT
    )

    assert metrics.num_trades == 3
    # (1.1050-1.1000) + (1.0980-1.1000) + (1.1000-1.0950)*-1*(-1) ->
    # long +0.0050, long -0.0020, short +0.0050 (preco caiu, sign=-1: (0.0950-0.1000... )
    assert metrics.net_profit_after_costs == pytest.approx(0.0050 - 0.0020 + 0.0050)
    assert metrics.win_rate == pytest.approx(2 / 3)


def test_trading_metrics_threshold_filters_rows() -> None:
    dataset = _trade_dataset()
    probabilities = np.array([0.9, 0.1, 0.9])  # segunda linha abaixo do limiar
    zero_cost = CostModel(use_recorded_spread=False, slippage_points=0.0)

    metrics = compute_trading_metrics(
        dataset, probabilities, threshold=0.5, cost_model=zero_cost, point=_POINT
    )

    assert metrics.num_trades == 2
    assert metrics.result_by_regime_trend == {
        "UP": pytest.approx(0.0050),
        "DOWN": pytest.approx(0.0050),
    }


def test_trading_metrics_commission_reduces_net_profit() -> None:
    dataset = _trade_dataset().iloc[[0]]
    probabilities = np.array([1.0])
    cost_model = CostModel(use_recorded_spread=False, slippage_points=0.0, commission_per_lot=0.001)

    metrics = compute_trading_metrics(
        dataset, probabilities, threshold=0.5, cost_model=cost_model, point=_POINT, volume=1.0
    )

    assert metrics.net_profit_after_costs == pytest.approx(0.0050 - 0.001)


def test_trading_metrics_empty_selection_returns_zeroed_metrics() -> None:
    dataset = _trade_dataset()
    probabilities = np.array([0.1, 0.1, 0.1])
    metrics = compute_trading_metrics(
        dataset, probabilities, threshold=0.5, cost_model=CostModel(), point=_POINT
    )
    assert metrics.num_trades == 0
    assert metrics.net_profit_after_costs == 0.0
    assert metrics.profit_factor is None

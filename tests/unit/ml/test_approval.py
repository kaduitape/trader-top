import pandas as pd

from app.ml.approval import evaluate_approval
from app.ml.validation import ClassificationMetrics, TradingMetrics
from app.ml.walk_forward import MLWalkForwardReport, MLWalkForwardWindow

_T0 = pd.Timestamp("2026-01-01", tz="UTC")


def _classification_metrics(*, brier_score: float | None = 0.1) -> ClassificationMetrics:
    return ClassificationMetrics(
        num_samples=100,
        num_positive=50,
        precision=0.6,
        recall=0.6,
        f1=0.6,
        roc_auc=0.65,
        pr_auc=0.6,
        brier_score=brier_score,
        log_loss_value=0.5,
    )


def _trading_metrics(*, num_trades: int, expectancy: float) -> TradingMetrics:
    return TradingMetrics(
        num_trades=num_trades,
        win_rate=0.55,
        expectancy_after_costs=expectancy,
        profit_factor=1.2,
        net_profit_after_costs=expectancy * num_trades,
        expectancy_ci_low=None,
        expectancy_ci_high=None,
        result_by_regime_trend={},
    )


def _window(
    index: int, *, num_trades: int, expectancy: float, brier_score: float | None = 0.1
) -> MLWalkForwardWindow:
    return MLWalkForwardWindow(
        index=index,
        train_rows=100,
        test_rows=num_trades,
        test_start=_T0 + pd.Timedelta(days=index),
        test_end=_T0 + pd.Timedelta(days=index, hours=1),
        classification_metrics=_classification_metrics(brier_score=brier_score),
        trading_metrics=_trading_metrics(num_trades=num_trades, expectancy=expectancy),
    )


def test_evaluate_approval_no_windows_fails_immediately() -> None:
    report = MLWalkForwardReport(
        windows=[],
        profitable_window_ratio=0.0,
        mean_expectancy_after_costs=0.0,
        std_expectancy_after_costs=0.0,
    )
    approval = evaluate_approval(report)

    assert approval.all_passed is False
    assert len(approval.criteria) == 1
    assert approval.criteria[0].name == "janelas_disponiveis"


def test_evaluate_approval_all_criteria_pass_for_a_healthy_report() -> None:
    windows = [_window(i, num_trades=20, expectancy=5.0) for i in range(4)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=5.0,
        std_expectancy_after_costs=0.5,
    )
    approval = evaluate_approval(report)

    assert approval.all_passed is True
    assert all(c.passed for c in approval.criteria)


def test_evaluate_approval_fails_trade_count_criterion_when_too_few_trades() -> None:
    windows = [_window(0, num_trades=5, expectancy=5.0)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=5.0,
        std_expectancy_after_costs=0.0,
    )
    approval = evaluate_approval(report, min_trades_total=30)

    criterion = next(c for c in approval.criteria if c.name == "numero_de_trades_suficiente")
    assert criterion.passed is False
    assert approval.all_passed is False


def test_evaluate_approval_fails_edge_criterion_when_expectancy_negative() -> None:
    windows = [_window(i, num_trades=20, expectancy=-2.0) for i in range(3)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=0.0,
        mean_expectancy_after_costs=-2.0,
        std_expectancy_after_costs=0.5,
    )
    approval = evaluate_approval(report)

    criterion = next(c for c in approval.criteria if c.name == "edge_positivo_apos_custos")
    assert criterion.passed is False


def test_evaluate_approval_fails_stability_criterion_when_few_windows_profitable() -> None:
    windows = [_window(i, num_trades=20, expectancy=5.0) for i in range(4)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=0.25,
        mean_expectancy_after_costs=5.0,
        std_expectancy_after_costs=1.0,
    )
    approval = evaluate_approval(report, min_profitable_window_ratio=0.6)

    criterion = next(c for c in approval.criteria if c.name == "estavel_entre_periodos")
    assert criterion.passed is False


def test_evaluate_approval_fails_erratic_criterion_when_std_too_high_relative_to_mean() -> None:
    windows = [_window(i, num_trades=20, expectancy=5.0) for i in range(4)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=1.0,
        std_expectancy_after_costs=10.0,  # desvio 10x maior que a media
    )
    approval = evaluate_approval(report, max_expectancy_relative_std=2.0)

    criterion = next(
        c for c in approval.criteria if c.name == "nao_dependente_de_janela_excepcional"
    )
    assert criterion.passed is False


def test_evaluate_approval_erratic_criterion_fails_when_mean_expectancy_is_zero() -> None:
    windows = [_window(i, num_trades=20, expectancy=0.0) for i in range(4)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=0.0,
        std_expectancy_after_costs=1.0,
    )
    approval = evaluate_approval(report)

    criterion = next(
        c for c in approval.criteria if c.name == "nao_dependente_de_janela_excepcional"
    )
    assert criterion.passed is False


def test_evaluate_approval_fails_calibration_criterion_when_brier_score_too_high() -> None:
    windows = [_window(i, num_trades=20, expectancy=5.0, brier_score=0.4) for i in range(3)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=5.0,
        std_expectancy_after_costs=0.5,
    )
    approval = evaluate_approval(report, max_mean_brier_score=0.25)

    criterion = next(
        c for c in approval.criteria if c.name == "probabilidades_razoavelmente_calibradas"
    )
    assert criterion.passed is False


def test_evaluate_approval_calibration_criterion_fails_when_no_brier_score_available() -> None:
    windows = [_window(i, num_trades=20, expectancy=5.0, brier_score=None) for i in range(3)]
    report = MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=1.0,
        mean_expectancy_after_costs=5.0,
        std_expectancy_after_costs=0.5,
    )
    approval = evaluate_approval(report)

    criterion = next(
        c for c in approval.criteria if c.name == "probabilidades_razoavelmente_calibradas"
    )
    assert criterion.passed is False

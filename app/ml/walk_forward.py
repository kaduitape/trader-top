"""Walk-forward para o pipeline de ML (Fase 9).

Complementa `app.ml.splits.temporal_train_test_split` (uma única
divisão) com múltiplas janelas EXPANSIVAS: o treino sempre começa no
início do dataset e cresce a cada janela; o teste é sempre a próxima
fatia cronológica (nunca sobreposta ao treino nem a outro teste). Cada
janela repete a mesma disciplina da Fase 8 — cronológico, com embargo,
calibração ajustada numa fatia separada do treino — para que "estável
entre períodos" (um dos 5 critérios de aprovação) possa ser julgado com
dados reais em vez de uma única divisão.

Uma janela é simplesmente pulada (nunca fabricada) quando não há dados
suficientes para treino, calibração ou teste — por exemplo, quando a
classe minoritária da calibração tem menos de 2 amostras nessa janela
específica.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.backtesting.costs import CostModel
from app.ml.calibration import calibrate_model, split_fit_calibration
from app.ml.train import train_model
from app.ml.validation import (
    ClassificationMetrics,
    TradingMetrics,
    compute_classification_metrics,
    compute_trading_metrics,
)


@dataclass(frozen=True, slots=True)
class MLWalkForwardWindow:
    index: int
    train_rows: int
    test_rows: int
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    classification_metrics: ClassificationMetrics
    trading_metrics: TradingMetrics


@dataclass(frozen=True, slots=True)
class MLWalkForwardReport:
    windows: list[MLWalkForwardWindow]
    profitable_window_ratio: float
    mean_expectancy_after_costs: float
    std_expectancy_after_costs: float


def expanding_window_bounds(n: int, *, n_windows: int) -> list[tuple[int, int, int]]:
    """Retorna, para cada janela, `(train_end, test_start, test_end)` —
    treino sempre `[0, train_end)`, teste sempre `[test_start, test_end)`.
    O dataset é dividido em `n_windows + 1` blocos iguais: o primeiro bloco
    é o treino inicial; cada bloco seguinte é o teste de uma janela E passa
    a fazer parte do treino da janela seguinte (daí "expansiva")."""
    if n_windows < 1:
        raise ValueError("n_windows deve ser >= 1.")
    total_blocks = n_windows + 1
    if n < total_blocks:
        raise ValueError(
            f"dados insuficientes ({n} linha(s)) para {n_windows} janela(s) expansivas "
            f"(mínimo: {total_blocks})."
        )

    block_size = n // total_blocks
    boundaries = [block_size * i for i in range(total_blocks)] + [n]

    return [(boundaries[w + 1], boundaries[w + 1], boundaries[w + 2]) for w in range(n_windows)]


def run_ml_walk_forward(
    dataset: pd.DataFrame,
    *,
    model_name: str,
    n_windows: int,
    feature_columns: list[str],
    embargo_samples: int = 5,
    calibration_fraction: float = 0.2,
    calibration_method: str = "sigmoid",
    threshold: float = 0.5,
    cost_model: CostModel,
    point: float,
    volume: float = 1.0,
    time_column: str = "signal_time",
) -> MLWalkForwardReport:
    sorted_df = dataset.sort_values(time_column).reset_index(drop=True)
    n = len(sorted_df)
    bounds = expanding_window_bounds(n, n_windows=n_windows)

    windows: list[MLWalkForwardWindow] = []
    for idx, (train_end, test_start, test_end) in enumerate(bounds):
        embargoed_train_end = max(0, train_end - embargo_samples)
        train_df = sorted_df.iloc[:embargoed_train_end]
        test_df = sorted_df.iloc[test_start:test_end]

        if train_df.empty or test_df.empty:
            continue

        try:
            fit_calib = split_fit_calibration(
                train_df[feature_columns],
                train_df["label"],
                calibration_fraction=calibration_fraction,
            )
            base_pipeline = train_model(model_name, fit_calib.x_fit, fit_calib.y_fit)
            calibrated = calibrate_model(
                base_pipeline, fit_calib.x_calib, fit_calib.y_calib, method=calibration_method
            )
        except ValueError:
            # Treino/calibracao pequenos demais ou classe minoritaria
            # insuficiente nesta janela especifica — pulada, nunca fabricada.
            continue

        x_test = test_df[feature_columns]
        y_test = test_df["label"].to_numpy()
        y_prob = calibrated.predict_proba(x_test)[:, 1]

        classification_metrics = compute_classification_metrics(y_test, y_prob, threshold=threshold)
        trading_metrics = compute_trading_metrics(
            test_df,
            y_prob,
            threshold=threshold,
            cost_model=cost_model,
            point=point,
            volume=volume,
        )

        windows.append(
            MLWalkForwardWindow(
                index=idx,
                train_rows=len(train_df),
                test_rows=len(test_df),
                test_start=test_df[time_column].iloc[0],
                test_end=test_df[time_column].iloc[-1],
                classification_metrics=classification_metrics,
                trading_metrics=trading_metrics,
            )
        )

    if not windows:
        return MLWalkForwardReport(
            windows=[],
            profitable_window_ratio=0.0,
            mean_expectancy_after_costs=0.0,
            std_expectancy_after_costs=0.0,
        )

    expectancies = np.array([w.trading_metrics.expectancy_after_costs for w in windows])

    return MLWalkForwardReport(
        windows=windows,
        profitable_window_ratio=float(np.mean(expectancies >= 0)),
        mean_expectancy_after_costs=float(expectancies.mean()),
        std_expectancy_after_costs=(
            float(expectancies.std(ddof=1)) if len(expectancies) >= 2 else 0.0
        ),
    )

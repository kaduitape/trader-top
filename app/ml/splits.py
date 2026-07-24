"""Divisao temporal treino/teste com periodo de embargo (Fase 8).

Walk-forward completo (multiplas janelas deslizantes) e a Fase 9 — aqui e
uma UNICA divisao cronologica. O embargo remove as ultimas `embargo_samples`
amostras do treino (as mais proximas do teste) para reduzir a chance de uma
amostra de treino cuja janela de barreira tripla se estenda para dentro do
periodo de teste. Nunca embaralha por aleatoriedade — a ordem e sempre
cronologica, exigencia explicita do prompt mestre.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    train: pd.DataFrame
    test: pd.DataFrame
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    embargo_samples_dropped: int


def temporal_train_test_split(
    dataset: pd.DataFrame,
    *,
    test_fraction: float = 0.3,
    embargo_samples: int = 5,
    time_column: str = "signal_time",
) -> TemporalSplit:
    if dataset.empty:
        raise ValueError("dataset vazio: nao ha como dividir treino/teste.")
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction deve estar entre 0 e 1 (exclusive).")

    sorted_df = dataset.sort_values(time_column).reset_index(drop=True)
    n = len(sorted_df)
    test_size = max(1, int(round(n * test_fraction)))
    split_point = n - test_size

    train_end_index = max(0, split_point - embargo_samples)
    train = sorted_df.iloc[:train_end_index].reset_index(drop=True)
    test = sorted_df.iloc[split_point:].reset_index(drop=True)
    dropped = split_point - train_end_index

    if train.empty or test.empty:
        raise ValueError(
            "dataset pequeno demais para a divisao temporal e o embargo configurados "
            f"(n={n}, test_fraction={test_fraction}, embargo_samples={embargo_samples})."
        )

    return TemporalSplit(
        train=train,
        test=test,
        train_start=train[time_column].iloc[0],
        train_end=train[time_column].iloc[-1],
        test_start=test[time_column].iloc[0],
        test_end=test[time_column].iloc[-1],
        embargo_samples_dropped=dropped,
    )

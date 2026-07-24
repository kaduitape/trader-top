"""Detecção de drift (Fase 13): distribuição de features e degradação de
métricas, puramente funcional.

## Drift de features (PSI)

`compute_psi` implementa o Population Stability Index, a métrica padrão
da indústria (originada em risco de crédito, adotada amplamente em
monitoramento de ML) para comparar duas distribuições univariadas. Os
limiares abaixo (`0.1`/`0.25`) são a convenção comum na literatura — não
inventados para este projeto:

- PSI < 0.10: sem mudança significativa.
- 0.10 <= PSI < 0.25: mudança moderada (`WARNING`).
- PSI >= 0.25: mudança significativa (`CRITICAL`).

## Drift de métricas

`detect_metric_drift` compara um valor de métrica RECENTE contra o valor
registrado no momento do treino (`ModelManifestEntry.metrics`, Fase 8) —
nunca contra um número "esperado" arbitrário. Funciona tanto para
métricas onde "maior é melhor" (expectativa, win rate) quanto "menor é
melhor" (Brier score, log-loss).

Este módulo nunca decide o que fazer com um drift detectado (não
desativa um modelo, não força `EMERGENCY_STOP`) — apenas classifica e
relata; a persistência (`app.database.models.drift_event`) e a decisão
continuam humanas.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np
import pandas as pd

_PSI_WARNING_THRESHOLD = 0.10
_PSI_CRITICAL_THRESHOLD = 0.25


class DriftSeverity(enum.StrEnum):
    NONE = "NONE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def compute_psi(reference: np.ndarray, current: np.ndarray, *, bins: int = 10) -> float:
    """PSI entre a amostra `reference` (ex.: conjunto de teste do treino,
    `ModelRegistry.load_test_set`) e `current` (dados recentes). Os bins
    são definidos pelos quantis de `reference`, nunca de `current` — o
    PSI mede o quanto `current` se afasta do que era "normal" na
    referência, não o contrário."""
    reference = np.asarray(reference, dtype=float)
    current = np.asarray(current, dtype=float)
    reference = reference[~np.isnan(reference)]
    current = current[~np.isnan(current)]

    if len(reference) == 0 or len(current) == 0:
        return 0.0

    quantiles = np.linspace(0.0, 1.0, bins + 1)
    edges = np.unique(np.quantile(reference, quantiles))
    if len(edges) < 3:
        # Referencia sem variancia suficiente para formar pelo menos 2
        # bins distintos -- nao da para medir drift de forma
        # significativa (nunca inventa um numero nesse caso).
        return 0.0
    edges[0] = -np.inf
    edges[-1] = np.inf

    ref_counts, _ = np.histogram(reference, bins=edges)
    cur_counts, _ = np.histogram(current, bins=edges)

    ref_pct = ref_counts / ref_counts.sum()
    cur_pct = cur_counts / cur_counts.sum()

    # Evita log(0)/divisao por zero em bins vazios -- piso padrao da
    # literatura de PSI, nao um ajuste especifico deste projeto.
    epsilon = 1e-4
    ref_pct = np.clip(ref_pct, epsilon, None)
    cur_pct = np.clip(cur_pct, epsilon, None)

    psi = float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))
    return psi


def classify_psi(psi: float) -> DriftSeverity:
    if psi >= _PSI_CRITICAL_THRESHOLD:
        return DriftSeverity.CRITICAL
    if psi >= _PSI_WARNING_THRESHOLD:
        return DriftSeverity.WARNING
    return DriftSeverity.NONE


@dataclass(frozen=True, slots=True)
class FeatureDriftResult:
    feature: str
    psi: float
    severity: DriftSeverity


def detect_feature_drift(
    reference_df: pd.DataFrame,
    current_df: pd.DataFrame,
    *,
    feature_columns: list[str],
    bins: int = 10,
) -> list[FeatureDriftResult]:
    """Uma linha por feature em `feature_columns` presente em ambos os
    DataFrames — features ausentes em um dos dois são simplesmente
    puladas, nunca tratadas como drift infinito."""
    results = []
    for column in feature_columns:
        if column not in reference_df.columns or column not in current_df.columns:
            continue
        psi = compute_psi(reference_df[column].to_numpy(), current_df[column].to_numpy(), bins=bins)
        results.append(FeatureDriftResult(feature=column, psi=psi, severity=classify_psi(psi)))
    return results


@dataclass(frozen=True, slots=True)
class MetricDriftResult:
    metric_name: str
    baseline_value: float
    current_value: float
    degradation_pct: float
    """Positivo = piorou em relacao ao baseline; negativo = melhorou.
    Sempre expresso na mesma direcao (piora = positivo),
    independentemente de a metrica ser "maior e melhor" ou "menor e
    melhor"."""
    severity: DriftSeverity


def detect_metric_drift(
    metric_name: str,
    baseline_value: float,
    current_value: float,
    *,
    higher_is_better: bool,
    warning_pct: float = 20.0,
    critical_pct: float = 50.0,
) -> MetricDriftResult:
    if baseline_value == 0:
        degradation_pct = 0.0 if current_value == 0 else 100.0
    else:
        raw_change_pct = (current_value - baseline_value) / abs(baseline_value) * 100.0
        degradation_pct = -raw_change_pct if higher_is_better else raw_change_pct

    if degradation_pct >= critical_pct:
        severity = DriftSeverity.CRITICAL
    elif degradation_pct >= warning_pct:
        severity = DriftSeverity.WARNING
    else:
        severity = DriftSeverity.NONE

    return MetricDriftResult(
        metric_name=metric_name,
        baseline_value=baseline_value,
        current_value=current_value,
        degradation_pct=degradation_pct,
        severity=severity,
    )

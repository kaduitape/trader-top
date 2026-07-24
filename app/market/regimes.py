"""Deteccao de regime de mercado por regras (ADX, volatilidade realizada,
spread, volume relativo) — a abordagem inicial explicitamente pedida pelo
prompt mestre (secao 10); clustering e Hidden Markov Models ficam para
fases posteriores, apenas se os metodos por regra se mostrarem
insuficientes.

O regime nao e um unico rotulo — e um conjunto de eixos ortogonais
(tendencia, volatilidade, adequacao de spread/liquidez, transicao, evento
extraordinario), exatamente como o exemplo do prompt mestre combina
"lateral + volatilidade normal" para Mean Reversion. Cada estrategia (Fase
6+) declara quais combinacoes permite/proibe.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import pandas as pd


class Trend(enum.StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    SIDEWAYS = "SIDEWAYS"


class VolatilityLevel(enum.StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    adx_trend_min: float = 25.0
    volatility_baseline_window: int = 100
    volatility_low_ratio: float = 0.7
    volatility_high_ratio: float = 1.5
    max_spread_points: float = 50.0
    min_relative_volume: float = 0.3
    extraordinary_atr_multiplier: float = 3.0


@dataclass(frozen=True, slots=True)
class MarketRegime:
    trend: Trend
    volatility: VolatilityLevel
    spread_adequate: bool
    liquidity_adequate: bool
    is_transition: bool
    is_extraordinary_event: bool


_REQUIRED_COLUMNS = (
    "adx_14",
    "plus_di_14",
    "minus_di_14",
    "realized_volatility_20",
    "atr_14",
    "relative_volume_20",
    "avg_spread_20",
)


def _classify_trend(adx: float, plus_di: float, minus_di: float, min_adx: float) -> Trend:
    if pd.isna(adx) or adx < min_adx:
        return Trend.SIDEWAYS
    return Trend.UP if plus_di >= minus_di else Trend.DOWN


def classify_regime_series(
    features: pd.DataFrame, *, thresholds: RegimeThresholds = RegimeThresholds()
) -> pd.DataFrame:
    """Classifica cada linha da matriz de features (ver
    `app.market.features.build_candle_features`) em um `MarketRegime`,
    retornando um DataFrame com uma coluna por eixo."""
    missing = [c for c in _REQUIRED_COLUMNS if c not in features.columns]
    if missing:
        raise ValueError(f"Colunas ausentes para classificar regime: {missing}")

    trend = features.apply(
        lambda row: _classify_trend(
            row["adx_14"], row["plus_di_14"], row["minus_di_14"], thresholds.adx_trend_min
        ),
        axis=1,
    )

    volatility_baseline = (
        features["realized_volatility_20"]
        .rolling(
            window=thresholds.volatility_baseline_window,
            min_periods=max(2, thresholds.volatility_baseline_window // 2),
        )
        .mean()
    )
    volatility_ratio = features["realized_volatility_20"] / volatility_baseline

    def _volatility_bucket(ratio: float) -> VolatilityLevel:
        if pd.isna(ratio):
            return VolatilityLevel.NORMAL
        if ratio < thresholds.volatility_low_ratio:
            return VolatilityLevel.LOW
        if ratio > thresholds.volatility_high_ratio:
            return VolatilityLevel.HIGH
        return VolatilityLevel.NORMAL

    volatility = volatility_ratio.apply(_volatility_bucket)

    spread_adequate = features["avg_spread_20"] <= thresholds.max_spread_points
    liquidity_adequate = features["relative_volume_20"] >= thresholds.min_relative_volume

    previous_trend = trend.shift(1)
    is_transition = (trend != previous_trend) & previous_trend.notna()

    atr_baseline = (
        features["atr_14"]
        .rolling(
            window=thresholds.volatility_baseline_window,
            min_periods=max(2, thresholds.volatility_baseline_window // 2),
        )
        .mean()
    )
    is_extraordinary_event = features["atr_14"] > (
        atr_baseline * thresholds.extraordinary_atr_multiplier
    )
    is_extraordinary_event = is_extraordinary_event.fillna(False)

    return pd.DataFrame(
        {
            "trend": trend.astype(str),
            "volatility": volatility.astype(str),
            "spread_adequate": spread_adequate.fillna(False),
            "liquidity_adequate": liquidity_adequate.fillna(False),
            "is_transition": is_transition,
            "is_extraordinary_event": is_extraordinary_event,
        },
        index=features.index,
    )


def regime_from_row(row: pd.Series) -> MarketRegime:
    """Reconstroi um `MarketRegime` a partir de uma linha do DataFrame
    retornado por `classify_regime_series` — usado pelo motor de backtest
    (Fase 5) para anexar o regime vigente a cada sinal/trade."""
    return MarketRegime(
        trend=Trend(row["trend"]),
        volatility=VolatilityLevel(row["volatility"]),
        spread_adequate=bool(row["spread_adequate"]),
        liquidity_adequate=bool(row["liquidity_adequate"]),
        is_transition=bool(row["is_transition"]),
        is_extraordinary_event=bool(row["is_extraordinary_event"]),
    )


def classify_latest_regime(
    features: pd.DataFrame, *, thresholds: RegimeThresholds = RegimeThresholds()
) -> MarketRegime:
    """Classifica apenas a barra mais recente — conveniencia para uso em
    CLI/estrategias, que normalmente so precisam do regime atual."""
    if features.empty:
        raise ValueError("features vazio: nao ha barra para classificar.")

    series = classify_regime_series(features, thresholds=thresholds)
    return regime_from_row(series.iloc[-1])

"""Construcao do dataset de treinamento (Fase 8).

Roda uma `Strategy` sobre uma serie de candles com a MESMA regra de "uma
posicao por vez" do motor de backtest por candle (Fase 5) — o proximo
sinal so pode ser considerado depois que o anterior foi resolvido (alvo,
stop ou limite de tempo). Isso e o que evita "sobrepasicao indevida entre
amostras" exigida pelo prompt mestre: as janelas de barreira tripla de
duas amostras consecutivas nunca se sobrepoem.

Cada amostra do dataset corresponde a um sinal REAL que a estrategia teria
gerado — nao a uma barra aleatoria — respondendo exatamente a pergunta do
prompt mestre (secao 12): dado este sinal, o alvo foi atingido antes do
stop?
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from app.market.features import CandleFeatureLike, build_candle_features, required_lookback_bars
from app.market.regimes import classify_regime_series, regime_from_row
from app.ml.labels import apply_triple_barrier
from app.strategies.base import MarketState, Strategy

# Features "seguras" para ML: distancias/razoes/osciladores relativos, nao
# niveis de preco absolutos (EMA/Bollinger em valor bruto nao generalizam
# entre periodos/simbolos — ver docs/features.md). MACD, momentum_10 e as
# features de forma do candle (amplitude/corpo/pavios) permanecem em escala
# de preco (limitacao conhecida, documentada em docs/ml.md).
ML_NUMERIC_FEATURE_COLUMNS: tuple[str, ...] = (
    "log_return",
    "return_10",
    "dist_ema_9",
    "dist_ema_21",
    "dist_ema_50",
    "dist_ema_200",
    "ema_21_slope",
    "rsi_14",
    "macd_line",
    "macd_signal",
    "macd_histogram",
    "atr_14",
    "adx_14",
    "plus_di_14",
    "minus_di_14",
    "zscore_20",
    "roc_10",
    "momentum_10",
    "realized_volatility_20",
    "dist_vwap_20",
    "candle_amplitude",
    "candle_body",
    "candle_upper_wick",
    "candle_lower_wick",
    "candle_streak",
    "relative_volume_20",
    "volume_acceleration",
    "avg_spread_20",
    "spread_variation_20",
    "relative_spread_bps",
    "hour_utc",
    "minute_of_day",
    "day_of_week",
)

ML_CATEGORICAL_FEATURE_COLUMNS: tuple[str, ...] = ("session",)

ML_METADATA_COLUMNS: tuple[str, ...] = (
    "signal_id",
    "signal_time",
    "direction",
    "entry_price",
    "entry_spread",
    "stop_loss",
    "take_profit",
    "outcome",
    "label",
    "exit_price",
    "bars_held",
    "regime_trend",
    "regime_volatility",
)


@dataclass(frozen=True, slots=True)
class _PendingSignal:
    signal_id: str
    signal_time: object
    direction: object
    signal_bar_index: int
    execute_at_index: int
    stop_loss: float
    take_profit: float
    regime_trend: str
    regime_volatility: str


def build_signal_dataset(
    strategy: Strategy,
    candles: Sequence[CandleFeatureLike],
    *,
    symbol: str,
    timeframe: str,
    point: float,
    max_horizon_bars: int,
    entry_delay_bars: int = 1,
) -> pd.DataFrame:
    """Retorna um DataFrame com uma linha por sinal gerado pela
    estrategia, contendo as features no momento do sinal + o rotulo de
    barreira tripla. Vazio (mas com as colunas corretas) se nenhum sinal
    foi gerado ou nao houver candles suficientes."""
    columns = (
        list(ML_METADATA_COLUMNS)
        + list(ML_NUMERIC_FEATURE_COLUMNS)
        + list(ML_CATEGORICAL_FEATURE_COLUMNS)
    )
    n = len(candles)
    if n == 0:
        return pd.DataFrame(columns=columns)

    features = build_candle_features(candles, point=point)
    regimes = classify_regime_series(features)

    rows: list[dict[str, object]] = []
    pending: _PendingSignal | None = None
    occupied_until_index = -1
    # Antes do lookback minimo (ex.: ema_200 precisa de 200 barras), varias
    # features ainda sao NaN — gerar um sinal com features incompletas
    # corromperia o dataset (e quebraria o treino de modelos que nao aceitam
    # NaN, como regressao logistica). Simplesmente nao avaliamos a
    # estrategia antes disso, em vez de fabricar/imputar um valor.
    warmup_index = required_lookback_bars() - 1

    i = 0
    while i < n:
        if pending is None and i > occupied_until_index and i >= warmup_index:
            current_regime = regime_from_row(regimes.iloc[i])
            state = MarketState(
                symbol=symbol,
                timeframe=timeframe,
                features=features.iloc[: i + 1],
                regime=current_regime,
            )
            signal = strategy.generate_signal(state)
            if signal is not None:
                execute_at = i + entry_delay_bars
                if execute_at < n:
                    pending = _PendingSignal(
                        signal_id=signal.signal_id,
                        signal_time=signal.generated_at,
                        direction=signal.direction,
                        signal_bar_index=i,
                        execute_at_index=execute_at,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        regime_trend=current_regime.trend.value,
                        regime_volatility=current_regime.volatility.value,
                    )

        if pending is not None and i == pending.execute_at_index:
            entry_candle = candles[pending.execute_at_index]
            entry_price = float(entry_candle.open)
            outcome = apply_triple_barrier(
                candles,
                pending.execute_at_index,
                pending.direction,  # type: ignore[arg-type]
                entry_price=entry_price,
                stop_loss=pending.stop_loss,
                take_profit=pending.take_profit,
                max_horizon_bars=max_horizon_bars,
            )

            if outcome is not None:
                feature_row = features.iloc[pending.signal_bar_index]
                row: dict[str, object] = {
                    "signal_id": pending.signal_id,
                    "signal_time": pending.signal_time,
                    "direction": pending.direction.value,  # type: ignore[attr-defined]
                    "entry_price": entry_price,
                    "entry_spread": int(entry_candle.spread),
                    "stop_loss": pending.stop_loss,
                    "take_profit": pending.take_profit,
                    "outcome": outcome.outcome.value,
                    "label": outcome.label,
                    "exit_price": outcome.exit_price,
                    "bars_held": outcome.bars_held,
                    "regime_trend": pending.regime_trend,
                    "regime_volatility": pending.regime_volatility,
                }
                for column in ML_NUMERIC_FEATURE_COLUMNS:
                    row[column] = feature_row[column]
                for column in ML_CATEGORICAL_FEATURE_COLUMNS:
                    row[column] = feature_row[column]
                rows.append(row)
                occupied_until_index = outcome.exit_index
            else:
                # Sem barras suficientes para resolver a barreira: nao ha
                # mais nada de util a processar depois deste ponto.
                occupied_until_index = n

            pending = None

        i += 1

    if not rows:
        return pd.DataFrame(columns=columns)

    return pd.DataFrame(rows, columns=columns)

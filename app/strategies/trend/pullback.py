"""Estrategia A do prompt mestre (secao 11): tendencia com pullback.

Escopo desta fase (unico timeframe disponivel — sem contexto M5 separado
de gatilho M1, que e a Estrategia I multi-timeframe, Fase 6 futura/
ensemble): a "tendencia" e a "direcao confirmada por EMA e inclinacao" vêm
do regime e da inclinacao da EMA21 no MESMO timeframe da candle
processada. O pullback e detectado via z-score (desvio da media que volta
a se contrair) e a retomada de momentum via RSI saindo de uma zona
neutra/adversa na direcao da tendencia.

Deliberadamente fora do escopo: filtro de notícia de alto impacto (depende
do calendario economico, Fase 19, ainda nao implementado).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.market.regimes import Trend
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.risk_helpers import atr_stop_and_target, is_nan


@dataclass(frozen=True, slots=True)
class TrendPullbackConfig:
    slope_column: str = "ema_21_slope"
    zscore_column: str = "zscore_20"
    rsi_column: str = "rsi_14"
    pullback_zscore_threshold: float = 1.0
    rsi_recovery_long: float = 45.0
    rsi_recovery_short: float = 55.0
    atr_multiplier_stop: float = 1.5
    risk_reward_ratio: float = 2.0
    validity_bars: int = 1


class TrendPullbackStrategy(Strategy):
    """Compra em tendencia de alta apos um pullback (zscore negativo se
    contraindo) com RSI voltando a subir; vende no cenario espelhado em
    tendencia de baixa. Exige regime de tendencia e spread adequado."""

    name = "trend_pullback"

    def __init__(self, config: TrendPullbackConfig, *, point: float, bar_seconds: int) -> None:
        self._config = config
        self._point = point
        self._bar_seconds = bar_seconds

    def generate_signal(self, state: MarketState) -> Signal | None:
        previous = state.previous
        if previous is None or state.regime is None:
            return None

        regime = state.regime
        if regime.trend == Trend.SIDEWAYS or not regime.spread_adequate:
            return None

        current = state.current
        cfg = self._config
        slope = current[cfg.slope_column]
        zscore_now = current[cfg.zscore_column]
        zscore_prev = previous[cfg.zscore_column]
        rsi_now = current[cfg.rsi_column]
        atr = current["atr_14"]

        if any(is_nan(v) for v in (slope, zscore_now, zscore_prev, rsi_now, atr)):
            return None

        direction: SignalDirection | None = None
        reason = ""

        if (
            regime.trend == Trend.UP
            and slope > 0
            and zscore_prev <= -cfg.pullback_zscore_threshold
            and zscore_now > zscore_prev
            and rsi_now >= cfg.rsi_recovery_long
        ):
            direction = SignalDirection.LONG
            reason = (
                f"tendencia de alta (slope={slope:.6f}), pullback (zscore {zscore_prev:.2f} -> "
                f"{zscore_now:.2f}) e RSI recuperando ({rsi_now:.1f})"
            )
        elif (
            regime.trend == Trend.DOWN
            and slope < 0
            and zscore_prev >= cfg.pullback_zscore_threshold
            and zscore_now < zscore_prev
            and rsi_now <= cfg.rsi_recovery_short
        ):
            direction = SignalDirection.SHORT
            reason = (
                f"tendencia de baixa (slope={slope:.6f}), pullback (zscore {zscore_prev:.2f} -> "
                f"{zscore_now:.2f}) e RSI recuperando ({rsi_now:.1f})"
            )

        if direction is None:
            return None

        reference_price = float(current["close"])
        stop_loss, take_profit = atr_stop_and_target(
            reference_price,
            direction,
            float(atr),
            atr_multiplier_stop=cfg.atr_multiplier_stop,
            risk_reward_ratio=cfg.risk_reward_ratio,
        )
        generated_at = current["open_time"]

        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=direction,
            generated_at=generated_at,
            reference_price=reference_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            valid_until=generated_at + timedelta(seconds=self._bar_seconds * cfg.validity_bars),
            reason=reason,
            regime_required="tendencia (UP ou DOWN) + spread adequado",
            confidence=0.5,
            features_used={
                cfg.slope_column: float(slope),
                cfg.zscore_column: float(zscore_now),
                cfg.rsi_column: float(rsi_now),
                "atr_14": float(atr),
            },
        )

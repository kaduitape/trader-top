"""Estrategia B do prompt mestre (secao 11): cruzamento de EMAs, usada
apenas como baseline de comparacao — "o objetivo e servir como comparacao,
nao presumir que sera lucrativa". E a estrategia escolhida para validar o
motor de backtest na Fase 5 justamente por ser simples e determinística.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.strategies.base import MarketState, Signal, SignalDirection, Strategy


@dataclass(frozen=True, slots=True)
class EmaCrossoverConfig:
    fast_column: str = "ema_9"
    slow_column: str = "ema_21"
    stop_loss_points: float = 100.0
    take_profit_points: float = 200.0
    validity_bars: int = 1


class EmaCrossoverStrategy(Strategy):
    """Compra quando `fast_column` cruza para cima de `slow_column`; vende
    quando cruza para baixo. Stop/alvo em pontos fixos (normalizados pelo
    `point` do simbolo) — sem gestao de risco alguma alem disso; o motor de
    risco real (Fase 17) e quem decide tamanho de posicao/limites."""

    name = "ema_crossover_baseline"

    def __init__(self, config: EmaCrossoverConfig, *, point: float, bar_seconds: int) -> None:
        self._config = config
        self._point = point
        self._bar_seconds = bar_seconds

    def generate_signal(self, state: MarketState) -> Signal | None:
        current = state.current
        previous = state.previous
        if previous is None:
            return None

        fast, slow = self._config.fast_column, self._config.slow_column
        current_fast, current_slow = current[fast], current[slow]
        previous_fast, previous_slow = previous[fast], previous[slow]

        if any(_is_nan(v) for v in (current_fast, current_slow, previous_fast, previous_slow)):
            return None

        crossed_up = previous_fast <= previous_slow and current_fast > current_slow
        crossed_down = previous_fast >= previous_slow and current_fast < current_slow

        if not crossed_up and not crossed_down:
            return None

        reference_price = float(current["close"])
        stop_distance = self._config.stop_loss_points * self._point
        target_distance = self._config.take_profit_points * self._point
        generated_at = current["open_time"]

        direction = SignalDirection.LONG if crossed_up else SignalDirection.SHORT
        stop_loss = (
            reference_price - stop_distance
            if direction == SignalDirection.LONG
            else reference_price + stop_distance
        )
        take_profit = (
            reference_price + target_distance
            if direction == SignalDirection.LONG
            else reference_price - target_distance
        )

        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=direction,
            generated_at=generated_at,
            reference_price=reference_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            valid_until=generated_at
            + timedelta(seconds=self._bar_seconds * self._config.validity_bars),
            reason=f"{fast} cruzou {'acima' if crossed_up else 'abaixo'} de {slow}",
            regime_required="nenhum (estrategia baseline, sem filtro de regime)",
            confidence=0.5,
            features_used={fast: float(current_fast), slow: float(current_slow)},
        )


def _is_nan(value: float) -> bool:
    return value != value  # nan != nan e True; evita depender de math/numpy aqui.

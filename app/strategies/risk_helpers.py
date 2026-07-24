"""Helpers compartilhados entre estrategias (Fase 6).

Nao e o motor de risco real (Fase 17) — apenas o calculo de stop/alvo que
cada estrategia usa para compor o proprio `Signal`. O motor de risco real
continua tendo poder de veto sobre qualquer sinal, independente de como o
stop foi calculado aqui.
"""

from __future__ import annotations

from app.strategies.base import SignalDirection


def atr_stop_and_target(
    reference_price: float,
    direction: SignalDirection,
    atr: float,
    *,
    atr_multiplier_stop: float,
    risk_reward_ratio: float,
) -> tuple[float, float]:
    """Stop a `atr_multiplier_stop` ATRs de distancia; alvo a
    `risk_reward_ratio` vezes a distancia do stop (razao risco/retorno
    fixa). Retorna `(stop_loss, take_profit)`."""
    stop_distance = atr * atr_multiplier_stop
    target_distance = stop_distance * risk_reward_ratio
    if direction == SignalDirection.LONG:
        return reference_price - stop_distance, reference_price + target_distance
    return reference_price + stop_distance, reference_price - target_distance


def is_nan(value: float) -> bool:
    return value != value  # nan != nan e True; evita depender de math/numpy aqui.

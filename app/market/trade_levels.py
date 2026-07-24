"""Niveis de trade (entrada/stop/TPs/trailing/break-even) — Fase 18.7.

Deliberadamente FORA de `app/risk/` (motor de risco real, Fase 11): esta
saida e consultiva, parte do relatorio de analise (Fase 18.8), e nunca
alimenta a pipeline de execucao/risco existente. O stop e o mais apertado
entre a distancia por ATR e um nivel de estrutura (order block/swing
oposto) quando ambos estao disponiveis do lado certo do preco de entrada —
nunca inventa um nivel de estrutura que nao foi de fato detectado (nesse
caso, cai para o stop puramente por ATR)."""

from __future__ import annotations

from dataclasses import dataclass

from app.strategies.base import SignalDirection


@dataclass(frozen=True, slots=True)
class TradeLevels:
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_1: float
    risk_reward_2: float
    risk_reward_3: float
    trailing_activation_price: float
    break_even_price: float


def compute_trade_levels(
    *,
    direction: SignalDirection,
    entry_price: float,
    atr: float,
    structure_stop_price: float | None,
    atr_multiplier_stop: float = 1.5,
    tp_r_multiples: tuple[float, float, float] = (1.0, 2.0, 3.0),
    break_even_at_r: float = 1.0,
    trailing_activation_at_r: float = 1.5,
) -> TradeLevels:
    atr_distance = atr_multiplier_stop * atr

    if direction == SignalDirection.LONG:
        atr_stop = entry_price - atr_distance
        if structure_stop_price is not None and structure_stop_price < entry_price:
            stop = max(atr_stop, structure_stop_price)
        else:
            stop = atr_stop
        risk = entry_price - stop
        sign = 1.0
    else:
        atr_stop = entry_price + atr_distance
        if structure_stop_price is not None and structure_stop_price > entry_price:
            stop = min(atr_stop, structure_stop_price)
        else:
            stop = atr_stop
        risk = stop - entry_price
        sign = -1.0

    return TradeLevels(
        entry=entry_price,
        stop_loss=stop,
        take_profit_1=entry_price + sign * risk * tp_r_multiples[0],
        take_profit_2=entry_price + sign * risk * tp_r_multiples[1],
        take_profit_3=entry_price + sign * risk * tp_r_multiples[2],
        risk_reward_1=tp_r_multiples[0],
        risk_reward_2=tp_r_multiples[1],
        risk_reward_3=tp_r_multiples[2],
        trailing_activation_price=entry_price + sign * risk * trailing_activation_at_r,
        break_even_price=entry_price + sign * risk * break_even_at_r,
    )

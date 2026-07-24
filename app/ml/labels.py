"""Rotulagem por barreira tripla (triple barrier), conforme prompt mestre
(secao 12): "Crie rotulos por barreiras: barreira superior, barreira
inferior, limite de tempo." e "Avalie abordagem semelhante a triple
barrier."

A barreira superior/inferior sao o `take_profit`/`stop_loss` do proprio
sinal da estrategia (nao um limiar arbitrario) — assim o rotulo responde
exatamente a pergunta do prompt mestre: dado ESTE sinal, o alvo foi
atingido antes do stop?

Usa a MESMA regra conservadora das Fases 5/6 quando ambas as barreiras
cabem na mesma candle (assume o stop, o pior caso) — nenhuma logica de
resolucao de ambiguidade nova foi inventada para o rotulo.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from app.market.features import CandleFeatureLike
from app.strategies.base import SignalDirection


class BarrierOutcome(enum.StrEnum):
    TARGET_FIRST = "TARGET_FIRST"
    STOP_FIRST = "STOP_FIRST"
    TIME_BARRIER = "TIME_BARRIER"


@dataclass(frozen=True, slots=True)
class TripleBarrierResult:
    outcome: BarrierOutcome
    label: int
    """1 se o alvo foi atingido antes do stop, 0 caso contrario (stop
    atingido primeiro OU nenhum dos dois dentro do horizonte — tratado
    conservadoramente como "nao operar")."""
    exit_index: int
    """Posicao absoluta (no array `candles`) da barra em que o desfecho
    ocorreu."""
    exit_price: float
    bars_held: int


def apply_triple_barrier(
    candles: Sequence[CandleFeatureLike],
    signal_index: int,
    direction: SignalDirection,
    *,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    max_horizon_bars: int,
) -> TripleBarrierResult | None:
    """Percorre as candles apos `signal_index` (a entrada e sempre na barra
    seguinte, mesma convencao de nao-antecipacao das Fases 5/6) procurando
    qual barreira foi tocada primeiro. Retorna `None` se nao houver barras
    suficientes apos o sinal (fim dos dados) — nunca inventa um desfecho."""
    n = len(candles)
    start = signal_index + 1
    end = min(start + max_horizon_bars, n)

    if start >= n:
        return None

    for i in range(start, end):
        candle = candles[i]
        low, high = float(candle.low), float(candle.high)

        if direction == SignalDirection.LONG:
            stop_hit = low <= stop_loss
            target_hit = high >= take_profit
        else:
            stop_hit = high >= stop_loss
            target_hit = low <= take_profit

        if stop_hit:
            # Conservador por construcao: se ambas as barreiras cabem na
            # mesma candle, assume-se o stop — nunca o resultado favoravel.
            return TripleBarrierResult(
                BarrierOutcome.STOP_FIRST, 0, i, float(candle.close), i - signal_index
            )
        if target_hit:
            return TripleBarrierResult(
                BarrierOutcome.TARGET_FIRST, 1, i, float(candle.close), i - signal_index
            )

    last_index = end - 1
    return TripleBarrierResult(
        BarrierOutcome.TIME_BARRIER,
        0,
        last_index,
        float(candles[last_index].close),
        last_index - signal_index,
    )

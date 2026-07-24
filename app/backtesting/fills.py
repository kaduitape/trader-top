"""Simulacao de preenchimento de ordens (fill) contra dados de tick reais.

Diferenca central para o backtest por candle (Fase 5): ali, quando stop e
alvo caem na mesma candle, e impossivel saber pela OHLC qual foi atingido
primeiro, entao assumimos sempre o pior caso. Aqui, com a sequencia real de
ticks, essa ambiguidade **deixa de existir** — os ticks tem ordem
cronologica verdadeira, entao o motor (`app.backtesting.tick_engine`)
simplesmente verifica qual nivel foi cruzado primeiro.

Cada fill (entrada ou saida) carrega o registro de auditoria completo
(`FillResult`): preco solicitado, preco de execucao, latencia aplicada,
spread no momento do fill, e o motivo caso tenha sido rejeitado — nunca um
fill "silencioso".
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.strategies.base import SignalDirection


class TickLike(Protocol):
    """Somente leitura (properties, nao atributos simples) — ver
    `app.market.data_quality.CandleLike` para a explicacao de por que isso
    e necessario para aceitar tanto `RawTick` (float) quanto `Tick` do ORM
    (Decimal) covariantemente."""

    @property
    def timestamp(self) -> datetime: ...
    @property
    def bid(self) -> float | Decimal: ...
    @property
    def ask(self) -> float | Decimal: ...


@dataclass(frozen=True, slots=True)
class TickCostModel:
    latency_ms: int = 0
    """Atraso entre a decisao (geracao do sinal ou gatilho de saida) e o
    pedido chegar na corretora — a execucao usa o primeiro tick disponivel
    apos esse atraso, nunca o tick do proprio instante da decisao."""

    slippage_points: float = 0.0
    """Aplicado desfavoravelmente, alem do spread, tanto na entrada quanto
    na saida."""

    max_spread_points: float = 50.0
    """Ordens de ENTRADA sao rejeitadas se o spread no momento do fill
    exceder este limite. Saidas nunca sao rejeitadas (fechar uma posicao
    nao pode ficar pendente por causa de spread — isso e risco, nao
    conveniencia de execucao)."""

    max_tick_gap_seconds: float = 5.0
    """Gap entre ticks consecutivos maior que isso, durante o
    monitoramento de uma posicao aberta, gera um aviso de liquidez
    insuficiente no trade (nao bloqueia a execucao)."""

    commission_per_lot: float = 0.0


@dataclass(frozen=True, slots=True)
class FillResult:
    filled: bool
    requested_time: datetime
    requested_price: float | None
    fill_time: datetime | None
    fill_price: float | None
    latency_ms: int
    spread_points: float | None
    rejection_reason: str | None


def _first_tick_index_at_or_after(ticks: list[TickLike], target_time: datetime) -> int | None:
    index = bisect.bisect_left(ticks, target_time, key=lambda t: t.timestamp)
    return index if index < len(ticks) else None


def simulate_entry_fill(
    ticks: list[TickLike],
    direction: SignalDirection,
    *,
    signal_time: datetime,
    cost_model: TickCostModel,
    point: float,
) -> FillResult:
    """Busca o primeiro tick disponivel apos `signal_time + latencia` e
    calcula o preco de entrada (ask para compra, bid para venda) mais
    slippage. Rejeita se o spread nesse tick exceder `max_spread_points`."""
    execution_floor = signal_time + timedelta(milliseconds=cost_model.latency_ms)
    index = _first_tick_index_at_or_after(ticks, execution_floor)

    if index is None:
        return FillResult(
            filled=False,
            requested_time=signal_time,
            requested_price=None,
            fill_time=None,
            fill_price=None,
            latency_ms=cost_model.latency_ms,
            spread_points=None,
            rejection_reason="sem ticks disponiveis apos a latencia de execucao",
        )

    tick = ticks[index]
    bid, ask = float(tick.bid), float(tick.ask)
    spread_points = (ask - bid) / point if point > 0 else 0.0

    if spread_points > cost_model.max_spread_points:
        return FillResult(
            filled=False,
            requested_time=signal_time,
            requested_price=ask if direction == SignalDirection.LONG else bid,
            fill_time=tick.timestamp,
            fill_price=None,
            latency_ms=cost_model.latency_ms,
            spread_points=spread_points,
            rejection_reason=(
                f"spread de {spread_points:.1f} pontos excede o limite de "
                f"{cost_model.max_spread_points} no momento da execucao"
            ),
        )

    base_price = ask if direction == SignalDirection.LONG else bid
    slippage = cost_model.slippage_points * point
    fill_price = (
        base_price + slippage if direction == SignalDirection.LONG else base_price - slippage
    )

    return FillResult(
        filled=True,
        requested_time=signal_time,
        requested_price=base_price,
        fill_time=tick.timestamp,
        fill_price=fill_price,
        latency_ms=cost_model.latency_ms,
        spread_points=spread_points,
        rejection_reason=None,
    )


def simulate_exit_fill(
    ticks: list[TickLike],
    direction: SignalDirection,
    *,
    trigger_time: datetime,
    cost_model: TickCostModel,
    point: float,
) -> FillResult:
    """Igual a `simulate_entry_fill`, mas para fechar uma posicao — nunca
    rejeitada por spread (fechar e sempre executado; spread largo so piora
    o preco, nao impede a saida)."""
    execution_floor = trigger_time + timedelta(milliseconds=cost_model.latency_ms)
    index = _first_tick_index_at_or_after(ticks, execution_floor)

    if index is None:
        # Sem ticks apos a latencia: usa o ultimo tick conhecido como
        # fallback (a posicao precisa ser fechada de algum jeito) —
        # quem chama e responsavel por marcar o aviso de liquidez.
        if not ticks:
            return FillResult(
                filled=False,
                requested_time=trigger_time,
                requested_price=None,
                fill_time=None,
                fill_price=None,
                latency_ms=cost_model.latency_ms,
                spread_points=None,
                rejection_reason="sem ticks disponiveis para fechar a posicao",
            )
        tick = ticks[-1]
    else:
        tick = ticks[index]

    bid, ask = float(tick.bid), float(tick.ask)
    spread_points = (ask - bid) / point if point > 0 else 0.0
    base_price = bid if direction == SignalDirection.LONG else ask
    slippage = cost_model.slippage_points * point
    fill_price = (
        base_price - slippage if direction == SignalDirection.LONG else base_price + slippage
    )

    return FillResult(
        filled=True,
        requested_time=trigger_time,
        requested_price=base_price,
        fill_time=tick.timestamp,
        fill_price=fill_price,
        latency_ms=cost_model.latency_ms,
        spread_points=spread_points,
        rejection_reason=None,
    )

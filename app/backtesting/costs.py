"""Modelo de custos do backtest por candle.

Escopo desta fase: custos configuraveis por simbolo (via `point`, ja
disponivel na especificacao do simbolo) e por execucao (spread, slippage,
comissao). Segmentacao adicional por corretora/conta/sessao/volume/tipo de
ordem (prompt mestre, secao 13) fica para quando o backtest por ticks
(Fase 7) tiver dados reais de corretora suficientes para justificar a
granularidade — nesta fase, sem essa informacao concreta, seria
configuracao especulativa.

O spread pode vir do proprio candle (campo `spread`, em pontos, gravado
pelo MetaTrader) ou de um valor fixo — nunca inventado quando ausente.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.strategies.base import SignalDirection


@dataclass(frozen=True, slots=True)
class CostModel:
    commission_per_lot: float = 0.0
    """Custo de ida-e-volta (abertura+fechamento) por lote, em moeda da conta."""

    slippage_points: float = 0.0
    """Aplicado desfavoravelmente tanto na entrada quanto na saida."""

    use_recorded_spread: bool = True
    """Se True, usa o campo `spread` (pontos) de cada candle. Se False e
    `fixed_spread_points` for None, o spread e tratado como zero."""

    fixed_spread_points: float | None = None
    """Sobrescreve o spread gravado, quando definido."""


def spread_points_for(model: CostModel, candle_spread_points: int) -> float:
    if model.fixed_spread_points is not None:
        return model.fixed_spread_points
    if model.use_recorded_spread:
        return float(candle_spread_points)
    return 0.0


def _unfavorable_price_shift(model: CostModel, candle_spread_points: int, point: float) -> float:
    """Metade do spread (o round-trip completo e pago entre entrada e
    saida) mais o slippage configurado, convertidos para preco."""
    spread_points = spread_points_for(model, candle_spread_points)
    return (spread_points / 2 + model.slippage_points) * point


def apply_entry_cost(
    reference_price: float,
    direction: SignalDirection,
    *,
    model: CostModel,
    candle_spread_points: int,
    point: float,
) -> float:
    """Preco de entrada apos custo de execucao — sempre pior que o preco de
    referencia (comprar paga mais caro, vender a descoberto recebe menos)."""
    shift = _unfavorable_price_shift(model, candle_spread_points, point)
    return reference_price + shift if direction == SignalDirection.LONG else reference_price - shift


def apply_exit_cost(
    raw_exit_price: float,
    direction: SignalDirection,
    *,
    model: CostModel,
    candle_spread_points: int,
    point: float,
) -> float:
    """Preco de saida apos custo de execucao — sempre pior que o preco
    bruto (fechar uma compra vendendo recebe menos; fechar uma venda
    comprando paga mais)."""
    shift = _unfavorable_price_shift(model, candle_spread_points, point)
    return raw_exit_price - shift if direction == SignalDirection.LONG else raw_exit_price + shift


def commission_cost(model: CostModel, volume: float) -> float:
    return model.commission_per_lot * volume

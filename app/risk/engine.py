"""Avaliação de risco de um sinal (Fase 11, estendida na Fase 13) —
poder de veto entre a estratégia e o envio de qualquer ordem.

Implementa em código as regras inegociáveis do prompt mestre (seção 2):

- **Sem stop-loss, sem aprovação** — rejeitado incondicionalmente.
- **Sem martingale/soros** — `compute_position_size` nunca recebe nem
  usa o resultado de trades anteriores; é sempre a mesma fração fixa do
  saldo atual.
- **Circuit breakers bloqueiam ANTES do prejuízo, não depois** —
  `SOFT_BLOCK` (perdas consecutivas) e `HARD_BLOCK`/`EMERGENCY_STOP`
  (prejuízo diário) rejeitam qualquer sinal novo incondicionalmente;
  apenas `WARNING` permite continuar (com o aviso explícito no motivo).
- **Conta real nunca opera aqui** — `account.is_demo` é checado de novo
  (além da checagem em `app.mt5.orders.send_market_order`) como defesa
  em profundidade.
- **Dados atrasados nunca operam** (Fase 13) — fecha uma pendência
  deixada em aberto na Fase 11 (`docs/risk-management.md` §1):
  `app.risk.feed_health.check_feed_health` rejeita o sinal se a última
  atualização do feed estiver mais velha que `max_feed_delay_seconds`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.mt5.account import AccountSnapshot
from app.mt5.symbol_mapper import SymbolSpecification
from app.risk.circuit_breaker import CircuitBreakerLevel, DailyStats, classify_circuit_breaker
from app.risk.config import RiskLimits
from app.risk.feed_health import check_feed_health
from app.risk.position_sizing import compute_position_size
from app.strategies.base import Signal

_BLOCKING_LEVELS = frozenset(
    {
        CircuitBreakerLevel.SOFT_BLOCK,
        CircuitBreakerLevel.HARD_BLOCK,
        CircuitBreakerLevel.EMERGENCY_STOP,
    }
)


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: bool
    reason: str
    circuit_breaker_level: CircuitBreakerLevel
    computed_volume: float | None


def evaluate_signal(
    signal: Signal,
    *,
    stats: DailyStats,
    limits: RiskLimits,
    account: AccountSnapshot,
    symbol_spec: SymbolSpecification,
    current_spread_points: float,
    feed_last_update_time: datetime,
    now: datetime,
) -> RiskDecision:
    circuit_level = classify_circuit_breaker(stats, limits, initial_balance=account.balance)
    if circuit_level in _BLOCKING_LEVELS:
        return RiskDecision(
            approved=False,
            reason=f"circuit breaker {circuit_level.value} ativo — nenhuma entrada nova permitida.",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    feed_health = check_feed_health(
        last_update_time=feed_last_update_time,
        now=now,
        max_delay_seconds=limits.max_feed_delay_seconds,
    )
    if not feed_health.is_healthy:
        return RiskDecision(
            approved=False,
            reason=feed_health.reason or "feed de dados nao saudavel.",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    if not account.is_demo:
        return RiskDecision(
            approved=False,
            reason="conta conectada não é demo — envio de ordem bloqueado.",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    if stats.open_positions_count >= limits.max_simultaneous_positions:
        return RiskDecision(
            approved=False,
            reason=(
                f"limite de posições simultâneas atingido "
                f"({limits.max_simultaneous_positions})."
            ),
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    if stats.trades_today >= limits.max_trades_per_day:
        return RiskDecision(
            approved=False,
            reason=f"limite diário de trades atingido ({limits.max_trades_per_day}).",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    if stats.last_trade_time is not None:
        elapsed = (now - stats.last_trade_time).total_seconds()
        if elapsed < limits.min_seconds_between_trades:
            return RiskDecision(
                approved=False,
                reason=(
                    f"intervalo mínimo entre operações não respeitado "
                    f"({elapsed:.0f}s < {limits.min_seconds_between_trades}s)."
                ),
                circuit_breaker_level=circuit_level,
                computed_volume=None,
            )

    if current_spread_points > limits.max_spread_points:
        return RiskDecision(
            approved=False,
            reason=(
                f"spread atual ({current_spread_points:.1f} pontos) acima do limite "
                f"({limits.max_spread_points})."
            ),
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    if signal.stop_loss == signal.reference_price:
        return RiskDecision(
            approved=False,
            reason="sinal sem stop-loss válido (stop igual ao preço de referência).",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    stop_distance_price = abs(signal.reference_price - signal.stop_loss)
    volume = compute_position_size(
        balance=account.balance,
        risk_pct=limits.risk_per_trade_pct,
        stop_distance_price=stop_distance_price,
        contract_size=symbol_spec.trade_contract_size,
        volume_min=symbol_spec.volume_min,
        volume_max=symbol_spec.volume_max,
        volume_step=symbol_spec.volume_step,
    )
    if volume <= 0:
        return RiskDecision(
            approved=False,
            reason="risco calculado resulta em volume abaixo do lote mínimo do símbolo.",
            circuit_breaker_level=circuit_level,
            computed_volume=None,
        )

    reason = (
        "aprovado"
        if circuit_level == CircuitBreakerLevel.NONE
        else f"aprovado com aviso ({circuit_level.value})"
    )
    return RiskDecision(
        approved=True,
        reason=reason,
        circuit_breaker_level=circuit_level,
        computed_volume=volume,
    )

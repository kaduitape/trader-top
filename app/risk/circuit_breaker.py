"""Circuit breakers de 4 níveis (Fase 11), conforme `docs/risk-management.md`.

Puramente funcional — nenhum estado próprio. `DailyStats` é sempre
calculado por quem chama (a partir do histórico real de trades do dia),
nunca aproximado ou assumido."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

from app.risk.config import RiskLimits


class CircuitBreakerLevel(enum.StrEnum):
    NONE = "NONE"
    WARNING = "WARNING"
    SOFT_BLOCK = "SOFT_BLOCK"
    HARD_BLOCK = "HARD_BLOCK"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass(frozen=True, slots=True)
class DailyStats:
    trades_today: int
    consecutive_losses: int
    daily_pnl: float
    open_positions_count: int
    last_trade_time: datetime | None


def classify_circuit_breaker(
    stats: DailyStats, limits: RiskLimits, *, initial_balance: float
) -> CircuitBreakerLevel:
    """Nunca escondido atrás de um único booleano "pode operar" — o nível
    retornado é sempre explícito, para que o motivo do bloqueio fique
    auditável (ver `app.risk.engine.evaluate_signal`)."""
    if initial_balance <= 0:
        return CircuitBreakerLevel.EMERGENCY_STOP

    daily_loss_pct = max(0.0, -stats.daily_pnl / initial_balance * 100)

    if daily_loss_pct >= limits.max_daily_loss_pct:
        return CircuitBreakerLevel.HARD_BLOCK
    if stats.consecutive_losses >= limits.max_consecutive_losses:
        return CircuitBreakerLevel.SOFT_BLOCK
    if daily_loss_pct >= limits.max_daily_loss_pct * 0.7:
        return CircuitBreakerLevel.WARNING
    return CircuitBreakerLevel.NONE

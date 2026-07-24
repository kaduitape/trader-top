"""Limites de risco configuráveis (Fase 11, estendido na Fase 13).

Valores padrão deliberadamente conservadores — o prompt mestre pede "a
alternativa mais segura quando em dúvida", nunca configuração agressiva
por padrão."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RiskLimits:
    risk_per_trade_pct: float = 1.0
    """Percentual fixo do saldo arriscado por trade (baseado na distância
    até o stop) — sempre o mesmo, nunca aumentado após uma perda."""

    max_daily_loss_pct: float = 3.0
    """Circuit breaker HARD_BLOCK: nenhuma entrada nova depois que o
    prejuízo do dia atinge este percentual do saldo inicial do dia."""

    max_consecutive_losses: int = 3
    """Circuit breaker SOFT_BLOCK: nenhuma entrada nova apos N perdas
    seguidas — evita "recuperacao compulsiva de perdas"."""

    max_simultaneous_positions: int = 1
    max_trades_per_day: int = 10
    min_seconds_between_trades: int = 60
    max_spread_points: float = 30.0

    max_feed_delay_seconds: float = 300.0
    """Circuit breaker de dados atrasados (Fase 13) — mesmo valor padrão
    de `Settings.quality_max_feed_delay_seconds` (Fase 3), aplicado aqui
    como veto de entrada em vez de ocorrência de qualidade relatada."""

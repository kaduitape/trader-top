"""Checagem de saúde do feed de dados (Fase 13) — fecha uma pendência
deliberadamente deixada em aberto na Fase 11 (ver `docs/risk-management.md`
§1: "bloqueio especificamente por dados atrasados/latência de feed ainda
não implementado").

Mesma semântica de atraso já usada em `app.market.data_quality.
_check_feed_delay` (Fase 3) — não uma segunda definição de "atraso"
divergente, apenas aplicada como um veto do motor de risco em vez de uma
ocorrência de qualidade relatada."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class FeedHealthCheck:
    is_healthy: bool
    reason: str | None
    age_seconds: float


def check_feed_health(
    *, last_update_time: datetime, now: datetime, max_delay_seconds: float
) -> FeedHealthCheck:
    """`last_update_time` é o horário da candle/tick mais recente já
    coletada; `now`, o instante da avaliação do sinal. Nunca assume que o
    feed está saudável na ausência de dados — quem chama decide o que
    fazer quando não há nenhuma barra ainda (não é responsabilidade desta
    função)."""
    age_seconds = (now - last_update_time).total_seconds()
    if age_seconds > max_delay_seconds:
        return FeedHealthCheck(
            is_healthy=False,
            reason=(
                f"dados atrasados ({age_seconds:.0f}s desde a última atualização, "
                f"limite {max_delay_seconds:.0f}s)."
            ),
            age_seconds=age_seconds,
        )
    return FeedHealthCheck(is_healthy=True, reason=None, age_seconds=age_seconds)

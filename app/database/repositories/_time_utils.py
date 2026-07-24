"""Utilitario interno compartilhado pelos repositorios de series temporais
(candles, ticks). Nao e uma API publica do pacote."""

from __future__ import annotations

from datetime import UTC, datetime


def as_aware_utc(value: datetime) -> datetime:
    """SQLite nao preserva tzinfo em colunas DateTime(timezone=True) — o
    valor volta "naive" do banco mesmo tendo sido gravado em UTC (ver
    docs/assumptions.md secao 2.2). Normalizamos para que comparacoes de
    deduplicacao funcionem igual em SQLite (testes) e MySQL (producao),
    onde toda gravacao segue a convencao UTC do projeto."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value

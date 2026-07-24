"""Dimensionamento de posição a partir do risco (Fase 11).

**Nunca depende do resultado de trades anteriores** — é sempre uma fração
fixa (`risk_per_trade_pct`) do saldo ATUAL, recalculada do zero a cada
sinal a partir da distância até o stop deste sinal especificamente. Isso
é o que torna martingale/soros estruturalmente impossíveis aqui: não há
nenhum parâmetro de "sequência" ou "multiplicador após perda/ganho" em
lugar nenhum desta função.
"""

from __future__ import annotations

import math


def compute_position_size(
    *,
    balance: float,
    risk_pct: float,
    stop_distance_price: float,
    contract_size: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
) -> float:
    """Retorna o volume (lotes) tal que, se o stop for atingido, a perda
    seja aproximadamente `balance * risk_pct / 100` — normalizado para o
    `volume_step` do símbolo e limitado a `[volume_min, volume_max]`.
    Retorna `0.0` (nunca um valor negativo ou inventado) se o risco
    calculado não alcançar nem o lote mínimo."""
    if balance <= 0 or risk_pct <= 0 or stop_distance_price <= 0 or contract_size <= 0:
        return 0.0
    if volume_step <= 0 or volume_min <= 0 or volume_max < volume_min:
        return 0.0

    risk_amount = balance * (risk_pct / 100.0)
    raw_volume = risk_amount / (stop_distance_price * contract_size)

    if raw_volume < volume_min:
        return 0.0

    steps = math.floor(raw_volume / volume_step)
    normalized = steps * volume_step
    normalized = max(volume_min, min(normalized, volume_max))
    return round(normalized, 8)

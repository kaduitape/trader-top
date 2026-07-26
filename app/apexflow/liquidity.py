"""Liquidity Engine: onde os stops estao e quem foi buscar eles.

Reaproveita integralmente os detectores ja implementados e testados em
`app.market.smc` e `app.market.structure` — sweeps, springs, upthrusts,
falsos rompimentos, order blocks, fair value gaps e niveis iguais. O que
este modulo acrescenta e a **leitura operacional** deles: consolidar os
eventos recentes em uma unica resposta a pergunta que o motor de decisao
precisa fazer, que e "acabou de acontecer uma caca de stops aqui?".

Por que isso e um veto e nao um sinal: uma caca de stops em andamento
(rompimento falso ainda nao rejeitado) e o pior momento possivel para
entrar a favor do rompimento. Ja um sweep COM reversao confirmada e o
oposto — o movimento contra o rompimento tende a ter continuidade porque a
liquidez do outro lado ja foi consumida.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass

from app.market.features import CandleFeatureLike
from app.market.price_action import PatternDirection
from app.market.smc import (
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    detect_equal_highs_lows,
    detect_fair_value_gaps,
    detect_false_breakout,
    detect_liquidity_sweeps,
    detect_order_blocks,
    update_mitigation_status,
)
from app.market.structure import (
    StructureEvent,
    cluster_swing_levels,
    detect_structure_events,
    detect_swings,
    label_swing_structure,
)

RECENT_BARS = 5
"""Quantas barras finais contam como "acabou de acontecer". Alem disso o
evento e historia, nao condicao atual."""


class LiquidityState(enum.StrEnum):
    CLEAN = "CLEAN"
    """Nenhum evento de liquidez recente — caminho livre."""

    SWEEP_REVERSED = "SWEEP_REVERSED"
    """Liquidez tomada E rejeitada: favorece operar CONTRA o rompimento."""

    STOP_HUNT_ACTIVE = "STOP_HUNT_ACTIVE"
    """Rompimento suspeito ainda sem rejeicao confirmada: nao entrar."""

    FALSE_BREAKOUT = "FALSE_BREAKOUT"
    UNKNOWN = "UNKNOWN"


LIQUIDITY_STATE_LABELS: dict[LiquidityState, str] = {
    LiquidityState.CLEAN: "Sem manipulacao recente",
    LiquidityState.SWEEP_REVERSED: "Liquidez tomada e rejeitada",
    LiquidityState.STOP_HUNT_ACTIVE: "Possivel caca de stops em andamento",
    LiquidityState.FALSE_BREAKOUT: "Falso rompimento confirmado",
    LiquidityState.UNKNOWN: "Sem dados",
}


@dataclass(frozen=True, slots=True)
class LiquidityReading:
    state: LiquidityState
    direction: PatternDirection | None
    """Sentido do evento mais recente, quando existe."""

    recent_sweeps: tuple[LiquiditySweep, ...]
    unmitigated_order_blocks: tuple[OrderBlock, ...]
    open_fair_value_gaps: tuple[FairValueGap, ...]
    structure_events: tuple[StructureEvent, ...]
    institutional_zones: int
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return LIQUIDITY_STATE_LABELS[self.state]

    @property
    def blocks_entry(self) -> bool:
        """So a caca de stops EM ANDAMENTO bloqueia. Um sweep ja revertido
        e informacao a favor, nao impedimento."""
        return self.state == LiquidityState.STOP_HUNT_ACTIVE


def read_liquidity(
    candles: Sequence[CandleFeatureLike], *, recent_bars: int = RECENT_BARS
) -> LiquidityReading:
    """Consolida os eventos de liquidez das ultimas `recent_bars` barras."""
    if len(candles) < 10:
        return LiquidityReading(
            state=LiquidityState.UNKNOWN,
            direction=None,
            recent_sweeps=(),
            unmitigated_order_blocks=(),
            open_fair_value_gaps=(),
            structure_events=(),
            institutional_zones=0,
            reasons=("Historico insuficiente para avaliar liquidez.",),
        )

    swings = detect_swings(candles)
    labels = label_swing_structure(swings)
    events = detect_structure_events(candles, labels)
    sr_levels = cluster_swing_levels(swings)
    equal_levels = detect_equal_highs_lows(swings)

    sweeps = [
        *detect_liquidity_sweeps(candles, sr_levels),
        *detect_liquidity_sweeps(candles, equal_levels),
    ]
    order_blocks = update_mitigation_status(detect_order_blocks(candles, events), candles)
    gaps = detect_fair_value_gaps(candles)
    false_breakouts = detect_false_breakout(candles, sr_levels)

    threshold = len(candles) - recent_bars
    recent_sweeps = tuple(sweep for sweep in sweeps if sweep.index >= threshold)
    recent_false_breakouts = [
        pattern for pattern in false_breakouts if pattern.index >= threshold
    ]
    unmitigated = tuple(block for block in order_blocks if not block.mitigated)
    open_gaps = tuple(gap for gap in gaps if not gap.filled)

    reasons: list[str] = []
    direction: PatternDirection | None = None

    reversed_sweeps = [sweep for sweep in recent_sweeps if sweep.reversal_confirmed]
    pending_sweeps = [sweep for sweep in recent_sweeps if not sweep.reversal_confirmed]

    if pending_sweeps:
        latest = max(pending_sweeps, key=lambda sweep: sweep.index)
        state = LiquidityState.STOP_HUNT_ACTIVE
        direction = latest.direction
        reasons.append(
            f"{latest.kind.value} em {latest.swept_price:.5f} ainda sem rejeicao "
            "confirmada — entrar agora seria operar dentro da manipulacao."
        )
    elif reversed_sweeps:
        latest = max(reversed_sweeps, key=lambda sweep: sweep.index)
        state = LiquidityState.SWEEP_REVERSED
        direction = latest.direction
        reasons.append(
            f"{latest.kind.value} em {latest.swept_price:.5f} com reversao "
            "confirmada: a liquidez do outro lado ja foi consumida."
        )
    elif recent_false_breakouts:
        latest_pattern = max(recent_false_breakouts, key=lambda pattern: pattern.index)
        state = LiquidityState.FALSE_BREAKOUT
        direction = latest_pattern.direction
        reasons.append(
            "Falso rompimento confirmado nas ultimas barras — o movimento "
            "aparente nao tinha fluxo por tras."
        )
    else:
        state = LiquidityState.CLEAN
        reasons.append("Nenhum evento de liquidez nas ultimas barras.")

    if unmitigated:
        reasons.append(
            f"{len(unmitigated)} order block(s) nao mitigado(s) — possiveis "
            "regioes institucionais ainda ativas."
        )
    if open_gaps:
        reasons.append(f"{len(open_gaps)} fair value gap(s) por preencher.")

    return LiquidityReading(
        state=state,
        direction=direction,
        recent_sweeps=recent_sweeps,
        unmitigated_order_blocks=unmitigated,
        open_fair_value_gaps=open_gaps,
        structure_events=tuple(events),
        institutional_zones=len(unmitigated) + len(open_gaps),
        reasons=tuple(reasons),
    )

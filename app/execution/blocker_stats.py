"""Contagem de motivos de nao-entrada ao longo do dia.

O status ao vivo mostra o motivo do ULTIMO ciclo. Isso responde "por que
nao entrou agora", mas nao responde a pergunta que importa depois de um dia
inteiro sem operacao: "o que esta me barrando de verdade?".

Um motivo que aparece em 100% dos ciclos e um problema de configuracao (ou
de coleta) esperando para ser resolvido; um que aparece em 8% e o mercado
sendo o mercado. Sem contar, os dois parecem iguais na tela.

Os motivos sao agrupados por PREFIXO (o texto antes do primeiro parentese ou
travessao), porque a mensagem carrega detalhes variaveis — "Volume nao
favoravel (score 51,3...)" e "(score 48,9...)" sao o mesmo problema e
precisam somar, nao gerar duas linhas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

BLOCKER_STATS_SETTING = "autopilot_blocker_stats"
_MAX_REASONS = 12


@dataclass(frozen=True, slots=True)
class BlockerCount:
    reason: str
    count: int
    share: float
    """Fracao dos ciclos do dia em que este motivo apareceu (0.0 a 1.0)."""


@dataclass(frozen=True, slots=True)
class BlockerStats:
    day: str
    cycles: int
    reasons: tuple[BlockerCount, ...] = ()

    @property
    def dominant(self) -> BlockerCount | None:
        return self.reasons[0] if self.reasons else None


def normalize_reason(reason: str) -> str:
    """Descarta a parte variavel da mensagem para que motivos iguais somem."""
    trimmed = re.split(r"[(—:]", reason, maxsplit=1)[0].strip()
    return trimmed or reason.strip()


def _today(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d")


def load_blocker_stats(session: Session, *, now: datetime | None = None) -> BlockerStats:
    day = _today(now)
    raw = SystemSettingRepository(session).get(BLOCKER_STATS_SETTING)
    if not raw:
        return BlockerStats(day=day, cycles=0)
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return BlockerStats(day=day, cycles=0)
    if str(data.get("day")) != day:
        # Dia anterior: a contagem recomeca sozinha.
        return BlockerStats(day=day, cycles=0)

    cycles = int(data.get("cycles", 0))
    counts: dict = data.get("reasons", {})
    ordered = sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))
    return BlockerStats(
        day=day,
        cycles=cycles,
        reasons=tuple(
            BlockerCount(
                reason=reason,
                count=int(count),
                share=(int(count) / cycles) if cycles else 0.0,
            )
            for reason, count in ordered[:_MAX_REASONS]
        ),
    )


def record_cycle(
    session: Session, *, blockers: list[str], now: datetime | None = None
) -> BlockerStats:
    """Registra um ciclo e os motivos que o barraram.

    Um ciclo que entrou (sem motivo) tambem conta, senao a fracao ficaria
    sempre 100% e nao diria nada.
    """
    day = _today(now)
    current = load_blocker_stats(session, now=now)
    counts = {item.reason: item.count for item in current.reasons}
    for reason in {normalize_reason(item) for item in blockers if item.strip()}:
        counts[reason] = counts.get(reason, 0) + 1

    payload = {
        "day": day,
        "cycles": current.cycles + 1,
        "reasons": dict(sorted(counts.items(), key=lambda item: -item[1])[:_MAX_REASONS]),
    }
    SystemSettingRepository(session).set(
        BLOCKER_STATS_SETTING,
        json.dumps(payload),
        description="Motivos de nao-entrada por dia (reiniciado a cada dia UTC).",
    )
    return load_blocker_stats(session, now=now)

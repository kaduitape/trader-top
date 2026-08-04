"""Diario da varredura: o que o scanner ESCOLHERIA, gravado sem operar.

Existe por uma razao so. O scanner aumenta o numero de oportunidades, nao a
qualidade media de cada uma — se a expectativa por operacao for negativa,
varrer o mercado inteiro faz perder mais rapido. A unica forma de saber se a
complexidade valeu e comparar: as escolhas dele foram melhores do que operar
um par fixo?

Para responder isso e preciso ter as escolhas registradas ANTES de confiar
nelas. Por isso o modo observacao vem junto do scanner, e nao depois.

O registro fica em `system_settings` como uma fila circular de tamanho fixo:
o objetivo e avaliacao amostral ("o que ele escolheu nas ultimas semanas"),
nao auditoria contabil — para essa ja existem `live_trades` e o log de
auditoria. Uma tabela dedicada seria mais poderosa e exigiria migracao; se
um dia a amostra justificar, o caminho de migrar esta aberto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.market.scanner import ScanResult

SCAN_JOURNAL_SETTING = "scanner_observations"
MAX_ENTRIES = 500


@dataclass(frozen=True, slots=True)
class ScanObservation:
    at: str
    symbol: str
    score: float
    session: str
    volume: str
    spread_atr_ratio: float | None
    runner_up: str | None
    runner_up_score: float | None
    """O segundo colocado importa: se ele quase sempre empata com o
    primeiro, o ranking nao esta discriminando nada e a complexidade nao se
    paga."""


def _serialize(observation: ScanObservation) -> dict:
    return {
        "at": observation.at,
        "symbol": observation.symbol,
        "score": round(observation.score, 2),
        "session": observation.session,
        "volume": observation.volume,
        "spread_atr": (
            round(observation.spread_atr_ratio, 4)
            if observation.spread_atr_ratio is not None
            else None
        ),
        "runner_up": observation.runner_up,
        "runner_up_score": (
            round(observation.runner_up_score, 2)
            if observation.runner_up_score is not None
            else None
        ),
    }


def load_observations(session: Session) -> list[ScanObservation]:
    raw = SystemSettingRepository(session).get(SCAN_JOURNAL_SETTING)
    if not raw:
        return []
    try:
        registros = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(registros, list):
        return []

    observacoes: list[ScanObservation] = []
    for item in registros:
        if not isinstance(item, dict):
            continue
        observacoes.append(
            ScanObservation(
                at=str(item.get("at", "")),
                symbol=str(item.get("symbol", "")),
                score=float(item.get("score", 0.0)),
                session=str(item.get("session", "")),
                volume=str(item.get("volume", "")),
                spread_atr_ratio=item.get("spread_atr"),
                runner_up=item.get("runner_up"),
                runner_up_score=item.get("runner_up_score"),
            )
        )
    return observacoes


def record_scan(session: Session, result: ScanResult) -> ScanObservation | None:
    """Grava a escolha do scanner. Varredura sem candidato nao gera registro.

    Nao grava nada quando nada seria escolhido: um diario cheio de "nao
    havia nada" enterraria as escolhas de verdade no meio do ruido.
    """
    melhor = result.best
    if melhor is None:
        return None

    aprovados = result.top(2)
    segundo = aprovados[1] if len(aprovados) > 1 else None

    observacao = ScanObservation(
        at=result.generated_at.isoformat(),
        symbol=melhor.symbol,
        score=melhor.score,
        session=melhor.session_label,
        volume=melhor.volume_label,
        spread_atr_ratio=melhor.spread_atr_ratio,
        runner_up=segundo.symbol if segundo else None,
        runner_up_score=segundo.score if segundo else None,
    )

    historico = [_serialize(item) for item in load_observations(session)]
    historico.append(_serialize(observacao))
    SystemSettingRepository(session).set(
        SCAN_JOURNAL_SETTING,
        json.dumps(historico[-MAX_ENTRIES:]),
        description="Escolhas do scanner em modo observacao (fila circular).",
    )
    return observacao


@dataclass(frozen=True, slots=True)
class ScanSummary:
    total: int
    by_symbol: tuple[tuple[str, int], ...]
    average_score: float | None
    average_margin: float | None
    """Distancia media entre o primeiro e o segundo colocado. Margem baixa
    significa que o ranking esta praticamente sorteando."""

    first_at: str | None = None
    last_at: str | None = None


def summarize(session: Session) -> ScanSummary:
    observacoes = load_observations(session)
    if not observacoes:
        return ScanSummary(total=0, by_symbol=(), average_score=None, average_margin=None)

    contagem: dict[str, int] = {}
    for item in observacoes:
        contagem[item.symbol] = contagem.get(item.symbol, 0) + 1

    margens = [
        item.score - item.runner_up_score
        for item in observacoes
        if item.runner_up_score is not None
    ]

    return ScanSummary(
        total=len(observacoes),
        by_symbol=tuple(sorted(contagem.items(), key=lambda par: -par[1])),
        average_score=sum(item.score for item in observacoes) / len(observacoes),
        average_margin=(sum(margens) / len(margens)) if margens else None,
        first_at=observacoes[0].at,
        last_at=observacoes[-1].at,
    )

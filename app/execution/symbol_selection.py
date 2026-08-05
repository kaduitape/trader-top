"""Qual moeda o robo opera neste ciclo.

Ate aqui o par era fixo: o operador escolhia EURUSD e o robo passava o dia
olhando so para EURUSD, mesmo que o radar apontasse XAUUSD com nota muito
melhor. O radar existia e nao mandava em nada.

Este modulo liga os dois. A cada ciclo, quando o modo RADAR esta ativo, a
varredura ordena os candidatos e o robo trabalha o primeiro que ele
consegue de fato operar — se o primeiro nao serve (nao existe na corretora,
sem candles, evento no calendario), ele desce para o proximo em vez de
parar o dia.

DUAS REGRAS INEGOCIAVEIS, e as duas existem para proteger dinheiro:

1. **Posicao aberta congela a escolha.** Trocar de par com posicao aberta
   significaria abandonar o trailing e o break-even dela no meio do
   caminho. O stop no servidor ainda protege o pior caso, mas a gestao
   pararia — e gestao abandonada e a diferenca entre um stop e um stop
   pior. Enquanto houver posicao, o par dela e o par do ciclo.

2. **Trocar tem custo, entao troca precisa de motivo.** Sem histerese, dois
   pares empatados fariam o robo alternar a cada ciclo, sem nunca acompanhar
   nenhum tempo suficiente para operar. A troca so acontece com margem
   minima sobre o par atual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.catalog import resolve_broker_symbol

SOURCE_FIXED = "FIXED"
SOURCE_RADAR = "RADAR"
SYMBOL_SOURCES = (SOURCE_FIXED, SOURCE_RADAR)

# Quanto o candidato precisa ser melhor que o par atual para justificar a
# troca. Cinco pontos e pequeno o bastante para nao travar uma mudanca real
# e grande o bastante para nao alternar por ruido de arredondamento.
SWITCH_MARGIN = 5.0

MAX_CANDIDATES = 8


@dataclass(frozen=True, slots=True)
class SymbolChoice:
    symbol: str
    reason: str
    from_radar: bool
    considered: tuple[str, ...] = ()
    """Quem estava na fila, em ordem. O painel mostra isso para o operador
    ver o que o robo esta monitorando alem do escolhido."""


def _has_open_position(session: Session, symbol_name: str, strategy_name: str) -> bool:
    symbol = SymbolRepository(session).get_by_name(symbol_name)
    if symbol is None:
        return False
    return (
        LiveTradeRepository(session).get_active_position(symbol.id, None, strategy_name)
        is not None
    )


def _tradable_here(session: Session, symbol_name: str, available: list[str]) -> str | None:
    """Nome real na corretora, ou None se o par nao serve para este ciclo."""
    broker = resolve_broker_symbol(symbol_name, available)
    if broker is None:
        return None
    return broker if SymbolRepository(session).get_by_name(broker) is not None else None


def choose_symbol(
    session: Session,
    *,
    configured_symbol: str,
    source: str,
    available_symbols: list[str],
    strategy_name: str,
    now: datetime,
    scan_result=None,
) -> SymbolChoice:
    """Par a operar neste ciclo.

    `scan_result` e injetavel para que o teste (e o painel) nao precisem
    refazer a varredura; quando ausente, ela e feita aqui.
    """
    if source != SOURCE_RADAR:
        return SymbolChoice(
            symbol=configured_symbol,
            reason="Par fixo definido na tela de operacao.",
            from_radar=False,
        )

    # Regra 1: posicao aberta manda.
    if _has_open_position(session, configured_symbol, strategy_name):
        return SymbolChoice(
            symbol=configured_symbol,
            reason=(
                f"Posicao aberta em {configured_symbol}: o radar so volta a "
                "escolher quando ela fechar."
            ),
            from_radar=False,
        )

    if scan_result is None:
        from app.core.config import get_settings
        from app.market.scanner import scan_market

        settings = get_settings()
        scan_result = scan_market(
            session, now=now, timeframe=settings.analysis_default_timeframe
        )

    fila: list[str] = []
    for candidato in scan_result.top(MAX_CANDIDATES):
        if _tradable_here(session, candidato.symbol, available_symbols) is not None:
            fila.append(candidato.symbol)

    if not fila:
        return SymbolChoice(
            symbol=configured_symbol,
            reason=(
                "Radar sem candidato operavel agora — seguindo com "
                f"{configured_symbol} e monitorando."
            ),
            from_radar=False,
        )

    melhor = fila[0]
    if melhor == configured_symbol:
        return SymbolChoice(
            symbol=melhor,
            reason=f"{melhor} continua sendo o melhor do radar.",
            from_radar=True,
            considered=tuple(fila),
        )

    # Regra 2: histerese. Precisa ser melhor o suficiente para valer a troca.
    notas = {item.symbol: item.score for item in scan_result.candidates}
    nota_atual = notas.get(configured_symbol)
    if (
        nota_atual is not None
        and configured_symbol in fila
        and notas[melhor] - nota_atual < SWITCH_MARGIN
    ):
        return SymbolChoice(
            symbol=configured_symbol,
            reason=(
                f"{melhor} lidera por menos de {SWITCH_MARGIN:.0f} pontos — "
                f"nao compensa trocar, seguindo em {configured_symbol}."
            ),
            from_radar=True,
            considered=tuple(fila),
        )

    return SymbolChoice(
        symbol=melhor,
        reason=(
            f"Radar escolheu {melhor} (nota {notas.get(melhor, 0):.0f})"
            + (f", acima de {configured_symbol}." if nota_atual is not None else ".")
        ),
        from_radar=True,
        considered=tuple(fila),
    )

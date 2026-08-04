"""Varredura de oportunidades: qual instrumento merece atencao AGORA.

O sistema sempre soube avaliar UM simbolo — aquele que o operador escolheu.
Este modulo compara todos os coletados e ordena.

## Por que a peneira e barata

Analise completa sao nove timeframes por simbolo. Rodar isso em vinte pares
a cada ciclo de 15 segundos derrubaria o worker. Entao aqui so entram
verificacoes que o banco ja responde: sessao, volume relativo, spread e
calendario. O resultado e um ranking, e a analise cara roda depois, somente
nos primeiros colocados.

## Por que tudo e normalizado

Volume bruto de XAUUSD nao se compara com o de EURUSD — instrumentos
diferentes tem escalas diferentes. Cada criterio aqui compara o ativo com
ELE MESMO: o volume contra a mediana da propria hora, o spread contra o
proprio ATR. Sem isso o ranking so diria qual ativo e maior, nao qual esta
com oportunidade.

## Por que o custo entra na nota

Um sinal otimo num par com spread tres vezes acima do normal e pior que um
sinal bom num par barato — o custo e certo e o sinal e hipotese. Muito
scanner ignora isso, e e onde o dinheiro vaza sem aparecer no backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.calendar_feed.blackout import BlackoutWindow, describe, find_blocking_event
from app.calendar_feed.provider import CalendarSnapshot
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market import features as features_module
from app.market.sessions import SessionRating, evaluate_symbol_session
from app.market.volume_profile import (
    VolumeLevel,
    build_volume_profile,
    read_current_volume,
)

# Peso de cada criterio na nota final. Sessao pesa mais que volume porque
# manda no custo de execucao o dia inteiro: fora da sessao principal, o
# spread e estruturalmente pior, nao ocasionalmente pior.
WEIGHT_SESSION = 0.40
WEIGHT_VOLUME = 0.35
WEIGHT_COST = 0.25

_SESSION_SCORES = {
    SessionRating.PRIME: 100.0,
    SessionRating.ACTIVE: 75.0,
    SessionRating.QUIET: 35.0,
    SessionRating.CLOSED: 0.0,
}

_VOLUME_SCORES = {
    VolumeLevel.HIGH: 100.0,
    VolumeLevel.NORMAL: 75.0,
    VolumeLevel.LOW: 35.0,
    # Pico de evento: spread alargado e slippage imprevisivel. Nao e
    # oportunidade, e armadilha com aparencia de oportunidade.
    VolumeLevel.EXTREME: 15.0,
    VolumeLevel.DEAD: 0.0,
    VolumeLevel.UNKNOWN: 40.0,
}

# Spread em fracao do ATR. Acima do teto a nota de custo zera: pagar meio
# ATR so para entrar inviabiliza qualquer alvo realista.
COST_GOOD_RATIO = 0.05
COST_MAX_RATIO = 0.50


@dataclass(frozen=True, slots=True)
class ScanCandidate:
    symbol: str
    score: float
    session_score: float
    volume_score: float
    cost_score: float
    session_label: str
    volume_label: str
    spread_points: float | None
    atr_points: float | None
    spread_atr_ratio: float | None
    blocked_reason: str | None
    reasons: tuple[str, ...] = ()

    @property
    def tradable(self) -> bool:
        return self.blocked_reason is None

    @property
    def headline(self) -> str:
        if self.blocked_reason:
            return f"{self.symbol}: {self.blocked_reason}"
        return (
            f"{self.symbol}: nota {self.score:.0f} "
            f"({self.session_label}, volume {self.volume_label})"
        )


@dataclass(frozen=True, slots=True)
class ScanResult:
    generated_at: datetime
    candidates: tuple[ScanCandidate, ...]

    @property
    def best(self) -> ScanCandidate | None:
        for candidato in self.candidates:
            if candidato.tradable:
                return candidato
        return None

    def top(self, limit: int) -> tuple[ScanCandidate, ...]:
        return tuple(c for c in self.candidates if c.tradable)[:limit]


def _cost_score(spread_points: float | None, atr_points: float | None) -> tuple[float, float | None]:
    """Nota de custo e a razao spread/ATR que a originou."""
    if not spread_points or not atr_points or atr_points <= 0:
        # Sem como medir o custo: nota neutra. Zerar seria punir o ativo por
        # uma lacuna de dado nossa, nao por uma condicao do mercado.
        return 50.0, None
    razao = spread_points / atr_points
    if razao <= COST_GOOD_RATIO:
        return 100.0, razao
    if razao >= COST_MAX_RATIO:
        return 0.0, razao
    proporcao = (razao - COST_GOOD_RATIO) / (COST_MAX_RATIO - COST_GOOD_RATIO)
    return 100.0 * (1.0 - proporcao), razao


def _atr_points(frame: pd.DataFrame, point: float) -> float | None:
    if frame is None or frame.empty or "atr_14" not in frame.columns or point <= 0:
        return None
    valor = frame["atr_14"].iloc[-1]
    if pd.isna(valor) or valor <= 0:
        return None
    return float(valor) / point


def evaluate_candidate(
    session: Session,
    *,
    symbol: str,
    now: datetime,
    timeframe: str = "M15",
    calendar: CalendarSnapshot | None = None,
    window: BlackoutWindow | None = None,
    min_impact: str = "HIGH",
) -> ScanCandidate:
    """Nota de um instrumento, so com dados locais."""
    estado = evaluate_symbol_session(symbol, now=now)
    session_score = _SESSION_SCORES.get(estado.rating, 40.0)

    symbol_row = SymbolRepository(session).get_by_name(symbol)
    candles = []
    if symbol_row is not None:
        candles = CandleRepository(session).get_recent(
            symbol_row.id, timeframe, features_module.required_lookback_bars() + 50, as_of=now
        )

    volume_score = _VOLUME_SCORES[VolumeLevel.UNKNOWN]
    volume_label = "sem dados"
    spread_points: float | None = None
    atr_points: float | None = None

    if len(candles) >= 2:
        perfil = build_volume_profile(candles)
        leitura = read_current_volume(candles, profile=perfil, now=now)
        volume_score = _VOLUME_SCORES.get(leitura.level, 40.0)
        volume_label = leitura.label
        spread_points = float(candles[-1].spread) if candles[-1].spread else None
        frame = features_module.build_candle_features(candles)
        atr_points = _atr_points(frame, float(symbol_row.point) if symbol_row else 0.0)

    cost_score, razao = _cost_score(spread_points, atr_points)

    nota = (
        WEIGHT_SESSION * session_score
        + WEIGHT_VOLUME * volume_score
        + WEIGHT_COST * cost_score
    )

    bloqueio: str | None = None
    if not estado.market_open:
        bloqueio = "Mercado fechado."
    elif len(candles) < 2:
        bloqueio = "Sem candles coletados."
    elif calendar is not None and calendar.usable:
        evento = find_blocking_event(
            list(calendar.events),
            symbol=symbol,
            now=now,
            window=window or BlackoutWindow(),
            min_impact=min_impact,
        )
        if evento is not None:
            bloqueio = describe(evento, now=now)

    razoes = [estado.headline, f"Volume: {volume_label}."]
    if razao is not None:
        razoes.append(f"Spread {spread_points:.0f} pontos = {razao * 100:.0f}% do ATR.")

    return ScanCandidate(
        symbol=symbol,
        score=nota,
        session_score=session_score,
        volume_score=volume_score,
        cost_score=cost_score,
        session_label=estado.label,
        volume_label=volume_label,
        spread_points=spread_points,
        atr_points=atr_points,
        spread_atr_ratio=razao,
        blocked_reason=bloqueio,
        reasons=tuple(razoes),
    )


def scan_market(
    session: Session,
    *,
    now: datetime,
    symbols: list[str] | None = None,
    timeframe: str = "M15",
    calendar: CalendarSnapshot | None = None,
    window: BlackoutWindow | None = None,
    min_impact: str = "HIGH",
) -> ScanResult:
    """Ordena os instrumentos coletados por oportunidade.

    Bloqueados ficam no fim da lista, e nao fora dela: o painel precisa
    poder mostrar por que um par que parecia bom nao entrou.
    """
    alvos = symbols
    if alvos is None:
        alvos = [linha.name for linha in SymbolRepository(session).list_active()]

    candidatos = [
        evaluate_candidate(
            session,
            symbol=symbol,
            now=now,
            timeframe=timeframe,
            calendar=calendar,
            window=window,
            min_impact=min_impact,
        )
        for symbol in alvos
    ]
    candidatos.sort(key=lambda item: (item.blocked_reason is not None, -item.score))
    return ScanResult(generated_at=now, candidates=tuple(candidatos))

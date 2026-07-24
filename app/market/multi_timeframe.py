"""Camada de dados multi-timeframe (Fase 18).

Le candles JA armazenadas no banco (mesma convencao de `quality check`/
`features build`/`monitor feed`: nenhum destes reconecta ao MetaTrader, so
operam sobre o que ja foi coletado via `collect candles`) e monta, para um
simbolo, um snapshot com a matriz de features de cada timeframe relevante
(`ANALYSIS_TIMEFRAMES`) de uma so vez.

Um timeframe sem historico suficiente (ex.: corretora sem MN1, ou operador
que ainda nao coletou W1/D1) vira um AVISO (`TimeframeSnapshot.is_sufficient
= False`), nunca uma excecao — o motor de analise (Fase 18.8) precisa
funcionar mesmo com cobertura parcial, sempre expondo a lacuna, nunca
escondendo-a (mesma filosofia de `app.market.data_quality`: reportar o
problema, nao mascarar). So um simbolo genuinamente desconhecido (nunca
coletado) e um erro de verdade (`SymbolNotFoundError`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market import features as features_module
from app.mt5.market_data import Timeframe

# Ordem do maior para o menor timeframe — mesma ordem de leitura usada na
# saida do relatorio (Fase 18.8): contexto macro primeiro, execucao por
# ultimo.
ANALYSIS_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.MN1,
    Timeframe.W1,
    Timeframe.D1,
    Timeframe.H4,
    Timeframe.H1,
    Timeframe.M30,
    Timeframe.M15,
    Timeframe.M5,
    Timeframe.M1,
)


class SymbolNotFoundError(Exception):
    """Simbolo nunca coletado (nao existe no banco) — diferente de um
    timeframe especifico sem dados suficientes, que e apenas um aviso."""


@dataclass(frozen=True, slots=True)
class TimeframeSnapshot:
    timeframe: Timeframe
    candles: list[object]
    features: pd.DataFrame | None
    bars_available: int
    bars_required: int
    is_sufficient: bool
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class MultiTimeframeSnapshot:
    symbol: str
    generated_at: datetime
    timeframes: dict[Timeframe, TimeframeSnapshot]

    def get(self, timeframe: Timeframe) -> TimeframeSnapshot | None:
        return self.timeframes.get(timeframe)

    def sufficient_timeframes(self) -> list[Timeframe]:
        """Timeframes com dados suficientes, na mesma ordem de
        `ANALYSIS_TIMEFRAMES` (nao a ordem de insercao do dict)."""
        return [
            tf
            for tf in ANALYSIS_TIMEFRAMES
            if self.timeframes.get(tf) is not None and self.timeframes[tf].is_sufficient
        ]


def _build_timeframe_snapshot(
    session: Session, *, symbol_id: int, timeframe: Timeframe, extra_bars: int
) -> TimeframeSnapshot:
    bars_required = features_module.required_lookback_bars() + extra_bars
    candles = CandleRepository(session).get_recent(symbol_id, timeframe.value, bars_required)
    bars_available = len(candles)

    warnings: list[str] = []
    feature_frame: pd.DataFrame | None = None
    if bars_available < 2:
        warnings.append(
            f"{timeframe.value}: nenhuma candle coletada ainda (ou apenas 1) — "
            "rode 'collect candles' para este timeframe antes de incluir na analise."
        )
    else:
        feature_frame = features_module.build_candle_features(candles)
        if bars_available < bars_required:
            warnings.append(
                f"{timeframe.value}: apenas {bars_available} candle(s) disponivel(is), "
                f"{bars_required} recomendado(s) para features sem NaN (EMA200 etc.) — "
                "resultado pode conter lacunas nas features mais longas."
            )

    is_sufficient = bars_available >= 2 and len(warnings) == 0

    return TimeframeSnapshot(
        timeframe=timeframe,
        candles=list(candles),
        features=feature_frame,
        bars_available=bars_available,
        bars_required=bars_required,
        is_sufficient=is_sufficient,
        warnings=warnings,
    )


def build_multi_timeframe_snapshot(
    session: Session,
    *,
    symbol: str,
    timeframes: tuple[Timeframe, ...] = ANALYSIS_TIMEFRAMES,
    extra_bars: int = 50,
    now: datetime,
) -> MultiTimeframeSnapshot:
    """Monta o snapshot multi-timeframe de `symbol` a partir de candles ja
    armazenadas no banco (nunca reconecta ao MetaTrader).

    `now` e explicito (nunca `datetime.now()` interno) para manter a funcao
    determinista em teste, mesma convencao de `fetch_server_time`/
    `check_feed_health`."""
    symbol_row = SymbolRepository(session).get_by_name(symbol)
    if symbol_row is None:
        raise SymbolNotFoundError(
            f"Simbolo '{symbol}' nao encontrado no banco. Colete dados primeiro "
            "('collect candles')."
        )

    snapshots = {
        tf: _build_timeframe_snapshot(
            session, symbol_id=symbol_row.id, timeframe=tf, extra_bars=extra_bars
        )
        for tf in timeframes
    }

    return MultiTimeframeSnapshot(symbol=symbol, generated_at=now, timeframes=snapshots)

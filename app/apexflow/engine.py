"""Orquestrador do ApexFlow AI: dos dados brutos a decisao registrada.

Une os motores independentes na ordem em que eles dependem uns dos outros,
sem que nenhum deles precise conhecer os outros:

```
ticks ──► tick_flow ──┬──► spread ────┐
                      ├──► volatility ┤
candles ──► features ─┼──► momentum ──┼──► context ──► feature vector ──► decisao
                      ├──► liquidity ─┤                      │
                      └──► mtf ───────┘                      └──► journal
```

O resultado (`ApexFlowAnalysis`) carrega TODAS as leituras intermediarias,
nao so a decisao final: e o que permite ao painel explicar o "porque" e ao
Learning Engine reavaliar o passado sem reprocessar dados de mercado.

Este modulo le do banco (candles/ticks) mas continua sem enviar ordem — a
execucao pertence a `app.execution`. A separacao e deliberada: decidir e
executar falham por motivos diferentes e devem poder ser testadas em
separado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from app.apexflow.config import ApexFlowConfig
from app.apexflow.context import MarketContext, classify_market_context
from app.apexflow.decision import ApexFlowDecision, ProbabilityModel, decide
from app.apexflow.features import FeatureVector, build_feature_vector
from app.apexflow.liquidity import LiquidityReading, read_liquidity
from app.apexflow.momentum import MomentumReading, read_momentum
from app.apexflow.mtf import (
    MultiTimeframeView,
    analyze_timeframes,
    ensure_entry_timeframe,
)
from app.apexflow.spread import SpreadReading, read_spread
from app.apexflow.tick_flow import TickFlowMetrics, compute_tick_flow
from app.apexflow.volatility import VolatilityReading, read_volatility
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.tick_repository import TickRepository
from app.market.features import build_candle_features, required_lookback_bars
from app.market.multi_timeframe import SymbolNotFoundError
from app.market.price_action import CandlestickPattern, detect_patterns
from app.market.regimes import MarketRegime, classify_latest_regime
from app.market.sessions import SymbolSessionState, evaluate_symbol_session
from app.market.volume_profile import VolumeReading, read_current_volume
from app.mt5.market_data import Timeframe
from app.news.provider import NewsAssessment

CONTEXT_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.H1,
    Timeframe.M15,
    Timeframe.M5,
    Timeframe.M1,
)
"""Os quatro papeis do `mtf`. H1 entra so como direcao macro."""

VOLUME_PROFILE_BARS = 1_500


@dataclass(frozen=True, slots=True)
class ApexFlowAnalysis:
    """Tudo o que o motor apurou neste ciclo, decisao inclusa."""

    symbol: str
    timeframe: Timeframe
    generated_at: datetime
    decision: ApexFlowDecision
    vector: FeatureVector
    context: MarketContext
    flow: TickFlowMetrics
    spread: SpreadReading
    volatility: VolatilityReading
    momentum: MomentumReading
    liquidity: LiquidityReading
    mtf: MultiTimeframeView
    session: SymbolSessionState
    volume: VolumeReading
    patterns: tuple[CandlestickPattern, ...]
    regime: MarketRegime | None
    candles: tuple = ()
    """Candles do timeframe de entrada, na ordem em que foram analisadas.
    Viajam junto para que o adaptador de execucao possa localizar niveis de
    estrutura reais (swings/order blocks) sem reconsultar o banco."""

    warnings: tuple[str, ...] = ()

    @property
    def is_entry(self) -> bool:
        return self.decision.is_entry


def _features_for(
    repository: CandleRepository, symbol_id: int, timeframe: Timeframe, *, bars: int
) -> tuple[list, pd.DataFrame]:
    candles = repository.get_recent(symbol_id, timeframe.value, bars)
    if len(candles) < 2:
        return candles, pd.DataFrame()
    try:
        return candles, build_candle_features(candles)
    except ValueError:
        return candles, pd.DataFrame()


def _regime_or_none(features: pd.DataFrame) -> MarketRegime | None:
    if features.empty:
        return None
    try:
        return classify_latest_regime(features)
    except ValueError:
        return None


def analyze(
    session: Session,
    *,
    symbol: str,
    timeframe: Timeframe,
    config: ApexFlowConfig,
    point: float | None = None,
    news: NewsAssessment | None = None,
    model: ProbabilityModel | None = None,
    now: datetime | None = None,
) -> ApexFlowAnalysis:
    """Roda o ciclo completo de analise e devolve a decisao com o porque.

    Levanta `SymbolNotFoundError` para simbolo nunca coletado (erro real
    que exige acao humana) e `UnsupportedEntryTimeframeError` se alguem
    tentar decidir entrada em um timeframe de contexto — as unicas duas
    excecoes; falta de dado de mercado sempre vira abstencao explicada.
    """
    ensure_entry_timeframe(timeframe)
    resolved_now = (now or datetime.now(UTC)).astimezone(UTC)

    symbol_row = SymbolRepository(session).get_by_name(symbol)
    if symbol_row is None:
        raise SymbolNotFoundError(f"Simbolo '{symbol}' nunca foi coletado.")
    resolved_point = point if point is not None else float(symbol_row.point)

    candle_repository = CandleRepository(session)
    lookback = required_lookback_bars() + 20

    entry_candles, entry_features = _features_for(
        candle_repository, symbol_row.id, timeframe, bars=lookback
    )
    features_by_timeframe: dict[Timeframe, pd.DataFrame] = {}
    for context_timeframe in CONTEXT_TIMEFRAMES:
        if context_timeframe == timeframe:
            features_by_timeframe[context_timeframe] = entry_features
            continue
        _, frame = _features_for(
            candle_repository, symbol_row.id, context_timeframe, bars=lookback
        )
        features_by_timeframe[context_timeframe] = frame

    ticks = TickRepository(session).get_range(
        symbol_row.id,
        start=resolved_now - timedelta(seconds=config.tick_window_seconds),
        end=resolved_now,
    )

    flow = compute_tick_flow(ticks, point=resolved_point, now=resolved_now)
    volatility = read_volatility(
        entry_features,
        ticks,
        point=resolved_point,
        min_atr_points=config.min_atr_points,
    )
    target_points = (
        volatility.atr_points * config.risk_reward_min
        if volatility.atr_points is not None
        else None
    )
    fallback_spread = (
        float(entry_candles[-1].spread) if entry_candles else None
    )
    spread = read_spread(
        flow,
        target_points=target_points,
        max_spread_points=config.max_spread_points,
        max_widening_ratio=config.max_spread_widening,
        max_spread_to_target=config.max_spread_to_target,
        fallback_spread_points=fallback_spread,
    )
    momentum = read_momentum(entry_features, flow)
    liquidity = read_liquidity(entry_candles)
    mtf = analyze_timeframes(features_by_timeframe)
    regime = _regime_or_none(entry_features)

    session_state = evaluate_symbol_session(symbol, now=resolved_now)
    volume_candles = candle_repository.get_recent(
        symbol_row.id, Timeframe.M15.value, VOLUME_PROFILE_BARS
    )
    volume = read_current_volume(volume_candles, now=resolved_now)

    context = classify_market_context(
        regime=regime,
        flow=flow,
        volatility=volatility,
        spread=spread,
        liquidity=liquidity,
        news=news,
        now=resolved_now,
    )

    patterns = detect_patterns(entry_candles) if entry_candles else []
    vector = build_feature_vector(
        symbol=symbol,
        timeframe=timeframe.value,
        features=entry_features,
        flow=flow,
        spread=spread,
        volatility=volatility,
        momentum=momentum,
        liquidity=liquidity,
        mtf=mtf,
        session=session_state,
        volume=volume,
        context=context,
        patterns=patterns,
        now=resolved_now,
    )

    decision = decide(
        vector,
        context=context,
        momentum=momentum,
        mtf=mtf,
        liquidity=liquidity,
        spread=spread,
        volatility=volatility,
        flow=flow,
        config=config,
        model=model,
        now=resolved_now,
    )

    warnings: list[str] = []
    if entry_features.empty:
        warnings.append(
            f"Sem matriz de features em {timeframe.value} — a decisao nasce "
            "necessariamente como abstencao."
        )
    if not ticks:
        warnings.append(
            f"Nenhum tick nos ultimos {config.tick_window_seconds}s: o fluxo nao "
            "pode ser confirmado (habilite a coleta de ticks no plano MT5)."
        )
    warnings.extend(flow.warnings)

    return ApexFlowAnalysis(
        symbol=symbol,
        timeframe=timeframe,
        generated_at=resolved_now,
        decision=decision,
        vector=vector,
        context=context,
        flow=flow,
        spread=spread,
        volatility=volatility,
        momentum=momentum,
        liquidity=liquidity,
        mtf=mtf,
        session=session_state,
        volume=volume,
        patterns=tuple(patterns),
        regime=regime,
        candles=tuple(entry_candles),
        warnings=tuple(warnings),
    )

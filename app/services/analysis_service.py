"""Servico de orquestracao do motor de analise Price Action / SMC /
multi-timeframe (Fase 18.8).

So compoe modulos ja existentes (Fases 18.1-18.7) — nenhuma logica NOVA de
deteccao vive aqui, so a montagem do relatorio final (`AnalysisReport`) e
os templates deterministicos de confluencia/justificativa/motivo de
rejeicao (nunca texto livre/gerado, para permanecer totalmente testavel).

100% consultivo: nao gera ordens, nao alimenta paper/demo trading, nao
importa nada de `app.execution`/`app.paper_trading`/`app.risk` — a pipeline
de execucao real permanece inteiramente separada e intocada."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

import pandas as pd
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.market.multi_timeframe import (
    ANALYSIS_TIMEFRAMES,
    MultiTimeframeSnapshot,
    build_multi_timeframe_snapshot,
)
from app.market.price_action import CandlestickPattern, PatternDirection, detect_patterns
from app.market.regimes import Trend, classify_latest_regime
from app.market.scoring import (
    OpportunityScore,
    ScoreWeights,
    compute_opportunity_score,
    score_correlation,
    score_fundamentals,
    score_liquidity,
    score_news,
    score_price_action,
    score_structure,
    score_volume,
)
from app.market.smc import (
    compute_premium_discount,
    detect_equal_highs_lows,
    detect_fair_value_gaps,
    detect_liquidity_sweeps,
    detect_order_blocks,
    update_mitigation_status,
)
from app.market.structure import (
    SwingKind,
    cluster_swing_levels,
    detect_structure_events,
    detect_swings,
    label_swing_structure,
)
from app.market.trade_levels import TradeLevels, compute_trade_levels
from app.market.volume_analysis import detect_volume_events
from app.mt5.market_data import Timeframe
from app.news.factory import get_fundamentals_provider, get_news_provider
from app.news.provider import FundamentalsProvider, NewsProvider, ProviderStatus
from app.news.unconfigured import skipped_fundamentals, skipped_news
from app.strategies.base import SignalDirection

_MISSING_HIGHER_TIMEFRAME_PENALTY_PER_TF = 5.0
_MISSING_HIGHER_TIMEFRAME_PENALTY_MAX = 20.0


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    symbol: str
    timeframe: Timeframe
    generated_at: datetime
    trend: Trend
    dominant_pattern: CandlestickPattern | None
    confluences: list[str]
    multi_timeframe_alignment: dict[Timeframe, str]
    score: OpportunityScore
    probability_estimate: float
    trade_levels: TradeLevels | None
    justification: list[str]
    recommendation: Literal["ENTER", "DO_NOT_ENTER"]
    rejection_reasons: list[str]


def _timeframe_trend_label(snapshot: MultiTimeframeSnapshot, timeframe: Timeframe) -> str:
    tf_snapshot = snapshot.get(timeframe)
    if tf_snapshot is None or not tf_snapshot.is_sufficient or tf_snapshot.features is None:
        return "SEM_DADOS"
    try:
        return classify_latest_regime(tf_snapshot.features).trend.value
    except ValueError:
        return "SEM_DADOS"


_MIN_VOLUME_SCORE = 60.0

# Quantas barras exigir de cada timeframe. Os operacionais (o de entrada e
# os dois imediatamente acima) precisam das features completas — e neles que
# a decisao acontece. Os de contexto macro so precisam existir em quantidade
# util para dizer a direcao: exigir 200 candles mensais seriam 16 ANOS de
# historico, que corretora nenhuma entrega e que bloqueava o sistema para
# sempre.
_CONTEXT_MIN_BARS = 30


def _coverage_blockers(
    snapshot: MultiTimeframeSnapshot, *, primary_timeframe: Timeframe
) -> list[str]:
    """Cobertura insuficiente para decidir, em linguagem acionavel.

    Verificacao 100% local: roda antes de qualquer chamada paga.
    """
    index = ANALYSIS_TIMEFRAMES.index(primary_timeframe)
    # O de entrada e os dois acima dele (ou o que houver).
    operational = ANALYSIS_TIMEFRAMES[max(0, index - 2) : index + 1]

    blockers: list[str] = []

    incompletos = [
        tf.value
        for tf in operational
        if (tf_snapshot := snapshot.get(tf)) is None or not tf_snapshot.is_sufficient
    ]
    if incompletos:
        blockers.append(
            "Cobertura insuficiente nos timeframes operacionais "
            f"({', '.join(incompletos)}) — colete mais candles antes de operar."
        )

    sem_contexto = [
        tf.value
        for tf in ANALYSIS_TIMEFRAMES[: max(0, index - 2)]
        if (tf_snapshot := snapshot.get(tf)) is None
        or tf_snapshot.bars_available < _CONTEXT_MIN_BARS
    ]
    if sem_contexto:
        blockers.append(
            f"Contexto macro ausente ({', '.join(sem_contexto)}): menos de "
            f"{_CONTEXT_MIN_BARS} candles coletados."
        )
    return blockers


def analyze_symbol(
    session: Session,
    *,
    symbol: str,
    primary_timeframe: Timeframe = Timeframe.M15,
    weights: ScoreWeights = ScoreWeights(),
    threshold: float = 90.0,
    news_provider: NewsProvider | None = None,
    fundamentals_provider: FundamentalsProvider | None = None,
    now: datetime | None = None,
    enforce_gates: bool = True,
    as_of: datetime | None = None,
) -> AnalysisReport:
    """Analisa `symbol` no `primary_timeframe`, com contexto dos demais
    timeframes (`ANALYSIS_TIMEFRAMES`) para alinhamento de tendencia.

    Nunca levanta excecao por falta de dados — timeframes/fatores sem
    informacao suficiente contribuem neutro (score 50) e a lacuna fica
    visivel na justificativa, nunca escondida. So propaga
    `SymbolNotFoundError` (simbolo nunca coletado) e `NotImplementedError`
    (chave da API AIsa configurada sem um provedor real implementado — ver
    `app.news.factory`), ambos erros genuinos que exigem acao humana."""
    resolved_now = now if now is not None else datetime.now(tz=None).astimezone()
    settings = get_settings()

    snapshot = build_multi_timeframe_snapshot(
        session,
        symbol=symbol,
        timeframes=ANALYSIS_TIMEFRAMES,
        now=resolved_now,
        as_of=as_of,
    )

    primary = snapshot.get(primary_timeframe)
    candles: list = []
    features: pd.DataFrame = pd.DataFrame()
    has_primary_data = False
    if primary is not None and primary.is_sufficient and primary.features is not None:
        candles = list(primary.candles)
        features = primary.features
        has_primary_data = True

    patterns = detect_patterns(candles) if candles else []
    swings = detect_swings(candles) if candles else []
    labels = label_swing_structure(swings)
    events = detect_structure_events(candles, labels) if candles else []
    sr_levels = cluster_swing_levels(swings)
    equal_levels = detect_equal_highs_lows(swings)

    order_blocks = detect_order_blocks(candles, events) if candles else []
    order_blocks = update_mitigation_status(order_blocks, candles) if candles else order_blocks
    fvgs = detect_fair_value_gaps(candles) if candles else []

    sweeps = []
    if candles:
        sweeps.extend(detect_liquidity_sweeps(candles, sr_levels))
        sweeps.extend(detect_liquidity_sweeps(candles, equal_levels))

    swing_highs = [s for s in swings if s.kind == SwingKind.HIGH]
    swing_lows = [s for s in swings if s.kind == SwingKind.LOW]
    pd_zone = None
    if swing_highs and swing_lows:
        latest_high = max(swing_highs, key=lambda s: s.index)
        latest_low = max(swing_lows, key=lambda s: s.index)
        pd_zone = compute_premium_discount(latest_high, latest_low)

    volume_events = detect_volume_events(candles, features, swings) if candles else []

    dominant_pattern = max(patterns, key=lambda p: p.index) if patterns else None

    trend = Trend.SIDEWAYS
    if has_primary_data:
        try:
            trend = classify_latest_regime(features).trend
        except ValueError:
            trend = Trend.SIDEWAYS

    multi_timeframe_alignment = {
        tf: _timeframe_trend_label(snapshot, tf) for tf in ANALYSIS_TIMEFRAMES
    }

    structure_factor = score_structure(events, labels)
    higher_timeframes = ANALYSIS_TIMEFRAMES[: ANALYSIS_TIMEFRAMES.index(primary_timeframe)]
    missing_higher = [
        tf
        for tf in higher_timeframes
        if snapshot.get(tf) is None or not snapshot.get(tf).is_sufficient  # type: ignore[union-attr]
    ]
    if missing_higher:
        penalty = min(
            _MISSING_HIGHER_TIMEFRAME_PENALTY_MAX,
            _MISSING_HIGHER_TIMEFRAME_PENALTY_PER_TF * len(missing_higher),
        )
        structure_factor = replace(
            structure_factor,
            raw_score=max(0.0, structure_factor.raw_score - penalty),
            rationale=[
                *structure_factor.rationale,
                f"Cobertura multi-timeframe incompleta: {len(missing_higher)} timeframe(s) "
                f"superior(es) sem dados suficientes "
                f"({', '.join(tf.value for tf in missing_higher)}) — penalidade aplicada, "
                "nunca ignorada.",
            ],
        )

    price_action_factor = score_price_action(patterns)
    liquidity_factor = score_liquidity(order_blocks, fvgs, sweeps, pd_zone)
    volume_factor = score_volume(volume_events)

    # ------------------------------------------------------------------
    # Portoes locais ANTES da API paga.
    #
    # Cobertura e volume sao verificados com dados que ja estao no banco,
    # de graca. Consultar a MarketPulse para so entao descobrir que a
    # entrada ja estava bloqueada era queimar credito por analise
    # natimorta — foi exatamente o que esgotou a cota do usuario.
    # ------------------------------------------------------------------
    local_block_reasons: list[str] = []
    if enforce_gates:
        local_block_reasons.extend(
            _coverage_blockers(snapshot, primary_timeframe=primary_timeframe)
        )
        if volume_factor.raw_score < _MIN_VOLUME_SCORE:
            local_block_reasons.append(
                f"Volume nao favoravel (score {volume_factor.raw_score:.1f}, "
                f"minimo {_MIN_VOLUME_SCORE:.0f})."
            )

    if local_block_reasons:
        motivo = (
            "MarketPulse nao consultada: a entrada ja estava bloqueada por "
            "verificacao local (economia de cota)."
        )
        news_assessment = skipped_news(motivo)
        fundamentals_assessment = skipped_fundamentals(motivo)
    else:
        resolved_news_provider = news_provider or get_news_provider(session, settings)
        resolved_fundamentals_provider = fundamentals_provider or get_fundamentals_provider(
            session, settings
        )
        news_assessment = resolved_news_provider.fetch_assessment(symbol, now=resolved_now)
        fundamentals_assessment = resolved_fundamentals_provider.fetch_assessment(
            symbol, now=resolved_now
        )

    news_factor = score_news(news_assessment)
    fundamentals_factor = score_fundamentals(fundamentals_assessment)
    correlation_factor = score_correlation()

    score = compute_opportunity_score(
        symbol=symbol,
        timeframe=primary_timeframe,
        generated_at=resolved_now,
        structure=structure_factor,
        price_action=price_action_factor,
        liquidity=liquidity_factor,
        volume=volume_factor,
        news=news_factor,
        fundamentals=fundamentals_factor,
        correlation=correlation_factor,
        weights=weights,
        threshold=threshold,
    )

    # Portoes que dependem da resposta externa. Os locais ja rodaram acima;
    # aqui so entram os que exigem a API — e apenas quando ela foi de fato
    # consultada, para nao transformar uma economia deliberada em bloqueio.
    hard_block_reasons: list[str] = list(local_block_reasons)
    if enforce_gates and not local_block_reasons:
        if news_assessment.status != ProviderStatus.OK:
            hard_block_reasons.append(
                "Noticias/calendario sem confirmacao valida; entrada bloqueada por seguranca."
            )
        if fundamentals_assessment.status != ProviderStatus.OK:
            hard_block_reasons.append(
                "Fundamentos/macro sem confirmacao valida; entrada bloqueada por seguranca."
            )

        high_impact_deadline = resolved_now + timedelta(minutes=60)
        if any(
            item.impact == "HIGH"
            and resolved_now <= item.published_at <= high_impact_deadline
            for item in news_assessment.items
        ):
            hard_block_reasons.append(
                "Noticia de alto impacto prevista para os proximos 60 minutos."
            )

    if hard_block_reasons:
        score = replace(
            score,
            recommendation="DO_NOT_ENTER",
            reasons_below_threshold=[
                *score.reasons_below_threshold,
                *hard_block_reasons,
            ],
        )

    confluences: list[str] = []
    for event in events:
        confluences.append(f"{primary_timeframe.value}: {event.type.value} {event.direction.value}")
    for ob in order_blocks:
        if not ob.mitigated:
            confluences.append(
                f"{primary_timeframe.value}: Order Block {ob.direction.value} nao mitigado"
            )
    for gap in fvgs:
        if not gap.filled:
            confluences.append(
                f"{primary_timeframe.value}: FVG {gap.direction.value} nao preenchido"
            )
    for sweep in sweeps:
        if sweep.reversal_confirmed:
            confluences.append(
                f"{primary_timeframe.value}: {sweep.kind.value} com reversao confirmada"
            )
    if dominant_pattern is not None:
        confluences.append(
            f"{primary_timeframe.value}: padrao {dominant_pattern.name.value} "
            f"({dominant_pattern.direction.value})"
        )

    justification = [
        f"{factor.name} ({factor.weight * 100:.0f}%): {'; '.join(factor.rationale)}"
        for factor in score.factors
    ]

    trade_levels: TradeLevels | None = None
    if score.recommendation == "ENTER" and candles:
        if events:
            latest_event = max(events, key=lambda e: e.index)
            direction = (
                SignalDirection.LONG
                if latest_event.direction == PatternDirection.BULLISH
                else SignalDirection.SHORT
            )
        elif trend == Trend.DOWN:
            direction = SignalDirection.SHORT
        else:
            direction = SignalDirection.LONG

        entry_price = float(candles[-1].close)
        atr_series = features["atr_14"] if "atr_14" in features.columns else pd.Series(dtype=float)
        atr_value = (
            float(atr_series.iloc[-1])
            if not atr_series.empty and not pd.isna(atr_series.iloc[-1])
            else float(candles[-1].high) - float(candles[-1].low)
        )
        if atr_value <= 0:
            atr_value = max(entry_price * 0.001, 0.0001)

        if direction == SignalDirection.LONG:
            candidates = [s.price for s in swing_lows if s.price < entry_price] + [
                ob.low
                for ob in order_blocks
                if ob.direction == PatternDirection.BULLISH and ob.low < entry_price
            ]
            structure_stop_price = max(candidates) if candidates else None
        else:
            candidates = [s.price for s in swing_highs if s.price > entry_price] + [
                ob.high
                for ob in order_blocks
                if ob.direction == PatternDirection.BEARISH and ob.high > entry_price
            ]
            structure_stop_price = min(candidates) if candidates else None

        trade_levels = compute_trade_levels(
            direction=direction,
            entry_price=entry_price,
            atr=atr_value,
            structure_stop_price=structure_stop_price,
        )

    return AnalysisReport(
        symbol=symbol,
        timeframe=primary_timeframe,
        generated_at=resolved_now,
        trend=trend,
        dominant_pattern=dominant_pattern,
        confluences=confluences,
        multi_timeframe_alignment=multi_timeframe_alignment,
        score=score,
        probability_estimate=score.total_score,
        trade_levels=trade_levels,
        justification=justification,
        recommendation=score.recommendation,
        rejection_reasons=score.reasons_below_threshold,
    )

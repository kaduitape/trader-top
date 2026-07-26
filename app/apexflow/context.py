"""Market Context Engine: em que mercado estamos, antes de qualquer entrada.

Antes de perguntar "compro ou vendo?", o motor pergunta "que mercado e
este?". Cada estado usa parametros diferentes — nunca uma configuracao fixa
para todos, que e exatamente o erro que faz um robo lucrar tres meses e
devolver tudo no quarto.

Os nove estados sao avaliados em ordem de PRIORIDADE, do mais perigoso ao
mais operavel, e o primeiro que casa vence. A ordem importa: um mercado
simultaneamente explosivo e com spread largo deve ser reportado pelo
motivo que bloqueia, nao pelo que parece oportunidade.

Nao ha estado "bom" e "ruim" — ha estados que pedem operacionais
diferentes (`TRENDING` favorece continuidade, `RANGING` favorece reversao)
e estados em que nenhum operacional tem vantagem (`DEAD`, `ILLIQUID`,
`WIDE_SPREAD`, `POST_NEWS`, `EXPLOSIVE`, `LIQUIDITY_HUNT`), que o motor de
decisao traduz em NAO OPERAR.

Reaproveita o regime por regras ja existente (`app.market.regimes`) e o
enriquece com fluxo de ticks, liquidez e calendario — nenhuma formula de
indicador nasce aqui.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.apexflow.liquidity import LiquidityReading, LiquidityState
from app.apexflow.spread import SpreadReading, SpreadVerdict
from app.apexflow.tick_flow import TickFlowMetrics
from app.apexflow.volatility import VolatilityReading, VolatilityState
from app.market.regimes import MarketRegime, Trend
from app.news.provider import NewsAssessment, ProviderStatus


class MarketContextState(enum.StrEnum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    EXPLOSIVE = "EXPLOSIVE"
    DEAD = "DEAD"
    LIQUIDITY_HUNT = "LIQUIDITY_HUNT"
    POST_NEWS = "POST_NEWS"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    WIDE_SPREAD = "WIDE_SPREAD"
    ILLIQUID = "ILLIQUID"
    UNKNOWN = "UNKNOWN"


CONTEXT_LABELS: dict[MarketContextState, str] = {
    MarketContextState.TRENDING: "Mercado em tendencia",
    MarketContextState.RANGING: "Mercado lateral",
    MarketContextState.EXPLOSIVE: "Mercado explosivo",
    MarketContextState.DEAD: "Mercado morto",
    MarketContextState.LIQUIDITY_HUNT: "Mercado cacando liquidez",
    MarketContextState.POST_NEWS: "Mercado apos noticia",
    MarketContextState.HIGH_VOLATILITY: "Mercado extremamente volatil",
    MarketContextState.WIDE_SPREAD: "Mercado com spread alto",
    MarketContextState.ILLIQUID: "Mercado sem liquidez",
    MarketContextState.UNKNOWN: "Contexto indeterminado",
}

TRADEABLE_STATES: frozenset[MarketContextState] = frozenset(
    {MarketContextState.TRENDING, MarketContextState.RANGING}
)
"""Os unicos estados em que uma entrada e considerada. Todos os demais
levam o motor de decisao a NAO OPERAR — abster-se e a resposta padrao."""

EXPLOSIVE_TICK_RATE = 3.0
"""Ticks/s acima disso, combinado com expansao de volatilidade, e explosao
de fluxo (evento), nao mercado operavel."""

DEAD_TICK_RATE = 0.15
NEWS_WINDOW_MINUTES = 30
"""Janela apos um evento de alto impacto em que o mercado ainda esta
digerindo — spread e slippage se comportam de forma atipica."""

HIGH_VOLATILITY_ATR_RATIO = 2.0


@dataclass(frozen=True, slots=True)
class MarketContext:
    state: MarketContextState
    trend: Trend
    is_tradeable: bool
    confidence: float
    """0-1: quao claramente as evidencias apontam para este estado."""

    reasons: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def label(self) -> str:
        return CONTEXT_LABELS[self.state]


def _recent_high_impact_news(
    news: NewsAssessment | None, *, now: datetime, window_minutes: int
) -> bool:
    if news is None or news.status != ProviderStatus.OK:
        return False
    window_start = now - timedelta(minutes=window_minutes)
    return any(
        item.impact == "HIGH" and window_start <= item.published_at <= now
        for item in news.items
    )


def classify_market_context(
    *,
    regime: MarketRegime | None,
    flow: TickFlowMetrics,
    volatility: VolatilityReading,
    spread: SpreadReading,
    liquidity: LiquidityReading,
    news: NewsAssessment | None = None,
    now: datetime | None = None,
) -> MarketContext:
    """Elege UM estado, na ordem de prioridade documentada no modulo."""
    resolved_now = now or datetime.now(tz=None).astimezone()
    reasons: list[str] = []
    blockers: list[str] = []

    if regime is None:
        return MarketContext(
            state=MarketContextState.UNKNOWN,
            trend=Trend.SIDEWAYS,
            is_tradeable=False,
            confidence=0.0,
            reasons=("Regime base indisponivel (dados insuficientes).",),
            blockers=("Contexto de mercado nao pode ser determinado.",),
        )

    # 1. Spread alto — bloqueia qualquer coisa, inclusive tendencia perfeita.
    if spread.verdict in (SpreadVerdict.TOO_WIDE, SpreadVerdict.WIDENING):
        blockers.append(spread.reasons[-1] if spread.reasons else spread.label)
        return MarketContext(
            state=MarketContextState.WIDE_SPREAD,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.9,
            reasons=tuple(spread.reasons),
            blockers=tuple(blockers),
        )

    # 2. Explosao de fluxo / evento extraordinario.
    if regime.is_extraordinary_event or (
        flow.ticks_per_second is not None
        and flow.ticks_per_second >= EXPLOSIVE_TICK_RATE
        and volatility.state == VolatilityState.EXPANDING
    ):
        reasons.append(
            f"Fluxo de {flow.ticks_per_second:.1f} ticks/s com volatilidade em "
            "expansao."
            if flow.ticks_per_second is not None
            else "Amplitude muito acima do normal para este par."
        )
        blockers.append(
            "Mercado explosivo: preco e spread se movem mais rapido que a "
            "execucao — nenhum operacional tem vantagem estatistica."
        )
        return MarketContext(
            state=MarketContextState.EXPLOSIVE,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.85,
            reasons=tuple(reasons),
            blockers=tuple(blockers),
        )

    # 3. Janela pos-noticia.
    if _recent_high_impact_news(news, now=resolved_now, window_minutes=NEWS_WINDOW_MINUTES):
        blockers.append(
            f"Evento de alto impacto nos ultimos {NEWS_WINDOW_MINUTES} minutos — "
            "o mercado ainda esta digerindo a informacao."
        )
        return MarketContext(
            state=MarketContextState.POST_NEWS,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.9,
            reasons=("Calendario confirmou evento de alto impacto recente.",),
            blockers=tuple(blockers),
        )

    # 4. Caca de liquidez em andamento.
    if liquidity.blocks_entry:
        blockers.append(
            "Manipulacao de liquidez em andamento: aguardar a rejeicao se "
            "confirmar antes de considerar qualquer entrada."
        )
        return MarketContext(
            state=MarketContextState.LIQUIDITY_HUNT,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.75,
            reasons=tuple(liquidity.reasons),
            blockers=tuple(blockers),
        )

    # 5. Volatilidade extrema (sem chegar a explosao de fluxo).
    if volatility.atr_ratio is not None and volatility.atr_ratio >= HIGH_VOLATILITY_ATR_RATIO:
        blockers.append(
            f"Volatilidade {volatility.atr_ratio:.1f}x a media: o stop necessario "
            "ficaria largo demais para o alvo disponivel."
        )
        return MarketContext(
            state=MarketContextState.HIGH_VOLATILITY,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.8,
            reasons=tuple(volatility.reasons),
            blockers=tuple(blockers),
        )

    # 6. Mercado morto (sem movimento) e 7. sem liquidez (sem fluxo) sao
    # diferentes: o primeiro tem ticks mas nao anda, o segundo nem ticks tem.
    if volatility.state == VolatilityState.INSUFFICIENT:
        blockers.append(
            "Mercado morto: a amplitude atual nao paga spread, comissao e "
            "slippage de nenhuma operacao."
        )
        return MarketContext(
            state=MarketContextState.DEAD,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.85,
            reasons=tuple(volatility.reasons),
            blockers=tuple(blockers),
        )

    if not regime.liquidity_adequate or (
        flow.ticks_per_second is not None and flow.ticks_per_second <= DEAD_TICK_RATE
    ):
        blockers.append(
            "Sem liquidez: o fluxo de ticks e raro demais para confiar no preco "
            "de execucao."
        )
        return MarketContext(
            state=MarketContextState.ILLIQUID,
            trend=regime.trend,
            is_tradeable=False,
            confidence=0.8,
            reasons=(
                f"Taxa de ticks: {flow.ticks_per_second:.2f}/s."
                if flow.ticks_per_second is not None
                else "Volume relativo abaixo do minimo do regime.",
            ),
            blockers=tuple(blockers),
        )

    # 8/9. Operavel: tendencia ou lateral.
    if regime.trend in (Trend.UP, Trend.DOWN):
        reasons.append(
            f"Tendencia {'de alta' if regime.trend == Trend.UP else 'de baixa'} "
            "confirmada por ADX, com spread e liquidez adequados."
        )
        if liquidity.state == LiquidityState.SWEEP_REVERSED:
            reasons.append(
                "Sweep recente ja revertido — liquidez limpa para continuidade."
            )
        return MarketContext(
            state=MarketContextState.TRENDING,
            trend=regime.trend,
            is_tradeable=True,
            confidence=0.8 if not regime.is_transition else 0.6,
            reasons=tuple(reasons),
            blockers=(),
        )

    reasons.append("Sem direcao definida (ADX abaixo do limiar), volatilidade comportada.")
    return MarketContext(
        state=MarketContextState.RANGING,
        trend=Trend.SIDEWAYS,
        is_tradeable=True,
        confidence=0.7,
        reasons=tuple(reasons),
        blockers=(),
    )

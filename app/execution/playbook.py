"""Seletor automatico do "melhor operacional" para uma moeda AGORA.

Responde tres perguntas, nesta ordem, e nunca pula nenhuma:

1. **Da para operar?** — mercado aberto, longe do fechamento semanal,
   volume compativel, spread adequado, sem evento extraordinario. Qualquer
   "nao" produz `STAND_ASIDE`: ficar de fora e uma decisao valida e
   frequente, nao uma falha.
2. **Como operar?** — o regime vigente (`app.market.regimes`) combinado com
   a sessao (`app.market.sessions`) e o volume relativo
   (`app.market.volume_profile`) escolhe UMA das estrategias ja
   implementadas e testadas em `app.strategies.registry`. Nenhuma logica de
   entrada nova nasce aqui; este modulo so ELEGE entre operacionais que ja
   existem e ja foram validados por backtest.
3. **Com qual folga?** — timeframe de execucao, score minimo exigido e
   multiplicador de risco.

Duas invariantes de seguranca, impostas por codigo e cobertas por teste:

- O score minimo escolhido **nunca fica abaixo** do configurado pelo
  operador. Horario ruim so torna o robo MAIS exigente, jamais menos.
- O multiplicador de risco **nunca passa de 1.0**. O seletor so pode
  reduzir a exposicao definida na configuracao, nunca amplia-la.

Modulo puro: sem banco, sem MetaTrader, sem envio de ordem.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.market.regimes import MarketRegime, Trend, VolatilityLevel
from app.market.sessions import (
    SessionRating,
    SymbolSessionState,
    describe_sessions,
    is_weekend_protection_window,
)
from app.market.volume_profile import VolumeLevel, VolumeReading


class PlaybookKind(enum.StrEnum):
    TREND_PULLBACK = "TREND_PULLBACK"
    TREND_CROSSOVER = "TREND_CROSSOVER"
    BREAKOUT = "BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    MOMENTUM = "MOMENTUM"
    STAND_ASIDE = "STAND_ASIDE"


@dataclass(frozen=True, slots=True)
class PlaybookProfile:
    kind: PlaybookKind
    strategy_name: str | None
    label: str
    description: str
    icon: str


PLAYBOOK_PROFILES: dict[PlaybookKind, PlaybookProfile] = {
    PlaybookKind.TREND_PULLBACK: PlaybookProfile(
        kind=PlaybookKind.TREND_PULLBACK,
        strategy_name="trend_pullback",
        label="Tendencia com pullback",
        description=(
            "Entra a favor da tendencia dominante apos uma correcao — melhor "
            "relacao risco/retorno quando o par tem direcao definida."
        ),
        icon="bi-graph-up-arrow",
    ),
    PlaybookKind.TREND_CROSSOVER: PlaybookProfile(
        kind=PlaybookKind.TREND_CROSSOVER,
        strategy_name="ema_crossover",
        label="Tendencia por cruzamento",
        description=(
            "Acompanha o giro das medias quando a tendencia acabou de mudar de "
            "mao e ainda nao ofereceu pullback."
        ),
        icon="bi-arrow-left-right",
    ),
    PlaybookKind.BREAKOUT: PlaybookProfile(
        kind=PlaybookKind.BREAKOUT,
        strategy_name="range_breakout",
        label="Rompimento de faixa",
        description=(
            "Opera a quebra da faixa formada antes da abertura da sessao, "
            "quando o volume confirma a saida."
        ),
        icon="bi-box-arrow-up-right",
    ),
    PlaybookKind.MEAN_REVERSION: PlaybookProfile(
        kind=PlaybookKind.MEAN_REVERSION,
        strategy_name="zscore_mean_reversion",
        label="Retorno a media",
        description=(
            "Compra o exagero para baixo e vende o exagero para cima dentro de "
            "um mercado lateral com volatilidade comportada."
        ),
        icon="bi-arrow-repeat",
    ),
    PlaybookKind.MOMENTUM: PlaybookProfile(
        kind=PlaybookKind.MOMENTUM,
        strategy_name="momentum_continuation",
        label="Continuidade de momentum",
        description=(
            "Segue o fluxo forte enquanto ele persiste — reservado para "
            "tendencia clara com volume acima do normal."
        ),
        icon="bi-lightning-charge",
    ),
    PlaybookKind.STAND_ASIDE: PlaybookProfile(
        kind=PlaybookKind.STAND_ASIDE,
        strategy_name=None,
        label="Fora do mercado",
        description=(
            "Nenhum operacional tem vantagem nas condicoes atuais. O robo "
            "permanece observando, sem enviar ordem."
        ),
        icon="bi-pause-circle",
    ),
}

QUIET_THRESHOLD_BONUS = 5.0
ACTIVE_THRESHOLD_BONUS = 2.0
LOW_VOLUME_THRESHOLD_BONUS = 3.0
MAX_THRESHOLD = 100.0

PRIME_RISK_FACTOR = 1.0
ACTIVE_RISK_FACTOR = 0.75
QUIET_RISK_FACTOR = 0.5
LOW_VOLUME_RISK_FACTOR = 0.75
MIN_RISK_FACTOR = 0.25

SCALP_TIMEFRAME = "M5"
STANDARD_TIMEFRAME = "M15"
SLOW_TIMEFRAME = "M30"


@dataclass(frozen=True, slots=True)
class PlaybookDecision:
    """O operacional eleito e o porque, em linguagem de operador."""

    kind: PlaybookKind
    strategy_name: str | None
    timeframe: str
    analysis_threshold: float
    risk_factor: float
    tradeable: bool
    fit_score: float
    headline: str
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    session_rating: SessionRating
    volume_level: VolumeLevel
    trend: str
    volatility: str

    @property
    def profile(self) -> PlaybookProfile:
        return PLAYBOOK_PROFILES[self.kind]

    @property
    def label(self) -> str:
        return self.profile.label

    @property
    def description(self) -> str:
        return self.profile.description

    @property
    def icon(self) -> str:
        return self.profile.icon


def _stand_aside(
    *,
    blockers: list[str],
    reasons: list[str],
    timeframe: str,
    threshold: float,
    session: SymbolSessionState,
    volume: VolumeReading,
    regime: MarketRegime | None,
) -> PlaybookDecision:
    return PlaybookDecision(
        kind=PlaybookKind.STAND_ASIDE,
        strategy_name=None,
        timeframe=timeframe,
        analysis_threshold=threshold,
        risk_factor=0.0,
        tradeable=False,
        fit_score=0.0,
        headline=blockers[0] if blockers else "Sem vantagem operacional agora.",
        reasons=tuple(reasons),
        blockers=tuple(blockers),
        session_rating=session.rating,
        volume_level=volume.level,
        trend=regime.trend.value if regime is not None else "SEM_DADOS",
        volatility=regime.volatility.value if regime is not None else "SEM_DADOS",
    )


def _choose_timeframe(
    session: SymbolSessionState,
    volume: VolumeReading,
    available: tuple[str, ...],
) -> tuple[str, str]:
    """Escolhe o timeframe de execucao e explica a escolha.

    Mais fluxo permite barras menores (reacao mais rapida); pouco fluxo
    exige barras maiores para nao transformar ruido em sinal.
    """
    if session.rating == SessionRating.PRIME and volume.level == VolumeLevel.HIGH:
        preferred, why = (
            SCALP_TIMEFRAME,
            "Horario nobre com volume forte: barras de 5 minutos reagem sem virar ruido.",
        )
    elif volume.level == VolumeLevel.LOW or session.rating == SessionRating.QUIET:
        preferred, why = (
            SLOW_TIMEFRAME,
            "Fluxo abaixo do normal: barras de 30 minutos filtram oscilacao sem informacao.",
        )
    else:
        preferred, why = (
            STANDARD_TIMEFRAME,
            "Condicao intermediaria: barras de 15 minutos equilibram reacao e filtro.",
        )

    if preferred in available:
        return preferred, why

    fallback_order = (STANDARD_TIMEFRAME, SLOW_TIMEFRAME, SCALP_TIMEFRAME)
    for candidate in fallback_order:
        if candidate in available:
            return (
                candidate,
                f"{why} ({preferred} nao esta sincronizado; usando {candidate}.)",
            )
    chosen = available[0] if available else STANDARD_TIMEFRAME
    return (
        chosen,
        f"{why} (nenhum timeframe preferido disponivel; usando {chosen}.)",
    )


def _choose_kind(
    regime: MarketRegime,
    session: SymbolSessionState,
    volume: VolumeReading,
) -> tuple[PlaybookKind, str]:
    trending = regime.trend in (Trend.UP, Trend.DOWN)
    direction = "de alta" if regime.trend == Trend.UP else "de baixa"

    if session.opening_sessions and volume.level in (VolumeLevel.HIGH, VolumeLevel.NORMAL):
        return (
            PlaybookKind.BREAKOUT,
            f"Abertura de {describe_sessions(session.opening_sessions)} com volume "
            f"{volume.label.lower()}: a faixa formada antes da abertura tende a ser "
            "rompida com continuidade.",
        )

    if trending and volume.level == VolumeLevel.HIGH and regime.volatility != VolatilityLevel.LOW:
        return (
            PlaybookKind.MOMENTUM,
            f"Tendencia {direction} confirmada (ADX) com volume forte: o fluxo "
            "atual favorece seguir o movimento em vez de esperar correcao.",
        )

    if trending and regime.is_transition:
        return (
            PlaybookKind.TREND_CROSSOVER,
            f"Tendencia {direction} recem-estabelecida: ainda nao houve pullback "
            "para entrar, o giro das medias e o gatilho disponivel.",
        )

    if trending:
        return (
            PlaybookKind.TREND_PULLBACK,
            f"Tendencia {direction} com fluxo comportado: esperar a correcao "
            "oferece o melhor risco/retorno.",
        )

    if regime.volatility == VolatilityLevel.HIGH:
        return (
            PlaybookKind.BREAKOUT,
            "Mercado sem direcao definida, mas com volatilidade alta: a faixa "
            "atual tende a ser resolvida por rompimento.",
        )

    return (
        PlaybookKind.MEAN_REVERSION,
        "Mercado lateral com volatilidade comportada: os exageros contra a "
        "media sao o operacional com vantagem.",
    )


def _fit_score(
    session: SymbolSessionState,
    volume: VolumeReading,
    regime: MarketRegime,
) -> float:
    """Quao bem as condicoes atuais servem ao operacional eleito (0-100).

    Nao e probabilidade de acerto — e a qualidade do CONTEXTO. A
    probabilidade de entrada continua vindo do motor de analise
    (`app.services.analysis_service`), que tem poder de veto proprio.
    """
    score = 50.0
    score += {
        SessionRating.PRIME: 25.0,
        SessionRating.ACTIVE: 12.0,
        SessionRating.QUIET: -10.0,
        SessionRating.CLOSED: -50.0,
    }[session.rating]
    score += {
        VolumeLevel.HIGH: 15.0,
        VolumeLevel.NORMAL: 8.0,
        VolumeLevel.LOW: -8.0,
        VolumeLevel.DEAD: -30.0,
        VolumeLevel.EXTREME: -15.0,
        VolumeLevel.UNKNOWN: -20.0,
    }[volume.level]
    if session.is_overlap:
        score += 5.0
    if regime.spread_adequate:
        score += 5.0
    else:
        score -= 20.0
    if regime.liquidity_adequate:
        score += 5.0
    else:
        score -= 10.0
    if regime.is_extraordinary_event:
        score -= 30.0
    return round(min(100.0, max(0.0, score)), 1)


def select_playbook(
    *,
    session: SymbolSessionState,
    volume: VolumeReading,
    regime: MarketRegime | None,
    base_threshold: float,
    available_timeframes: tuple[str, ...] = (
        SCALP_TIMEFRAME,
        STANDARD_TIMEFRAME,
        SLOW_TIMEFRAME,
    ),
) -> PlaybookDecision:
    """Elege o operacional para as condicoes informadas.

    `base_threshold` e o score minimo configurado pelo operador; o retorno
    nunca fica abaixo dele.
    """
    reasons: list[str] = [*session.reasons, *volume.reasons]
    blockers: list[str] = []
    timeframe, timeframe_reason = _choose_timeframe(session, volume, available_timeframes)

    if not session.market_open:
        blockers.append("Mercado fechado: nenhuma ordem e enviada no fim de semana.")
    if is_weekend_protection_window(session):
        blockers.append(
            "Proximo do fechamento semanal: entrada bloqueada por risco de gap "
            "de fim de semana."
        )
    if volume.level == VolumeLevel.UNKNOWN:
        blockers.append(
            "Sem historico suficiente para medir o volume deste horario — o robo "
            "nao opera as cegas."
        )
    if volume.level == VolumeLevel.DEAD:
        blockers.append(
            "Volume praticamente nulo neste horario: spread e execucao ficam "
            "imprevisiveis."
        )
    if volume.level == VolumeLevel.EXTREME:
        blockers.append(
            f"Pico atipico de volume ({volume.ratio:.1f}x o normal desta hora): "
            "comportamento de evento, nao de fluxo — o robo aguarda normalizar."
        )
    if regime is None:
        blockers.append(
            "Regime de mercado ainda nao pode ser classificado (dados insuficientes)."
        )
    else:
        if regime.is_extraordinary_event:
            blockers.append(
                "Evento extraordinario detectado (amplitude muito acima do normal): "
                "nenhum operacional tem vantagem estatistica agora."
            )
        if not regime.spread_adequate:
            blockers.append(
                "Spread medio acima do aceitavel para este par: o custo de entrada "
                "consome o alvo."
            )
        if (
            session.rating == SessionRating.QUIET
            and volume.level != VolumeLevel.HIGH
        ):
            blockers.append(
                "Fora do horario nobre do par e sem volume que compense: o robo "
                "aguarda a sessao correta."
            )

    if blockers:
        return _stand_aside(
            blockers=blockers,
            reasons=[*reasons, timeframe_reason],
            timeframe=timeframe,
            threshold=min(MAX_THRESHOLD, base_threshold),
            session=session,
            volume=volume,
            regime=regime,
        )

    assert regime is not None  # garantido pelos bloqueios acima
    kind, kind_reason = _choose_kind(regime, session, volume)

    threshold = base_threshold
    if session.rating == SessionRating.QUIET:
        threshold += QUIET_THRESHOLD_BONUS
    elif session.rating == SessionRating.ACTIVE:
        threshold += ACTIVE_THRESHOLD_BONUS
    if volume.level == VolumeLevel.LOW:
        threshold += LOW_VOLUME_THRESHOLD_BONUS
    threshold = min(MAX_THRESHOLD, max(base_threshold, threshold))

    risk_factor = {
        SessionRating.PRIME: PRIME_RISK_FACTOR,
        SessionRating.ACTIVE: ACTIVE_RISK_FACTOR,
        SessionRating.QUIET: QUIET_RISK_FACTOR,
        SessionRating.CLOSED: QUIET_RISK_FACTOR,
    }[session.rating]
    if volume.level == VolumeLevel.LOW:
        risk_factor *= LOW_VOLUME_RISK_FACTOR
    risk_factor = round(min(1.0, max(MIN_RISK_FACTOR, risk_factor)), 4)

    profile = PLAYBOOK_PROFILES[kind]
    reasons.extend([timeframe_reason, kind_reason])
    if threshold > base_threshold:
        reasons.append(
            f"Score minimo elevado de {base_threshold:.0f} para {threshold:.0f} "
            "porque o contexto e menos favoravel — o robo so fica mais exigente."
        )
    if risk_factor < 1.0:
        reasons.append(
            f"Risco por operacao reduzido a {risk_factor * 100:.0f}% do configurado "
            "pelas condicoes do horario/volume."
        )

    return PlaybookDecision(
        kind=kind,
        strategy_name=profile.strategy_name,
        timeframe=timeframe,
        analysis_threshold=threshold,
        risk_factor=risk_factor,
        tradeable=True,
        fit_score=_fit_score(session, volume, regime),
        headline=f"{profile.label} em {timeframe}",
        reasons=tuple(reasons),
        blockers=(),
        session_rating=session.rating,
        volume_level=volume.level,
        trend=regime.trend.value,
        volatility=regime.volatility.value,
    )

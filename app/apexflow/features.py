"""Feature Vector: tudo o que o motor sabe, em uma estrutura estavel.

O vetor e a fronteira entre "ler o mercado" e "decidir". Todo motor de
decisao — o scorecard deterministico de hoje e qualquer modelo treinado de
amanha — consome exatamente esta estrutura, o que permite trocar o cerebro
sem tocar nos sensores.

Tres propriedades sao obrigatorias e testadas:

1. **Ordem estavel.** `FEATURE_NAMES` e a fonte unica da ordem; um modelo
   treinado hoje nunca pode receber as colunas embaralhadas amanha.
2. **Versionamento.** Qualquer mudanca em nomes/ordem exige subir
   `FEATURE_VERSION`. O journal grava a versao junto de cada decisao, entao
   um modelo antigo nunca e alimentado silenciosamente com um vetor novo.
3. **Ausencia declarada.** Feature indisponivel vale `None` no dicionario e
   um valor neutro explicito no vetor numerico, com a mascara
   `missing_mask` indicando quais foram preenchidas. Nunca se confunde
   "zero" com "nao sei".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from app.apexflow.context import MarketContext, MarketContextState
from app.apexflow.liquidity import LiquidityReading, LiquidityState
from app.apexflow.momentum import MomentumReading, MomentumState
from app.apexflow.mtf import MultiTimeframeView
from app.apexflow.spread import SpreadReading, SpreadVerdict
from app.apexflow.structures import ChartStructure, StructureKind, latest_by_kind
from app.apexflow.tick_flow import TickFlowMetrics
from app.apexflow.volatility import VolatilityReading, VolatilityState
from app.market.price_action import CandlestickPattern, PatternDirection, PatternName
from app.market.regimes import Trend
from app.market.sessions import SessionRating, SymbolSessionState
from app.market.volume_profile import VolumeLevel, VolumeReading

FEATURE_VERSION = "apexflow-2"
"""Suba a versao a cada mudanca em `FEATURE_NAMES` (nomes, ordem ou
semantica). Modelos treinados guardam a versao com que foram treinados.

Historico:
- `apexflow-1` — versao inicial.
- `apexflow-2` — acrescenta VWAP (`indicator_vwap_20`, `indicator_dist_vwap_20`)
  e as nove estruturas de grafico (`structure_*`), completando o Price Action
  Engine. Um modelo treinado em `apexflow-1` NAO pode receber um vetor
  `apexflow-2` sem retreino: as colunas novas mudam o tamanho do vetor."""

NEUTRAL_FILL = 0.0
"""Valor usado no vetor numerico quando a feature nao existe. A mascara
`missing_mask` e o que distingue esse 0.0 de um 0.0 verdadeiro."""

_PATTERN_FEATURES: tuple[PatternName, ...] = (
    PatternName.PIN_BAR,
    PatternName.HAMMER,
    PatternName.SHOOTING_STAR,
    PatternName.BULLISH_ENGULFING,
    PatternName.BEARISH_ENGULFING,
    PatternName.INSIDE_BAR,
    PatternName.OUTSIDE_BAR,
    PatternName.DOJI,
    PatternName.TWEEZER_TOP,
    PatternName.TWEEZER_BOTTOM,
    PatternName.MORNING_STAR,
    PatternName.EVENING_STAR,
    PatternName.FAKEY,
    PatternName.FALSE_BREAKOUT,
)

_INDICATOR_FEATURES: tuple[str, ...] = (
    "rsi_14",
    "adx_14",
    "plus_di_14",
    "minus_di_14",
    "macd_histogram",
    "ema_21_slope",
    "dist_ema_21",
    "dist_ema_50",
    "dist_ema_200",
    "zscore_20",
    "roc_10",
    "relative_volume_20",
    "vwap_20",
    "dist_vwap_20",
    "avg_spread_20",
    "spread_variation_20",
    "realized_volatility_20",
    "bollinger_upper",
    "bollinger_lower",
)


def _feature_names() -> tuple[str, ...]:
    names: list[str] = [
        # Fluxo de ticks
        "flow_tick_count",
        "flow_window_seconds",
        "flow_ticks_per_second",
        "flow_acceleration",
        "flow_mean_interval_ms",
        "flow_max_interval_ms",
        "flow_uptick_ratio",
        "flow_direction_bias",
        "flow_price_velocity",
        "flow_price_path",
        "flow_efficiency",
        "flow_latency_seconds",
        # Spread
        "spread_points",
        "spread_mean_points",
        "spread_max_points",
        "spread_trend",
        "spread_to_target",
        "spread_ok",
        # Volatilidade
        "volatility_atr_points",
        "volatility_atr_ratio",
        "volatility_true_range",
        "volatility_realized",
        "volatility_seconds",
        "volatility_sufficient",
        # Momentum
        "momentum_strength_atr",
        "momentum_impulse",
        "momentum_direction",
        "momentum_persistence",
        "momentum_speed_change",
        "momentum_direction_change",
        "momentum_favours_continuation",
        # Liquidez / SMC
        "liquidity_recent_sweeps",
        "liquidity_unmitigated_blocks",
        "liquidity_open_gaps",
        "liquidity_structure_events",
        "liquidity_institutional_zones",
        "liquidity_blocks_entry",
        "liquidity_direction",
        # Multi-timeframe
        "mtf_alignment",
        "mtf_coverage",
        "mtf_macro_trend",
        "mtf_dominant_direction",
        # Sessao e horario
        "session_rating",
        "session_is_overlap",
        "session_is_opening",
        "session_hour_utc",
        "session_weekday",
        "session_minutes_to_close",
        # Volume
        "volume_level",
        "volume_ratio",
        # Contexto
        "context_state",
        "context_is_tradeable",
        "context_confidence",
        "context_trend",
    ]
    names.extend(f"pattern_{pattern.value.lower()}" for pattern in _PATTERN_FEATURES)
    names.append("pattern_dominant_direction")
    names.extend(f"structure_{kind.value.lower()}" for kind in StructureKind)
    names.extend(f"indicator_{name}" for name in _INDICATOR_FEATURES)
    return tuple(names)


FEATURE_NAMES: tuple[str, ...] = _feature_names()

_CONTEXT_CODES: dict[MarketContextState, float] = {
    state: float(index) for index, state in enumerate(MarketContextState)
}
_SESSION_CODES: dict[SessionRating, float] = {
    SessionRating.CLOSED: 0.0,
    SessionRating.QUIET: 1.0,
    SessionRating.ACTIVE: 2.0,
    SessionRating.PRIME: 3.0,
}
_VOLUME_CODES: dict[VolumeLevel, float] = {
    VolumeLevel.UNKNOWN: 0.0,
    VolumeLevel.DEAD: 1.0,
    VolumeLevel.LOW: 2.0,
    VolumeLevel.NORMAL: 3.0,
    VolumeLevel.HIGH: 4.0,
    VolumeLevel.EXTREME: 5.0,
}


def _trend_code(trend: Trend | None) -> float | None:
    if trend is None:
        return None
    return {Trend.DOWN: -1.0, Trend.SIDEWAYS: 0.0, Trend.UP: 1.0}[trend]


def _direction_code(direction: PatternDirection | None) -> float | None:
    if direction is None:
        return None
    return 1.0 if direction == PatternDirection.BULLISH else -1.0


def _indicator_value(features: pd.DataFrame, column: str) -> float | None:
    if features.empty or column not in features.columns:
        return None
    value = features[column].iloc[-1]
    return None if pd.isna(value) else float(value)


@dataclass(frozen=True, slots=True)
class FeatureVector:
    """Vetor nomeado e versionado, pronto para modelo ou para auditoria."""

    version: str
    generated_at: datetime
    symbol: str
    timeframe: str
    values: dict[str, float | None] = field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return FEATURE_NAMES

    def as_list(self, *, fill: float = NEUTRAL_FILL) -> list[float]:
        """Vetor numerico na ordem canonica, com ausencias preenchidas."""
        return [
            fill if self.values.get(name) is None else float(self.values[name])  # type: ignore[arg-type]
            for name in FEATURE_NAMES
        ]

    def missing_mask(self) -> list[bool]:
        """`True` onde o valor foi preenchido por ausencia — a unica forma
        de o consumidor distinguir "zero" de "nao sei"."""
        return [self.values.get(name) is None for name in FEATURE_NAMES]

    @property
    def missing_count(self) -> int:
        return sum(self.missing_mask())

    @property
    def completeness(self) -> float:
        return 1.0 - (self.missing_count / len(FEATURE_NAMES))

    def as_dict(self) -> dict[str, float | None]:
        return {name: self.values.get(name) for name in FEATURE_NAMES}


def build_feature_vector(
    *,
    symbol: str,
    timeframe: str,
    features: pd.DataFrame,
    flow: TickFlowMetrics,
    spread: SpreadReading,
    volatility: VolatilityReading,
    momentum: MomentumReading,
    liquidity: LiquidityReading,
    mtf: MultiTimeframeView,
    session: SymbolSessionState,
    volume: VolumeReading,
    context: MarketContext,
    patterns: list[CandlestickPattern] | None = None,
    structures: list[ChartStructure] | None = None,
    now: datetime | None = None,
) -> FeatureVector:
    """Monta o vetor a partir das leituras de todos os motores.

    `structures` ausente vale zero em todas as colunas `structure_*` — o
    mesmo tratamento de um padrao nao detectado, porque "nao encontrei esta
    estrutura" e uma informacao valida, nao uma lacuna."""
    resolved_now = now or datetime.now(UTC)
    detected = patterns or []
    latest_by_name = {pattern.name: pattern for pattern in detected}
    dominant = max(detected, key=lambda pattern: pattern.index) if detected else None

    values: dict[str, float | None] = {
        "flow_tick_count": float(flow.tick_count),
        "flow_window_seconds": flow.window_seconds,
        "flow_ticks_per_second": flow.ticks_per_second,
        "flow_acceleration": flow.tick_acceleration,
        "flow_mean_interval_ms": flow.mean_interval_ms,
        "flow_max_interval_ms": flow.max_interval_ms,
        "flow_uptick_ratio": flow.uptick_ratio,
        "flow_direction_bias": float(flow.direction_bias),
        "flow_price_velocity": flow.price_velocity_points,
        "flow_price_path": flow.price_path_points,
        "flow_efficiency": flow.efficiency,
        "flow_latency_seconds": flow.latency_seconds,
        "spread_points": spread.spread_points,
        "spread_mean_points": spread.mean_points,
        "spread_max_points": spread.max_points,
        "spread_trend": spread.trend,
        "spread_to_target": spread.spread_to_target,
        "spread_ok": 1.0 if spread.verdict == SpreadVerdict.OK else 0.0,
        "volatility_atr_points": volatility.atr_points,
        "volatility_atr_ratio": volatility.atr_ratio,
        "volatility_true_range": volatility.true_range_points,
        "volatility_realized": volatility.realized_volatility,
        "volatility_seconds": volatility.second_volatility_points,
        "volatility_sufficient": (
            0.0 if volatility.state == VolatilityState.INSUFFICIENT else 1.0
        ),
        "momentum_strength_atr": momentum.strength_atr,
        "momentum_impulse": momentum.impulse_points,
        "momentum_direction": float(momentum.direction),
        "momentum_persistence": momentum.persistence,
        "momentum_speed_change": momentum.speed_change,
        "momentum_direction_change": 1.0 if momentum.direction_change else 0.0,
        "momentum_favours_continuation": 1.0 if momentum.favours_continuation else 0.0,
        "liquidity_recent_sweeps": float(len(liquidity.recent_sweeps)),
        "liquidity_unmitigated_blocks": float(len(liquidity.unmitigated_order_blocks)),
        "liquidity_open_gaps": float(len(liquidity.open_fair_value_gaps)),
        "liquidity_structure_events": float(len(liquidity.structure_events)),
        "liquidity_institutional_zones": float(liquidity.institutional_zones),
        "liquidity_blocks_entry": 1.0 if liquidity.blocks_entry else 0.0,
        "liquidity_direction": _direction_code(liquidity.direction),
        "mtf_alignment": mtf.alignment_score,
        "mtf_coverage": mtf.coverage,
        "mtf_macro_trend": _trend_code(mtf.macro_trend),
        "mtf_dominant_direction": _trend_code(mtf.dominant_direction),
        "session_rating": _SESSION_CODES[session.rating],
        "session_is_overlap": 1.0 if session.is_overlap else 0.0,
        "session_is_opening": 1.0 if session.opening_sessions else 0.0,
        "session_hour_utc": float(session.now_utc.hour),
        "session_weekday": float(session.now_utc.weekday()),
        "session_minutes_to_close": session.minutes_to_week_close,
        "volume_level": _VOLUME_CODES[volume.level],
        "volume_ratio": volume.ratio,
        "context_state": _CONTEXT_CODES[context.state],
        "context_is_tradeable": 1.0 if context.is_tradeable else 0.0,
        "context_confidence": context.confidence,
        "context_trend": _trend_code(context.trend),
    }

    for pattern_name in _PATTERN_FEATURES:
        pattern = latest_by_name.get(pattern_name)
        values[f"pattern_{pattern_name.value.lower()}"] = (
            _direction_code(pattern.direction) if pattern is not None else 0.0
        )
    values["pattern_dominant_direction"] = (
        _direction_code(dominant.direction) if dominant is not None else 0.0
    )

    detected_structures = latest_by_kind(structures or [])
    for kind in StructureKind:
        structure = detected_structures.get(kind)
        if structure is None:
            values[f"structure_{kind.value.lower()}"] = 0.0
        elif structure.direction is None:
            # Estrutura neutra (compressao/expansao/range): presenca vale 1,
            # porque ela diz "como", nao "para onde".
            values[f"structure_{kind.value.lower()}"] = 1.0
        else:
            values[f"structure_{kind.value.lower()}"] = _direction_code(
                structure.direction
            )

    for column in _INDICATOR_FEATURES:
        values[f"indicator_{column}"] = _indicator_value(features, column)

    unknown = set(values) - set(FEATURE_NAMES)
    if unknown:  # pragma: no cover - erro de programacao, nunca de dados
        raise ValueError(f"Features fora do catalogo: {sorted(unknown)}")

    return FeatureVector(
        version=FEATURE_VERSION,
        generated_at=resolved_now,
        symbol=symbol,
        timeframe=timeframe,
        values=values,
    )


def context_state_from_code(code: float) -> MarketContextState:
    """Inverso de `_CONTEXT_CODES` — usado ao reler decisoes do journal."""
    for state, value in _CONTEXT_CODES.items():
        if value == code:
            return state
    return MarketContextState.UNKNOWN


__all__ = [
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "FeatureVector",
    "LiquidityState",
    "MomentumState",
    "build_feature_vector",
    "context_state_from_code",
]

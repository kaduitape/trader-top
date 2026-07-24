"""Reconhecimento de padroes de candle — Price Action (Fase 18.2).

Cada detector so olha para tras: um padrao "concluido" no indice `i` usa no
maximo `candles[i-2:i+1]` — nunca `candles[i+1]` em diante. Isso segue a
mesma disciplina de leak-safety de `app.market.indicators`/`features.py`:
o chamador e responsavel por garantir que toda barra passada aqui ja esta
fechada (mesmo contrato de `CandleFeatureLike` em toda a base).

Fakey, False Breakout, Spring e Upthrust NAO estao aqui: dependem de um
nivel de suporte/resistencia para fazer sentido (uma "falha de rompimento"
so existe em relacao a um nivel) — ficam em `app.market.smc` (Fase 18.4),
que ja tem os niveis calculados por `app.market.structure` (Fase 18.3).
Colocar esses quatro padroes aqui criaria uma dependencia invertida
(price_action.py -> structure.py), o que quebraria a ordem de estagios.

Prioridade quando mais de um padrao poderia casar no mesmo candle (varios
sao geometricamente parecidos): padroes de 3 candles > 2 candles > 1
candle; dentro dos padroes de 1 candle, Hammer/Shooting Star (mais
especificos: exigem o corpo perto de uma ponta do range) tem prioridade
sobre Pin Bar (mais generico: mesma geometria de pavio dominante, sem essa
exigencia de posicao do corpo) — um candle que ja satisfaz Hammer nunca e
tambem reportado como Pin Bar."""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.market.features import CandleFeatureLike


class PatternDirection(enum.StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class PatternName(enum.StrEnum):
    PIN_BAR = "PIN_BAR"
    HAMMER = "HAMMER"
    SHOOTING_STAR = "SHOOTING_STAR"
    BULLISH_ENGULFING = "BULLISH_ENGULFING"
    BEARISH_ENGULFING = "BEARISH_ENGULFING"
    INSIDE_BAR = "INSIDE_BAR"
    OUTSIDE_BAR = "OUTSIDE_BAR"
    BULLISH_HARAMI = "BULLISH_HARAMI"
    BEARISH_HARAMI = "BEARISH_HARAMI"
    DOJI = "DOJI"
    MARUBOZU_BULLISH = "MARUBOZU_BULLISH"
    MARUBOZU_BEARISH = "MARUBOZU_BEARISH"
    TWEEZER_TOP = "TWEEZER_TOP"
    TWEEZER_BOTTOM = "TWEEZER_BOTTOM"
    MORNING_STAR = "MORNING_STAR"
    EVENING_STAR = "EVENING_STAR"
    THREE_WHITE_SOLDIERS = "THREE_WHITE_SOLDIERS"
    THREE_BLACK_CROWS = "THREE_BLACK_CROWS"
    FAKEY = "FAKEY"
    FALSE_BREAKOUT = "FALSE_BREAKOUT"


@dataclass(frozen=True, slots=True)
class PatternDefinition:
    name: PatternName
    bars_required: int
    criteria: str
    leakage_risk: str


PATTERN_CATALOG: list[PatternDefinition] = [
    PatternDefinition(
        PatternName.HAMMER,
        1,
        "Corpo pequeno (<=30% do range) proximo do topo do range; pavio "
        "inferior >= 2x o corpo; pavio superior <= o corpo.",
        "Nenhum — usa apenas OHLC do proprio candle ja fechado.",
    ),
    PatternDefinition(
        PatternName.SHOOTING_STAR,
        1,
        "Corpo pequeno proximo da base do range; pavio superior >= 2x o "
        "corpo; pavio inferior <= o corpo.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.PIN_BAR,
        1,
        "Mesma geometria de pavio dominante de Hammer/Shooting Star, sem a "
        "exigencia de posicao do corpo — reportado so quando Hammer/"
        "Shooting Star nao casam (prioridade mais especifica primeiro).",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.DOJI,
        1,
        "Corpo quase nulo (<=10% do range) com range > 0.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.MARUBOZU_BULLISH,
        1,
        "Corpo >= 90% do range, fechamento acima da abertura, pavios " "praticamente inexistentes.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.MARUBOZU_BEARISH,
        1,
        "Espelho do Marubozu de alta, fechamento abaixo da abertura.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.BULLISH_ENGULFING,
        2,
        "Candle anterior de baixa; candle atual de alta cujo corpo cobre "
        "totalmente o corpo anterior.",
        "Nenhum — usa candles[i-1] e candles[i], nunca candles[i+1].",
    ),
    PatternDefinition(
        PatternName.BEARISH_ENGULFING,
        2,
        "Espelho do Engolfo de alta.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.BULLISH_HARAMI,
        2,
        "Candle anterior de baixa com corpo grande; candle atual de alta "
        "com corpo pequeno, totalmente contido no corpo anterior.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.BEARISH_HARAMI,
        2,
        "Espelho do Harami de alta.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.INSIDE_BAR,
        2,
        "Range do candle atual (high/low) totalmente contido no range do " "candle anterior.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.OUTSIDE_BAR,
        2,
        "Range do candle atual cobre totalmente o range do candle "
        "anterior (high maior e low menor).",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.TWEEZER_TOP,
        2,
        "Duas maximas praticamente iguais (tolerancia relativa ao range); "
        "segundo candle de baixa.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.TWEEZER_BOTTOM,
        2,
        "Duas minimas praticamente iguais; segundo candle de alta.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.MORNING_STAR,
        3,
        "Candle de baixa com corpo grande, candle 'estrela' de corpo "
        "pequeno, candle de alta com corpo grande fechando bem dentro do "
        "corpo do primeiro candle.",
        "Nenhum — usa candles[i-2:i+1], nunca dados futuros.",
    ),
    PatternDefinition(
        PatternName.EVENING_STAR,
        3,
        "Espelho da Estrela da Manha.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.THREE_WHITE_SOLDIERS,
        3,
        "Tres candles de alta consecutivos, cada um fechando acima do "
        "anterior e abrindo dentro do corpo anterior, pavios superiores "
        "pequenos.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.THREE_BLACK_CROWS,
        3,
        "Espelho das Tres Solidados Brancos.",
        "Nenhum.",
    ),
    PatternDefinition(
        PatternName.FAKEY,
        3,
        "Inside bar seguido de rompimento falso de sua maxima/minima, com "
        "fechamento de volta para dentro do range do inside bar. Deteccao "
        "em `app.market.smc.detect_fakey` (Fase 18.4) — depende do inside "
        "bar ja detectado por `app.market.price_action.detect_patterns`.",
        "Nenhum — usa apenas candles ja fechados.",
    ),
    PatternDefinition(
        PatternName.FALSE_BREAKOUT,
        2,
        "Rompimento de um nivel de suporte/resistencia (pavio alem do "
        "nivel) com fechamento de volta para o lado original. Deteccao em "
        "`app.market.smc.detect_false_breakout` (Fase 18.4) — depende de "
        "niveis de S/R ja calculados por `app.market.structure`.",
        "Nenhum.",
    ),
]


@dataclass(frozen=True, slots=True)
class CandlestickPattern:
    name: PatternName
    direction: PatternDirection
    index: int
    open_time: datetime
    strength: float
    description: str


@dataclass(frozen=True, slots=True)
class _Metrics:
    index: int
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    range_: float
    body: float
    upper_wick: float
    lower_wick: float
    is_bullish: bool
    is_bearish: bool


def _metrics(index: int, candle: CandleFeatureLike) -> _Metrics:
    open_ = float(candle.open)
    high = float(candle.high)
    low = float(candle.low)
    close = float(candle.close)
    return _Metrics(
        index=index,
        open_time=candle.open_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        range_=high - low,
        body=abs(close - open_),
        upper_wick=high - max(open_, close),
        lower_wick=min(open_, close) - low,
        is_bullish=close > open_,
        is_bearish=close < open_,
    )


def _hammer_or_shooting_star(m: _Metrics) -> CandlestickPattern | None:
    if m.range_ <= 0:
        return None
    body_small = m.body <= 0.3 * m.range_
    if not body_small:
        return None

    # Corpo proximo do topo do range (Hammer) ou da base (Shooting Star).
    body_top = max(m.open, m.close)
    body_bottom = min(m.open, m.close)
    near_top = (m.high - body_top) <= 0.1 * m.range_
    near_bottom = (body_bottom - m.low) <= 0.1 * m.range_

    if near_top and m.lower_wick >= 2 * m.body and m.upper_wick <= m.body:
        strength = min(1.0, m.lower_wick / m.range_) if m.range_ > 0 else 0.5
        return CandlestickPattern(
            name=PatternName.HAMMER,
            direction=PatternDirection.BULLISH,
            index=m.index,
            open_time=m.open_time,
            strength=strength,
            description="Martelo: corpo pequeno no topo do range, pavio inferior longo.",
        )
    if near_bottom and m.upper_wick >= 2 * m.body and m.lower_wick <= m.body:
        strength = min(1.0, m.upper_wick / m.range_) if m.range_ > 0 else 0.5
        return CandlestickPattern(
            name=PatternName.SHOOTING_STAR,
            direction=PatternDirection.BEARISH,
            index=m.index,
            open_time=m.open_time,
            strength=strength,
            description="Estrela cadente: corpo pequeno na base do range, pavio superior longo.",
        )
    return None


def _pin_bar(m: _Metrics) -> CandlestickPattern | None:
    if m.range_ <= 0:
        return None
    body_small = m.body <= 0.3 * m.range_
    if not body_small:
        return None

    if m.lower_wick >= 2 * m.body and m.upper_wick <= m.body:
        return CandlestickPattern(
            name=PatternName.PIN_BAR,
            direction=PatternDirection.BULLISH,
            index=m.index,
            open_time=m.open_time,
            strength=min(1.0, m.lower_wick / m.range_),
            description="Pin bar de alta: rejeicao de precos mais baixos (pavio inferior dominante).",
        )
    if m.upper_wick >= 2 * m.body and m.lower_wick <= m.body:
        return CandlestickPattern(
            name=PatternName.PIN_BAR,
            direction=PatternDirection.BEARISH,
            index=m.index,
            open_time=m.open_time,
            strength=min(1.0, m.upper_wick / m.range_),
            description="Pin bar de baixa: rejeicao de precos mais altos (pavio superior dominante).",
        )
    return None


def _doji(m: _Metrics) -> CandlestickPattern | None:
    if m.range_ <= 0:
        return None
    if m.body <= 0.1 * m.range_:
        return CandlestickPattern(
            name=PatternName.DOJI,
            direction=PatternDirection.NEUTRAL,
            index=m.index,
            open_time=m.open_time,
            strength=1.0 - (m.body / m.range_ if m.range_ > 0 else 0.0),
            description="Doji: abertura e fechamento praticamente iguais (indecisao).",
        )
    return None


def _marubozu(m: _Metrics) -> CandlestickPattern | None:
    if m.range_ <= 0:
        return None
    if m.body < 0.9 * m.range_:
        return None
    if m.is_bullish:
        return CandlestickPattern(
            name=PatternName.MARUBOZU_BULLISH,
            direction=PatternDirection.BULLISH,
            index=m.index,
            open_time=m.open_time,
            strength=m.body / m.range_,
            description="Marubozu de alta: corpo ocupa quase todo o range, sem pavios relevantes.",
        )
    if m.is_bearish:
        return CandlestickPattern(
            name=PatternName.MARUBOZU_BEARISH,
            direction=PatternDirection.BEARISH,
            index=m.index,
            open_time=m.open_time,
            strength=m.body / m.range_,
            description="Marubozu de baixa: corpo ocupa quase todo o range, sem pavios relevantes.",
        )
    return None


def _single_candle_pattern(m: _Metrics) -> CandlestickPattern | None:
    return _hammer_or_shooting_star(m) or _pin_bar(m) or _marubozu(m) or _doji(m)


def _engulfing(prev: _Metrics, cur: _Metrics) -> CandlestickPattern | None:
    if prev.is_bearish and cur.is_bullish and cur.open <= prev.close and cur.close >= prev.open:
        return CandlestickPattern(
            name=PatternName.BULLISH_ENGULFING,
            direction=PatternDirection.BULLISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=min(1.0, cur.body / prev.body) if prev.body > 0 else 1.0,
            description="Engolfo de alta: corpo atual cobre totalmente o corpo anterior de baixa.",
        )
    if prev.is_bullish and cur.is_bearish and cur.open >= prev.close and cur.close <= prev.open:
        return CandlestickPattern(
            name=PatternName.BEARISH_ENGULFING,
            direction=PatternDirection.BEARISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=min(1.0, cur.body / prev.body) if prev.body > 0 else 1.0,
            description="Engolfo de baixa: corpo atual cobre totalmente o corpo anterior de alta.",
        )
    return None


def _harami(prev: _Metrics, cur: _Metrics) -> CandlestickPattern | None:
    if prev.body <= 0 or cur.body >= prev.body:
        return None
    prev_top, prev_bottom = max(prev.open, prev.close), min(prev.open, prev.close)
    cur_top, cur_bottom = max(cur.open, cur.close), min(cur.open, cur.close)
    if not (cur_top <= prev_top and cur_bottom >= prev_bottom):
        return None
    if prev.is_bearish and cur.is_bullish:
        return CandlestickPattern(
            name=PatternName.BULLISH_HARAMI,
            direction=PatternDirection.BULLISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=1.0 - (cur.body / prev.body),
            description="Harami de alta: corpo pequeno de alta contido no corpo anterior de baixa.",
        )
    if prev.is_bullish and cur.is_bearish:
        return CandlestickPattern(
            name=PatternName.BEARISH_HARAMI,
            direction=PatternDirection.BEARISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=1.0 - (cur.body / prev.body),
            description="Harami de baixa: corpo pequeno de baixa contido no corpo anterior de alta.",
        )
    return None


def _inside_outside_bar(prev: _Metrics, cur: _Metrics) -> CandlestickPattern | None:
    if cur.high <= prev.high and cur.low >= prev.low:
        return CandlestickPattern(
            name=PatternName.INSIDE_BAR,
            direction=PatternDirection.NEUTRAL,
            index=cur.index,
            open_time=cur.open_time,
            strength=1.0 - (cur.range_ / prev.range_) if prev.range_ > 0 else 0.5,
            description="Inside bar: range atual totalmente contido no range anterior (consolidacao).",
        )
    if cur.high >= prev.high and cur.low <= prev.low:
        direction = PatternDirection.BULLISH if cur.is_bullish else PatternDirection.BEARISH
        return CandlestickPattern(
            name=PatternName.OUTSIDE_BAR,
            direction=direction,
            index=cur.index,
            open_time=cur.open_time,
            strength=min(1.0, cur.range_ / prev.range_ - 1.0) if prev.range_ > 0 else 0.5,
            description="Outside bar: range atual cobre totalmente o range anterior.",
        )
    return None


def _tweezer(
    prev: _Metrics, cur: _Metrics, *, tolerance_pct: float = 0.1
) -> CandlestickPattern | None:
    avg_range = (prev.range_ + cur.range_) / 2
    if avg_range <= 0:
        return None
    tolerance = tolerance_pct * avg_range
    if abs(cur.high - prev.high) <= tolerance and cur.is_bearish and prev.is_bullish:
        return CandlestickPattern(
            name=PatternName.TWEEZER_TOP,
            direction=PatternDirection.BEARISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=1.0 - (abs(cur.high - prev.high) / tolerance if tolerance > 0 else 0.0),
            description="Tweezer top: duas maximas praticamente iguais seguidas de reversao de baixa.",
        )
    if abs(cur.low - prev.low) <= tolerance and cur.is_bullish and prev.is_bearish:
        return CandlestickPattern(
            name=PatternName.TWEEZER_BOTTOM,
            direction=PatternDirection.BULLISH,
            index=cur.index,
            open_time=cur.open_time,
            strength=1.0 - (abs(cur.low - prev.low) / tolerance if tolerance > 0 else 0.0),
            description="Tweezer bottom: duas minimas praticamente iguais seguidas de reversao de alta.",
        )
    return None


def _two_candle_pattern(prev: _Metrics, cur: _Metrics) -> CandlestickPattern | None:
    return (
        _engulfing(prev, cur)
        or _harami(prev, cur)
        or _inside_outside_bar(prev, cur)
        or _tweezer(prev, cur)
    )


def _morning_evening_star(
    first: _Metrics, star: _Metrics, third: _Metrics
) -> CandlestickPattern | None:
    if first.body <= 0 or third.body <= 0:
        return None
    star_is_small = star.range_ <= 0 or star.body <= 0.3 * max(first.body, third.body, 1e-12)
    if not star_is_small:
        return None

    first_mid = (max(first.open, first.close) + min(first.open, first.close)) / 2

    if first.is_bearish and third.is_bullish and third.close >= first_mid:
        return CandlestickPattern(
            name=PatternName.MORNING_STAR,
            direction=PatternDirection.BULLISH,
            index=third.index,
            open_time=third.open_time,
            strength=min(1.0, (third.close - first_mid) / first.body) if first.body > 0 else 0.5,
            description="Estrela da manha: baixa forte, indecisao, alta forte recuperando o corpo anterior.",
        )
    if first.is_bullish and third.is_bearish and third.close <= first_mid:
        return CandlestickPattern(
            name=PatternName.EVENING_STAR,
            direction=PatternDirection.BEARISH,
            index=third.index,
            open_time=third.open_time,
            strength=min(1.0, (first_mid - third.close) / first.body) if first.body > 0 else 0.5,
            description="Estrela da noite: alta forte, indecisao, baixa forte perdendo o corpo anterior.",
        )
    return None


def _three_soldiers_or_crows(a: _Metrics, b: _Metrics, c: _Metrics) -> CandlestickPattern | None:
    candles = (a, b, c)
    if all(m.is_bullish for m in candles):
        closes_rising = a.close < b.close < c.close
        opens_within_prev_body = min(a.open, a.close) <= b.open <= max(a.open, a.close) and min(
            b.open, b.close
        ) <= c.open <= max(b.open, b.close)
        small_upper_wicks = all(m.body > 0 and m.upper_wick <= 0.3 * m.body for m in candles)
        if closes_rising and opens_within_prev_body and small_upper_wicks:
            return CandlestickPattern(
                name=PatternName.THREE_WHITE_SOLDIERS,
                direction=PatternDirection.BULLISH,
                index=c.index,
                open_time=c.open_time,
                strength=1.0,
                description="Tres soldados brancos: tres candles de alta consecutivos, fechamentos crescentes.",
            )
    if all(m.is_bearish for m in candles):
        closes_falling = a.close > b.close > c.close
        opens_within_prev_body = min(a.open, a.close) <= b.open <= max(a.open, a.close) and min(
            b.open, b.close
        ) <= c.open <= max(b.open, b.close)
        small_lower_wicks = all(m.body > 0 and m.lower_wick <= 0.3 * m.body for m in candles)
        if closes_falling and opens_within_prev_body and small_lower_wicks:
            return CandlestickPattern(
                name=PatternName.THREE_BLACK_CROWS,
                direction=PatternDirection.BEARISH,
                index=c.index,
                open_time=c.open_time,
                strength=1.0,
                description="Tres corvos negros: tres candles de baixa consecutivos, fechamentos decrescentes.",
            )
    return None


def _three_candle_pattern(a: _Metrics, b: _Metrics, c: _Metrics) -> CandlestickPattern | None:
    return _morning_evening_star(a, b, c) or _three_soldiers_or_crows(a, b, c)


def detect_patterns(candles: Sequence[CandleFeatureLike]) -> list[CandlestickPattern]:
    """Detecta padroes de candle em toda a serie, um no maximo por indice
    (prioridade: 3 candles > 2 candles > 1 candle — ver docstring do
    modulo). Nunca levanta excecao para sequencias vazias/curtas."""
    metrics = [_metrics(i, c) for i, c in enumerate(candles)]
    results: list[CandlestickPattern] = []
    for i in range(len(metrics)):
        pattern: CandlestickPattern | None = None
        if i >= 2:
            pattern = _three_candle_pattern(metrics[i - 2], metrics[i - 1], metrics[i])
        if pattern is None and i >= 1:
            pattern = _two_candle_pattern(metrics[i - 1], metrics[i])
        if pattern is None:
            pattern = _single_candle_pattern(metrics[i])
        if pattern is not None:
            results.append(pattern)
    return results


def detect_latest_pattern(candles: Sequence[CandleFeatureLike]) -> CandlestickPattern | None:
    """Padrao detectado na ultima barra da serie, ou `None` (sequencia
    vazia ou nenhum padrao reconhecido — resultado normal, nao um erro)."""
    if not candles:
        return None
    patterns = detect_patterns(candles)
    if not patterns:
        return None
    last_index = len(candles) - 1
    for pattern in reversed(patterns):
        if pattern.index == last_index:
            return pattern
    return None

from datetime import UTC, datetime, timedelta

from app.market.price_action import (
    CandlestickPattern,
    PatternDirection,
    PatternName,
    detect_latest_pattern,
    detect_patterns,
)
from app.mt5.market_data import RawCandle

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


def _candle(i: int, o: float, h: float, low: float, c: float) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=i),
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=100,
        spread=2,
        real_volume=0,
    )


def _names(patterns: list[CandlestickPattern]) -> list[PatternName]:
    return [p.name for p in patterns]


# --- Padroes de 1 candle -----------------------------------------------


def test_detects_hammer() -> None:
    candles = [_candle(0, 10.0, 10.05, 9.0, 10.03)]
    patterns = detect_patterns(candles)
    assert len(patterns) == 1
    assert patterns[0].name == PatternName.HAMMER
    assert patterns[0].direction == PatternDirection.BULLISH


def test_detects_shooting_star() -> None:
    candles = [_candle(0, 10.0, 11.0, 9.95, 9.97)]
    patterns = detect_patterns(candles)
    assert len(patterns) == 1
    assert patterns[0].name == PatternName.SHOOTING_STAR
    assert patterns[0].direction == PatternDirection.BEARISH


def test_pin_bar_reported_when_body_not_at_extreme() -> None:
    # Pavio inferior dominante (0.65 do range de 1.0, >= 2x o corpo), mas
    # pavio superior (0.15) fica ACIMA da tolerancia do Hammer (<=10% do
    # range = 0.1) embora ainda <= o corpo (0.2) -- nao satisfaz o criterio
    # extra do Hammer (corpo bem colado no topo), entao cai para Pin Bar
    # generico.
    candles = [_candle(0, 9.65, 10.0, 9.0, 9.85)]
    patterns = detect_patterns(candles)
    assert len(patterns) == 1
    assert patterns[0].name == PatternName.PIN_BAR
    assert patterns[0].direction == PatternDirection.BULLISH


def test_hammer_takes_priority_over_pin_bar() -> None:
    # Mesma geometria de pavio dominante do Pin Bar, mas corpo perto do
    # TOPO do range -- deve ser classificado como Hammer, nunca Pin Bar.
    candles = [_candle(0, 10.0, 10.05, 9.0, 10.03)]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.HAMMER]


def test_detects_doji() -> None:
    candles = [_candle(0, 10.0, 10.5, 9.5, 10.001)]
    patterns = detect_patterns(candles)
    assert patterns[0].name == PatternName.DOJI
    assert patterns[0].direction == PatternDirection.NEUTRAL


def test_detects_marubozu_bullish() -> None:
    candles = [_candle(0, 10.0, 10.991, 9.99, 10.98)]
    patterns = detect_patterns(candles)
    assert patterns[0].name == PatternName.MARUBOZU_BULLISH
    assert patterns[0].direction == PatternDirection.BULLISH


def test_detects_marubozu_bearish() -> None:
    candles = [_candle(0, 10.98, 10.991, 9.99, 10.0)]
    patterns = detect_patterns(candles)
    assert patterns[0].name == PatternName.MARUBOZU_BEARISH
    assert patterns[0].direction == PatternDirection.BEARISH


def test_normal_candle_detects_nothing() -> None:
    candles = [_candle(0, 10.0, 10.2, 9.9, 10.1)]
    assert detect_patterns(candles) == []


# --- Padroes de 2 candles ------------------------------------------------


def test_detects_bullish_engulfing() -> None:
    candles = [
        _candle(0, 10.0, 10.05, 9.5, 9.6),  # baixa
        _candle(1, 9.55, 10.2, 9.5, 10.1),  # alta, engole o corpo anterior
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.BULLISH_ENGULFING]
    assert patterns[0].index == 1


def test_detects_bearish_engulfing() -> None:
    candles = [
        _candle(0, 9.6, 10.05, 9.55, 10.0),  # alta
        _candle(1, 10.1, 10.15, 9.4, 9.5),  # baixa, engole o corpo anterior
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.BEARISH_ENGULFING]


def test_detects_bullish_harami() -> None:
    candles = [
        _candle(0, 10.0, 10.05, 9.0, 9.2),  # baixa, corpo grande
        _candle(1, 9.4, 9.55, 9.35, 9.5),  # alta, corpo pequeno contido
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.BULLISH_HARAMI]


def test_detects_bearish_harami() -> None:
    candles = [
        _candle(0, 9.2, 10.05, 9.0, 10.0),  # alta, corpo grande
        _candle(1, 9.6, 9.7, 9.55, 9.5),  # baixa, corpo pequeno contido
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.BEARISH_HARAMI]


def test_detects_inside_bar() -> None:
    candles = [
        _candle(0, 10.0, 10.5, 9.5, 10.2),
        _candle(1, 10.1, 10.3, 9.8, 10.15),
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.INSIDE_BAR]
    assert patterns[0].direction == PatternDirection.NEUTRAL


def test_detects_outside_bar() -> None:
    candles = [
        _candle(0, 10.0, 10.2, 9.9, 10.1),
        _candle(1, 10.15, 10.6, 9.5, 10.5),
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.OUTSIDE_BAR]
    assert patterns[0].direction == PatternDirection.BULLISH


def test_detects_tweezer_top() -> None:
    # Maximas praticamente iguais (10.5 vs 10.48, dentro da tolerancia),
    # mas corpos desenhados para NAO colidir com engolfo/harami/inside-
    # outside (checados com prioridade mais alta): corpo atual maior que o
    # anterior (descarta harami), sem conter nem ser contido no range
    # anterior (descarta inside/outside), sem satisfazer a direcao de
    # abertura/fechamento do engolfo.
    candles = [
        _candle(0, 9.5, 10.5, 9.4, 10.4),  # alta
        _candle(1, 10.3, 10.48, 9.2, 9.3),  # baixa, maxima praticamente igual
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.TWEEZER_TOP]


def test_detects_tweezer_bottom() -> None:
    candles = [
        _candle(0, 10.4, 10.6, 9.5, 9.6),  # baixa
        _candle(1, 9.7, 10.8, 9.52, 10.7),  # alta, minima praticamente igual
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns) == [PatternName.TWEEZER_BOTTOM]


# --- Padroes de 3 candles ------------------------------------------------


def test_detects_morning_star() -> None:
    candles = [
        _candle(0, 10.0, 10.05, 9.0, 9.1),  # baixa forte
        _candle(1, 8.95, 9.05, 8.85, 8.9),  # estrela, corpo pequeno
        _candle(2, 9.0, 9.9, 8.95, 9.8),  # alta forte, recupera o corpo
    ]
    patterns = detect_patterns(candles)
    assert PatternName.MORNING_STAR in _names(patterns)
    star = next(p for p in patterns if p.name == PatternName.MORNING_STAR)
    assert star.direction == PatternDirection.BULLISH
    assert star.index == 2


def test_detects_evening_star() -> None:
    candles = [
        _candle(0, 9.1, 10.05, 9.0, 10.0),  # alta forte
        _candle(1, 10.05, 10.15, 9.95, 10.02),  # estrela, corpo pequeno
        _candle(2, 9.95, 10.0, 9.1, 9.2),  # baixa forte, perde o corpo
    ]
    patterns = detect_patterns(candles)
    assert PatternName.EVENING_STAR in _names(patterns)
    star = next(p for p in patterns if p.name == PatternName.EVENING_STAR)
    assert star.direction == PatternDirection.BEARISH
    assert star.index == 2


def test_detects_three_white_soldiers() -> None:
    candles = [
        _candle(0, 10.0, 10.32, 9.95, 10.3),
        _candle(1, 10.1, 10.62, 10.05, 10.6),
        _candle(2, 10.4, 10.92, 10.35, 10.9),
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns)[-1] == PatternName.THREE_WHITE_SOLDIERS
    assert patterns[-1].direction == PatternDirection.BULLISH


def test_detects_three_black_crows() -> None:
    candles = [
        _candle(0, 10.3, 10.35, 9.98, 10.0),
        _candle(1, 10.0, 10.05, 9.68, 9.7),
        _candle(2, 9.7, 9.75, 9.38, 9.4),
    ]
    patterns = detect_patterns(candles)
    assert _names(patterns)[-1] == PatternName.THREE_BLACK_CROWS
    assert patterns[-1].direction == PatternDirection.BEARISH


# --- Robustez / leak-safety ----------------------------------------------


def test_empty_sequence_never_raises() -> None:
    assert detect_patterns([]) == []
    assert detect_latest_pattern([]) is None


def test_single_candle_sequence_never_raises() -> None:
    candles = [_candle(0, 10.0, 10.2, 9.9, 10.1)]
    assert detect_patterns(candles) == []
    assert detect_latest_pattern(candles) is None


def test_two_candle_sequence_never_raises_three_candle_check() -> None:
    candles = [
        _candle(0, 10.0, 10.2, 9.9, 10.1),
        _candle(1, 10.1, 10.3, 10.0, 10.2),
    ]
    # Nao deve tentar checar padrao de 3 candles com apenas 2 disponiveis.
    detect_patterns(candles)


def test_detect_latest_pattern_only_considers_last_bar() -> None:
    candles = [
        _candle(0, 10.0, 10.05, 9.0, 10.03),  # Hammer no indice 0
        _candle(1, 10.0, 10.2, 9.9, 10.1),  # candle normal no indice 1
    ]
    assert detect_latest_pattern(candles) is None


def test_detect_latest_pattern_returns_pattern_on_last_bar() -> None:
    candles = [
        _candle(0, 10.0, 10.2, 9.9, 10.1),  # candle normal
        _candle(1, 10.0, 10.05, 9.0, 10.03),  # Hammer no indice 1 (ultimo)
    ]
    latest = detect_latest_pattern(candles)
    assert latest is not None
    assert latest.name == PatternName.HAMMER
    assert latest.index == 1


def test_truncating_tail_does_not_change_earlier_detections() -> None:
    candles = [
        _candle(0, 10.0, 10.05, 9.5, 9.6),
        _candle(1, 9.55, 10.2, 9.5, 10.1),  # engolfo de alta no indice 1
        _candle(2, 10.15, 10.6, 9.5, 10.5),  # candle extra no final
    ]
    full = detect_patterns(candles)
    truncated = detect_patterns(candles[:2])
    engulfing_full = next(p for p in full if p.index == 1)
    engulfing_truncated = next(p for p in truncated if p.index == 1)
    assert engulfing_full.name == engulfing_truncated.name
    assert engulfing_full.direction == engulfing_truncated.direction

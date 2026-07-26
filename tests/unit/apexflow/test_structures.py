"""Estruturas de grafico do Price Action Engine (`app.apexflow.structures`)."""

from __future__ import annotations

from datetime import timedelta

from app.apexflow.structures import (
    StructureKind,
    detect_structures,
    latest_by_kind,
)
from app.market.features import build_candle_features
from app.market.price_action import PatternDirection
from tests.unit.apexflow.conftest import NOW, POINT, FakeCandle


def series(shape: list[tuple[float, float, float]], *, volume: int = 100) -> list[FakeCandle]:
    """Constroi candles a partir de (low, high, close) por barra."""
    candles: list[FakeCandle] = []
    start = NOW - timedelta(minutes=5 * len(shape))
    for index, (low, high, close) in enumerate(shape):
        candles.append(
            FakeCandle(
                open_time=start + timedelta(minutes=5 * index),
                open=(low + high) / 2,
                high=high,
                low=low,
                close=close,
                tick_volume=volume,
            )
        )
    return candles


def flat_shape(count: int, *, base: float = 1.1000, amplitude: float = 0.0010):
    return [(base - amplitude, base + amplitude, base) for _ in range(count)]


def detect(candles: list[FakeCandle]):
    features = build_candle_features(candles, point=POINT)
    return detect_structures(candles, features)


def kinds(candles: list[FakeCandle]) -> set[StructureKind]:
    return {structure.kind for structure in detect(candles)}


# --- Guardas ---------------------------------------------------------------


def test_short_series_detects_nothing() -> None:
    assert detect_structures(series(flat_shape(3)), build_candle_features(
        series(flat_shape(3)), point=POINT
    )) == []


def test_empty_features_detects_nothing() -> None:
    import pandas as pd

    assert detect_structures(series(flat_shape(60)), pd.DataFrame()) == []


# --- Topo / fundo duplo ----------------------------------------------------


def test_double_top_needs_a_retracement_between_the_peaks() -> None:
    """Dois topos no mesmo nivel COM recuo relevante entre eles."""
    shape = flat_shape(30)
    # topo 1
    shape += [(1.1010, 1.1080, 1.1020)]
    # recuo profundo
    shape += [(1.0940, 1.1000, 1.0950) for _ in range(6)]
    # topo 2 no mesmo nivel
    shape += [(1.1010, 1.1081, 1.1015)]
    shape += [(1.0960, 1.1020, 1.0970) for _ in range(6)]
    detected = kinds(series(shape))
    assert StructureKind.DOUBLE_TOP in detected


def test_double_bottom_is_bullish() -> None:
    shape = flat_shape(30)
    shape += [(1.0920, 1.0990, 1.0980)]
    shape += [(1.1000, 1.1060, 1.1050) for _ in range(6)]
    shape += [(1.0919, 1.0990, 1.0985)]
    shape += [(1.0990, 1.1050, 1.1040) for _ in range(6)]
    structures = [s for s in detect(series(shape)) if s.kind == StructureKind.DOUBLE_BOTTOM]
    assert structures
    assert structures[-1].direction == PatternDirection.BULLISH


def test_peaks_at_different_levels_are_not_a_double_top() -> None:
    shape = flat_shape(30)
    shape += [(1.1010, 1.1080, 1.1020)]
    shape += [(1.0940, 1.1000, 1.0950) for _ in range(6)]
    # segundo topo MUITO acima do primeiro
    shape += [(1.1100, 1.1400, 1.1300)]
    shape += [(1.1000, 1.1100, 1.1050) for _ in range(6)]
    assert StructureKind.DOUBLE_TOP not in kinds(series(shape))


# --- Compressao / expansao -------------------------------------------------


def test_shrinking_amplitude_is_compression() -> None:
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    narrow = [(1.0995, 1.1005, 1.1000) for _ in range(20)]
    assert StructureKind.COMPRESSION in kinds(series(wide + narrow))


def test_growing_amplitude_is_expansion() -> None:
    narrow = [(1.0995, 1.1005, 1.1000) for _ in range(30)]
    wide = [(1.0900, 1.1100, 1.1000) for _ in range(20)]
    assert StructureKind.EXPANSION in kinds(series(narrow + wide))


def test_compression_and_expansion_are_directionless() -> None:
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    narrow = [(1.0995, 1.1005, 1.1000) for _ in range(20)]
    for structure in detect(series(wide + narrow)):
        if structure.kind in (StructureKind.COMPRESSION, StructureKind.EXPANSION):
            assert structure.direction is None


# --- Acumulacao / distribuicao --------------------------------------------


def test_narrow_range_with_low_volume_is_only_compression() -> None:
    """Faixa estreita sem volume e falta de interesse, nao absorcao."""
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    narrow = [(1.0995, 1.1005, 1.1000) for _ in range(20)]
    candles = series(wide + narrow)
    rebuilt = []
    for index, candle in enumerate(candles):
        rebuilt.append(
            FakeCandle(
                open_time=candle.open_time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                tick_volume=400 if index < 30 else 40,
            )
        )
    detected = kinds(rebuilt)
    assert StructureKind.COMPRESSION in detected
    assert StructureKind.ACCUMULATION not in detected
    assert StructureKind.DISTRIBUTION not in detected


def test_narrow_range_with_high_volume_near_the_low_is_accumulation() -> None:
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    # faixa estreita, preco fechando na parte BAIXA da faixa
    narrow = [(1.0990, 1.1010, 1.0993) for _ in range(20)]
    candles = series(wide + narrow)
    rebuilt = [
        FakeCandle(
            open_time=candle.open_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            tick_volume=100 if index < 30 else 300,
        )
        for index, candle in enumerate(candles)
    ]
    detected = latest_by_kind(detect(rebuilt))
    assert StructureKind.ACCUMULATION in detected
    assert detected[StructureKind.ACCUMULATION].direction == PatternDirection.BULLISH


def test_narrow_range_with_high_volume_near_the_high_is_distribution() -> None:
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    narrow = [(1.0990, 1.1010, 1.1007) for _ in range(20)]
    candles = series(wide + narrow)
    rebuilt = [
        FakeCandle(
            open_time=candle.open_time,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            tick_volume=100 if index < 30 else 300,
        )
        for index, candle in enumerate(candles)
    ]
    detected = latest_by_kind(detect(rebuilt))
    assert StructureKind.DISTRIBUTION in detected
    assert detected[StructureKind.DISTRIBUTION].direction == PatternDirection.BEARISH


# --- Range ----------------------------------------------------------------


def test_repeated_touches_on_both_sides_form_a_range() -> None:
    shape = flat_shape(30, amplitude=0.0020)
    shape += [(1.0990, 1.1010, 1.1000) for _ in range(20)]
    assert StructureKind.RANGE in kinds(series(shape))


def test_a_trend_is_not_a_range() -> None:
    shape = []
    price = 1.1000
    for _ in range(60):
        price += 0.0020
        shape.append((price - 0.0005, price + 0.0005, price))
    assert StructureKind.RANGE not in kinds(series(shape))


# --- Micro estruturas -----------------------------------------------------


def test_consistent_recent_bars_form_a_micro_trend() -> None:
    shape = flat_shape(40)
    price = 1.1000
    for _ in range(5):
        price += 0.0025
        shape.append((price - 0.0005, price + 0.0005, price))
    detected = latest_by_kind(detect(series(shape)))
    assert StructureKind.MICRO_TREND in detected
    assert detected[StructureKind.MICRO_TREND].direction == PatternDirection.BULLISH


def test_last_bar_against_a_micro_trend_is_a_micro_pullback() -> None:
    shape = flat_shape(40)
    price = 1.1000
    for _ in range(5):
        price += 0.0025
        shape.append((price - 0.0005, price + 0.0005, price))
    # ultima barra recua um pouco, sem desfazer o movimento
    price -= 0.0008
    shape.append((price - 0.0005, price + 0.0005, price))
    detected = latest_by_kind(detect(series(shape)))
    assert StructureKind.MICRO_PULLBACK in detected
    assert detected[StructureKind.MICRO_PULLBACK].direction == PatternDirection.BULLISH


def test_a_full_reversal_is_not_a_micro_pullback() -> None:
    """Um recuo que desfaz todo o movimento e reversao, nao correcao."""
    shape = flat_shape(40)
    price = 1.1000
    for _ in range(5):
        price += 0.0020
        shape.append((price - 0.0005, price + 0.0005, price))
    price -= 0.0140  # desfaz tudo e mais
    shape.append((price - 0.0005, price + 0.0005, price))
    assert StructureKind.MICRO_PULLBACK not in kinds(series(shape))


# --- Contrato -------------------------------------------------------------


def test_every_structure_explains_itself() -> None:
    wide = [(1.0950, 1.1050, 1.1000) for _ in range(30)]
    narrow = [(1.0995, 1.1005, 1.1000) for _ in range(20)]
    for structure in detect(series(wide + narrow)):
        assert structure.description
        assert structure.label
        assert structure.index >= 0


def test_latest_by_kind_keeps_the_most_recent() -> None:
    shape = flat_shape(30)
    shape += [(1.1010, 1.1080, 1.1020)]
    shape += [(1.0940, 1.1000, 1.0950) for _ in range(6)]
    shape += [(1.1010, 1.1081, 1.1015)]
    shape += [(1.0960, 1.1020, 1.0970) for _ in range(6)]
    structures = detect(series(shape))
    latest = latest_by_kind(structures)
    for kind, structure in latest.items():
        same_kind = [s for s in structures if s.kind == kind]
        assert structure.index == max(s.index for s in same_kind)

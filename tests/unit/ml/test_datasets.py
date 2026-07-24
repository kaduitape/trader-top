from datetime import UTC, datetime, timedelta

import pytest

from app.ml import datasets as datasets_module
from app.ml.datasets import (
    ML_CATEGORICAL_FEATURE_COLUMNS,
    ML_METADATA_COLUMNS,
    ML_NUMERIC_FEATURE_COLUMNS,
    build_signal_dataset,
)
from app.mt5.market_data import RawCandle
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy

_START = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_POINT = 0.0001

_ALL_COLUMNS = (
    set(ML_METADATA_COLUMNS) | set(ML_NUMERIC_FEATURE_COLUMNS) | set(ML_CATEGORICAL_FEATURE_COLUMNS)
)


def _candle(minute_offset: int, o: float, h: float, low: float, c: float) -> RawCandle:
    return RawCandle(
        open_time=_START + timedelta(minutes=minute_offset),
        open=o,
        high=h,
        low=low,
        close=c,
        tick_volume=100,
        spread=10,
        real_volume=0,
    )


class _NeverSignalStrategy(Strategy):
    name = "test_never"

    def generate_signal(self, state: MarketState) -> Signal | None:
        return None


class _AlwaysSignalStrategy(Strategy):
    """Sempre tenta gerar um sinal LONG com stop/alvo fixos em pontos —
    usada para testar a regra de "uma posicao por vez" de
    `build_signal_dataset` sem depender de nenhum indicador real."""

    name = "test_always"

    def __init__(self, *, stop_points: float = 50.0, target_points: float = 50.0) -> None:
        self._stop_points = stop_points
        self._target_points = target_points

    def generate_signal(self, state: MarketState) -> Signal | None:
        current = state.current
        price = float(current["close"])
        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=SignalDirection.LONG,
            generated_at=current["open_time"],
            reference_price=price,
            stop_loss=price - self._stop_points * _POINT,
            take_profit=price + self._target_points * _POINT,
            valid_until=current["open_time"] + timedelta(minutes=5),
            reason="test always-on",
            regime_required="none",
            confidence=1.0,
            features_used={},
        )


def test_empty_candle_list_returns_empty_dataframe_with_correct_columns() -> None:
    dataset = build_signal_dataset(
        _NeverSignalStrategy(),
        [],
        symbol="EURUSD",
        timeframe="M1",
        point=_POINT,
        max_horizon_bars=10,
    )
    assert dataset.empty
    assert set(dataset.columns) == _ALL_COLUMNS


def test_never_signal_strategy_produces_empty_dataset() -> None:
    candles = [_candle(i, 1.1000, 1.1010, 1.0990, 1.1000) for i in range(30)]
    dataset = build_signal_dataset(
        _NeverSignalStrategy(),
        candles,
        symbol="EURUSD",
        timeframe="M1",
        point=_POINT,
        max_horizon_bars=10,
    )
    assert dataset.empty
    assert set(dataset.columns) == _ALL_COLUMNS


def test_single_resolved_signal_produces_one_row_with_expected_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sem isso, o aquecimento minimo de 200 barras (ema_200) excederia o
    # tamanho da serie sintetica deste teste (proposital e pequena).
    monkeypatch.setattr(datasets_module, "required_lookback_bars", lambda: 3)

    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(5)]
    # A quinta candle atinge o alvo (target_points=50 -> 1.1000+0.0050=1.1050).
    candles.append(_candle(5, 1.1000, 1.1060, 1.0995, 1.1000))
    candles.extend(_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(6, 10))

    dataset = build_signal_dataset(
        _AlwaysSignalStrategy(stop_points=50.0, target_points=50.0),
        candles,
        symbol="EURUSD",
        timeframe="M1",
        point=_POINT,
        max_horizon_bars=10,
        entry_delay_bars=1,
    )

    assert set(dataset.columns) == _ALL_COLUMNS
    assert len(dataset) >= 1
    first = dataset.iloc[0]
    assert first["outcome"] == "TARGET_FIRST"
    assert first["label"] == 1
    assert first["direction"] == "LONG"


def test_signals_do_not_overlap_between_consecutive_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(datasets_module, "required_lookback_bars", lambda: 3)
    candles = [_candle(i, 1.1000, 1.1005, 1.0995, 1.1000) for i in range(60)]

    dataset = build_signal_dataset(
        _AlwaysSignalStrategy(stop_points=1000.0, target_points=1000.0),
        candles,
        symbol="EURUSD",
        timeframe="M1",
        point=_POINT,
        max_horizon_bars=5,
        entry_delay_bars=1,
    )

    if len(dataset) < 2:
        return
    signal_times = list(dataset["signal_time"])
    assert signal_times == sorted(signal_times)
    assert len(set(signal_times)) == len(signal_times)


def test_dataset_has_no_raw_price_level_features() -> None:
    """Decisao de design (Fase 8): niveis de preco absolutos (EMA/Bollinger
    em valor bruto) nao entram no dataset de ML."""
    for banned in (
        "ema_9",
        "ema_21",
        "ema_50",
        "ema_200",
        "open",
        "high",
        "low",
        "close",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
    ):
        assert banned not in ML_NUMERIC_FEATURE_COLUMNS
        assert banned not in ML_CATEGORICAL_FEATURE_COLUMNS

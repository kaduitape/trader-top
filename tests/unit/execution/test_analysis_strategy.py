from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from app.execution.analysis_strategy import AnalysisReportStrategy
from app.strategies.base import MarketState, SignalDirection


def _report(*, stop_loss: float, entry: float = 100.0):
    factor = SimpleNamespace(name="structure", raw_score=91.0)
    return SimpleNamespace(
        recommendation="ENTER",
        trade_levels=SimpleNamespace(
            entry=entry,
            stop_loss=stop_loss,
            take_profit_3=106.0 if stop_loss < entry else 94.0,
        ),
        generated_at=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        symbol="EURUSD",
        score=SimpleNamespace(total_score=91.0, factors=[factor]),
        trend=SimpleNamespace(value="UP"),
        probability_estimate=91.0,
    )


def test_analysis_strategy_emits_once_on_expected_candle() -> None:
    candle_time = datetime(2026, 7, 24, 11, 45, tzinfo=UTC)
    state = MarketState(
        symbol="EURUSD",
        timeframe="M15",
        features=pd.DataFrame({"open_time": [candle_time]}),
        regime=None,
    )
    strategy = AnalysisReportStrategy(
        _report(stop_loss=98.0),
        expected_open_time=candle_time,
    )

    signal = strategy.generate_signal(state)

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.take_profit == 106.0
    assert strategy.generate_signal(state) is None


def test_analysis_strategy_does_not_emit_for_another_candle() -> None:
    expected = datetime(2026, 7, 24, 11, 45, tzinfo=UTC)
    state = MarketState(
        symbol="EURUSD",
        timeframe="M15",
        features=pd.DataFrame(
            {"open_time": [datetime(2026, 7, 24, 11, 30, tzinfo=UTC)]}
        ),
        regime=None,
    )

    signal = AnalysisReportStrategy(
        _report(stop_loss=102.0),
        expected_open_time=expected,
    ).generate_signal(state)

    assert signal is None

"""Adaptador do relatorio de analise para a pipeline de execucao demo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.analysis_service import AnalysisReport
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy


class AnalysisReportStrategy(Strategy):
    """Emite no maximo um sinal, apenas para a ultima candle analisada.

    O cursor persistente de ``DemoExecutionEngine`` garante que a mesma
    candle nao seja reprocessada em ciclos posteriores do worker.
    """

    name = "analysis_score"

    def __init__(self, report: AnalysisReport, *, expected_open_time: datetime) -> None:
        self._report = report
        self._expected_open_time = expected_open_time
        self._emitted = False

    def generate_signal(self, state: MarketState) -> Signal | None:
        if self._emitted or self._report.recommendation != "ENTER":
            return None
        levels = self._report.trade_levels
        if levels is None:
            return None

        current_open_time = state.current.get("open_time")
        if current_open_time is None:
            return None
        current_time = current_open_time.to_pydatetime()
        if current_time != self._expected_open_time:
            return None

        self._emitted = True
        direction = (
            SignalDirection.LONG
            if levels.stop_loss < levels.entry
            else SignalDirection.SHORT
        )
        generated_at = self._report.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)

        return Signal(
            symbol=self._report.symbol,
            strategy_name=self.name,
            direction=direction,
            generated_at=generated_at,
            reference_price=levels.entry,
            stop_loss=levels.stop_loss,
            take_profit=levels.take_profit_3,
            valid_until=generated_at + timedelta(minutes=15),
            reason=(
                f"Analise automatica aprovada com score "
                f"{self._report.score.total_score:.1f}."
            ),
            regime_required=self._report.trend.value,
            confidence=self._report.probability_estimate,
            features_used={
                factor.name: factor.raw_score for factor in self._report.score.factors
            },
        )

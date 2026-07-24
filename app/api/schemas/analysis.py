"""Schemas Pydantic do endpoint de analise (Fase 18.9). Espelham as
dataclasses de `app.services.analysis_service`/`app.market.scoring`/
`app.market.trade_levels` — nunca omitem os 7 fatores do score (mesma
regra anti-numero-unico ja aplicada em todo o motor de analise)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.market.trade_levels import TradeLevels
from app.services.analysis_service import AnalysisReport


class FactorScoreOut(BaseModel):
    name: str
    raw_score: float
    rationale: list[str]
    weight: float
    weighted_contribution: float


class OpportunityScoreOut(BaseModel):
    symbol: str
    timeframe: str
    generated_at: datetime
    factors: list[FactorScoreOut]
    total_score: float
    threshold: float
    recommendation: str
    reasons_below_threshold: list[str]


class TradeLevelsOut(BaseModel):
    entry: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    risk_reward_1: float
    risk_reward_2: float
    risk_reward_3: float
    trailing_activation_price: float
    break_even_price: float


class CandlestickPatternOut(BaseModel):
    name: str
    direction: str
    index: int
    open_time: datetime
    strength: float
    description: str


class AnalysisReportOut(BaseModel):
    symbol: str
    timeframe: str
    generated_at: datetime
    trend: str
    dominant_pattern: CandlestickPatternOut | None
    confluences: list[str]
    multi_timeframe_alignment: dict[str, str]
    score: OpportunityScoreOut
    probability_estimate: float
    trade_levels: TradeLevelsOut | None
    justification: list[str]
    recommendation: str
    rejection_reasons: list[str]


def _trade_levels_to_schema(levels: TradeLevels) -> TradeLevelsOut:
    return TradeLevelsOut(
        entry=levels.entry,
        stop_loss=levels.stop_loss,
        take_profit_1=levels.take_profit_1,
        take_profit_2=levels.take_profit_2,
        take_profit_3=levels.take_profit_3,
        risk_reward_1=levels.risk_reward_1,
        risk_reward_2=levels.risk_reward_2,
        risk_reward_3=levels.risk_reward_3,
        trailing_activation_price=levels.trailing_activation_price,
        break_even_price=levels.break_even_price,
    )


def to_schema(report: AnalysisReport) -> AnalysisReportOut:
    return AnalysisReportOut(
        symbol=report.symbol,
        timeframe=report.timeframe.value,
        generated_at=report.generated_at,
        trend=report.trend.value,
        dominant_pattern=(
            CandlestickPatternOut(
                name=report.dominant_pattern.name.value,
                direction=report.dominant_pattern.direction.value,
                index=report.dominant_pattern.index,
                open_time=report.dominant_pattern.open_time,
                strength=report.dominant_pattern.strength,
                description=report.dominant_pattern.description,
            )
            if report.dominant_pattern is not None
            else None
        ),
        confluences=report.confluences,
        multi_timeframe_alignment={
            tf.value: label for tf, label in report.multi_timeframe_alignment.items()
        },
        score=OpportunityScoreOut(
            symbol=report.score.symbol,
            timeframe=report.score.timeframe.value,
            generated_at=report.score.generated_at,
            factors=[
                FactorScoreOut(
                    name=f.name,
                    raw_score=f.raw_score,
                    rationale=f.rationale,
                    weight=f.weight,
                    weighted_contribution=f.weighted_contribution,
                )
                for f in report.score.factors
            ],
            total_score=report.score.total_score,
            threshold=report.score.threshold,
            recommendation=report.score.recommendation,
            reasons_below_threshold=report.score.reasons_below_threshold,
        ),
        probability_estimate=report.probability_estimate,
        trade_levels=(
            _trade_levels_to_schema(report.trade_levels)
            if report.trade_levels is not None
            else None
        ),
        justification=report.justification,
        recommendation=report.recommendation,
        rejection_reasons=report.rejection_reasons,
    )

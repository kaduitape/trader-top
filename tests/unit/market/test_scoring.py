from datetime import UTC, datetime, timedelta

import pytest

from app.market.price_action import CandlestickPattern, PatternDirection, PatternName
from app.market.scoring import (
    FactorScore,
    ScoreWeights,
    compute_opportunity_score,
    score_correlation,
    score_fundamentals,
    score_liquidity,
    score_news,
    score_price_action,
    score_structure,
    score_volume,
)
from app.market.smc import (
    FairValueGap,
    LiquidityEventKind,
    LiquiditySweep,
    OrderBlock,
)
from app.market.structure import (
    StructureEvent,
    StructureEventType,
    StructureLabel,
    SwingKind,
    SwingPoint,
    TrendStructureLabel,
)
from app.market.volume_analysis import VolumeEvent, VolumeEventKind
from app.mt5.market_data import Timeframe
from app.news.provider import FundamentalsAssessment, NewsAssessment, ProviderStatus

_START = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)


# --- ScoreWeights -------------------------------------------------------------


def test_score_weights_default_sums_to_one() -> None:
    ScoreWeights()  # nao deve levantar


def test_score_weights_invalid_sum_raises() -> None:
    with pytest.raises(ValueError):
        ScoreWeights(structure=0.5, price_action=0.5, liquidity=0.5)


# --- score_structure -----------------------------------------------------------


def _swing(index: int, price: float, kind: SwingKind) -> SwingPoint:
    return SwingPoint(
        index=index,
        open_time=_START + timedelta(minutes=index),
        price=price,
        kind=kind,
        confirmed_at_index=index,
    )


def _event(
    event_type: StructureEventType, index: int, direction: PatternDirection
) -> StructureEvent:
    return StructureEvent(
        type=event_type,
        index=index,
        open_time=_START + timedelta(minutes=index),
        broken_level=100.0,
        direction=direction,
    )


def test_score_structure_no_events_is_neutral() -> None:
    result = score_structure([], [])
    assert result.raw_score == 50.0
    assert result.rationale


def test_score_structure_bos_choch_mss_scores() -> None:
    bos = score_structure([_event(StructureEventType.BOS, 1, PatternDirection.BULLISH)], [])
    choch = score_structure([_event(StructureEventType.CHOCH, 1, PatternDirection.BEARISH)], [])
    mss = score_structure([_event(StructureEventType.MSS, 1, PatternDirection.BULLISH)], [])

    assert bos.raw_score == 70.0
    assert choch.raw_score == 40.0
    assert mss.raw_score == 90.0


def test_score_structure_consistent_trend_labels_add_bonus() -> None:
    labels = [
        StructureLabel(swing=_swing(2, 100, SwingKind.HIGH), label=TrendStructureLabel.HH),
        StructureLabel(swing=_swing(4, 95, SwingKind.LOW), label=TrendStructureLabel.HL),
    ]
    result = score_structure([_event(StructureEventType.BOS, 5, PatternDirection.BULLISH)], labels)
    assert result.raw_score == 80.0


# --- score_price_action ---------------------------------------------------------


def _pattern(direction: PatternDirection, strength: float, index: int = 0) -> CandlestickPattern:
    return CandlestickPattern(
        name=PatternName.HAMMER,
        direction=direction,
        index=index,
        open_time=_START + timedelta(minutes=index),
        strength=strength,
        description="",
    )


def test_score_price_action_no_patterns_is_neutral() -> None:
    result = score_price_action([])
    assert result.raw_score == 50.0


def test_score_price_action_neutral_pattern_is_neutral() -> None:
    result = score_price_action([_pattern(PatternDirection.NEUTRAL, 1.0)])
    assert result.raw_score == 50.0


def test_score_price_action_strength_scales_score() -> None:
    strong = score_price_action([_pattern(PatternDirection.BULLISH, 1.0)])
    weak = score_price_action([_pattern(PatternDirection.BULLISH, 0.5)])
    assert strong.raw_score == pytest.approx(90.0)
    assert weak.raw_score == pytest.approx(70.0)


# --- score_liquidity -------------------------------------------------------------


def _order_block(mitigated: bool) -> OrderBlock:
    return OrderBlock(
        index=0,
        open_time=_START,
        direction=PatternDirection.BULLISH,
        high=101.0,
        low=99.0,
        mitigated=mitigated,
        mitigated_at_index=None,
        is_breaker=False,
    )


def _fvg(filled: bool) -> FairValueGap:
    return FairValueGap(
        index=0,
        open_time=_START,
        direction=PatternDirection.BULLISH,
        gap_high=101.0,
        gap_low=100.0,
        filled=filled,
        filled_at_index=None,
    )


def _sweep(reversal_confirmed: bool) -> LiquiditySweep:
    return LiquiditySweep(
        kind=LiquidityEventKind.SWEEP,
        index=0,
        open_time=_START,
        swept_price=110.0,
        direction=PatternDirection.BEARISH,
        reversal_confirmed=reversal_confirmed,
    )


def test_score_liquidity_nothing_detected_is_neutral() -> None:
    result = score_liquidity([], [], [], None)
    assert result.raw_score == 50.0


def test_score_liquidity_combines_all_three_sources() -> None:
    result = score_liquidity([_order_block(False)], [_fvg(False)], [_sweep(True)], None)
    assert result.raw_score == pytest.approx(85.0)


def test_score_liquidity_clips_at_100() -> None:
    obs = [_order_block(False)] * 3
    fvgs = [_fvg(False)] * 3
    sweeps = [_sweep(True)] * 3
    result = score_liquidity(obs, fvgs, sweeps, None)
    assert result.raw_score == 100.0


# --- score_volume -----------------------------------------------------------------


def _volume_event(kind: VolumeEventKind) -> VolumeEvent:
    return VolumeEvent(kind=kind, index=0, open_time=_START, description="")


def test_score_volume_no_events_is_neutral() -> None:
    assert score_volume([]).raw_score == 50.0


def test_score_volume_single_divergence() -> None:
    result = score_volume([_volume_event(VolumeEventKind.BULLISH_DIVERGENCE)])
    assert result.raw_score == pytest.approx(65.0)


def test_score_volume_clips_at_100() -> None:
    events = [_volume_event(VolumeEventKind.BULLISH_DIVERGENCE)] * 3 + [
        _volume_event(VolumeEventKind.EXHAUSTION)
    ] * 3
    result = score_volume(events)
    assert result.raw_score == 100.0


# --- score_news / score_fundamentals / score_correlation ----------------------


def test_score_news_is_a_thin_passthrough() -> None:
    assessment = NewsAssessment(status=ProviderStatus.OK, score_contribution=75.0, message="teste")
    result = score_news(assessment)
    assert result.raw_score == 75.0
    assert result.rationale == ["teste"]


def test_score_fundamentals_is_a_thin_passthrough() -> None:
    assessment = FundamentalsAssessment(
        status=ProviderStatus.OK, score_contribution=60.0, message="ok"
    )
    result = score_fundamentals(assessment)
    assert result.raw_score == 60.0


def test_score_correlation_is_always_neutral() -> None:
    result = score_correlation()
    assert result.raw_score == 50.0
    assert result.rationale


# --- compute_opportunity_score --------------------------------------------------


def _factor(raw_score: float, name: str = "x") -> FactorScore:
    return FactorScore(name=name, raw_score=raw_score, rationale=["r"])


def test_compute_opportunity_score_at_exact_threshold_enters() -> None:
    score = compute_opportunity_score(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        generated_at=_START,
        structure=_factor(90.0, "structure"),
        price_action=_factor(90.0, "price_action"),
        liquidity=_factor(90.0, "liquidity"),
        volume=_factor(90.0, "volume"),
        news=_factor(90.0, "news"),
        fundamentals=_factor(90.0, "fundamentals"),
        correlation=_factor(90.0, "correlation"),
        threshold=90.0,
    )

    assert score.total_score == pytest.approx(90.0)
    assert score.recommendation == "ENTER"
    assert score.reasons_below_threshold == []
    assert len(score.factors) == 7


def test_compute_opportunity_score_below_threshold_lists_weak_factors() -> None:
    score = compute_opportunity_score(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        generated_at=_START,
        structure=_factor(90.0, "structure"),
        price_action=_factor(90.0, "price_action"),
        liquidity=_factor(20.0, "liquidity"),
        volume=_factor(90.0, "volume"),
        news=_factor(90.0, "news"),
        fundamentals=_factor(90.0, "fundamentals"),
        correlation=_factor(90.0, "correlation"),
        threshold=90.0,
    )

    assert score.total_score == pytest.approx(79.5)
    assert score.recommendation == "DO_NOT_ENTER"
    assert any("liquidity" in reason for reason in score.reasons_below_threshold)


def test_compute_opportunity_score_weights_applied_to_each_factor() -> None:
    score = compute_opportunity_score(
        symbol="EURUSD",
        timeframe=Timeframe.M15,
        generated_at=_START,
        structure=_factor(100.0, "structure"),
        price_action=_factor(0.0, "price_action"),
        liquidity=_factor(0.0, "liquidity"),
        volume=_factor(0.0, "volume"),
        news=_factor(0.0, "news"),
        fundamentals=_factor(0.0, "fundamentals"),
        correlation=_factor(0.0, "correlation"),
    )

    structure_factor = next(f for f in score.factors if f.name == "structure")
    assert structure_factor.weight == pytest.approx(0.30)
    assert structure_factor.weighted_contribution == pytest.approx(30.0)
    assert score.total_score == pytest.approx(30.0)

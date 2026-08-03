from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.multi_timeframe import ANALYSIS_TIMEFRAMES, SymbolNotFoundError
from app.market.scoring import ScoreWeights
from app.mt5.market_data import RawCandle, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification
from app.news.provider import FundamentalsAssessment, NewsAssessment, ProviderStatus
from app.services.analysis_service import analyze_symbol

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _spec(name: str) -> SymbolSpecification:
    return SymbolSpecification(
        name=name,
        description="Test symbol",
        digits=5,
        point=0.00001,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        trade_contract_size=100_000.0,
        spread=2,
        trade_mode=4,
        visible=True,
    )


def _make_uptrend_candles(
    n: int, *, start: datetime, step: timedelta, seed: int = 11
) -> list[RawCandle]:
    rng = np.random.default_rng(seed)
    trend = np.linspace(0, 60, n)
    oscillation = 5 * np.sin(np.arange(n) * 0.6)
    closes = 100 + trend + oscillation

    candles: list[RawCandle] = []
    prev_close = float(closes[0]) - 1.0
    for i in range(n):
        open_ = prev_close
        close = float(closes[i])
        high = max(open_, close) + abs(rng.normal(0, 0.1))
        low = min(open_, close) - abs(rng.normal(0, 0.1))
        candles.append(
            RawCandle(
                open_time=start + step * i,
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=int(100 + rng.integers(0, 50)),
                spread=2,
                real_volume=0,
            )
        )
        prev_close = close

    # Ultimo candle: marubozu de alta forte, para gerar um padrao de price
    # action com forca alta e deterministica.
    last = candles[-1]
    strong_close = last.close + 3.0
    candles[-1] = RawCandle(
        open_time=last.open_time,
        open=last.close,
        high=strong_close + 0.01,
        low=last.close - 0.01,
        close=strong_close,
        tick_volume=last.tick_volume,
        spread=2,
        real_volume=0,
    )
    return candles


def _make_choppy_candles(
    n: int, *, start: datetime, step: timedelta, seed: int = 13
) -> list[RawCandle]:
    rng = np.random.default_rng(seed)
    oscillation = 3 * np.sin(np.arange(n) * 0.8)
    closes = 100 + oscillation

    candles: list[RawCandle] = []
    prev_close = float(closes[0])
    for i in range(n):
        open_ = prev_close
        close = float(closes[i])
        high = max(open_, close) + abs(rng.normal(0, 0.05))
        low = min(open_, close) - abs(rng.normal(0, 0.05))
        candles.append(
            RawCandle(
                open_time=start + step * i,
                open=open_,
                high=high,
                low=low,
                close=close,
                tick_volume=int(100 + rng.integers(0, 10)),
                spread=2,
                real_volume=0,
            )
        )
        prev_close = close
    return candles


def _seed_timeframe(
    db_session, symbol_name: str, timeframe: Timeframe, candles: list[RawCandle]
) -> None:
    symbol = SymbolRepository(db_session).get_by_name(symbol_name)
    if symbol is None:
        symbol = SymbolRepository(db_session).upsert_from_specification(_spec(symbol_name))
    CandleRepository(db_session).bulk_upsert(symbol.id, timeframe.value, candles)
    db_session.flush()


def _seed_full_coverage(db_session, symbol_name: str) -> None:
    """Semeia o que os portoes locais exigem de verdade.

    Operacionais (H1/M30/M15 quando a entrada e no M15): features completas.
    Contexto macro (MN1/W1/D1/H4): apenas presenca minima — exigir 200
    candles mensais seria exigir 16 anos de historico.
    """
    for timeframe, quantidade, passo in (
        (Timeframe.MN1, 40, timedelta(days=30)),
        (Timeframe.W1, 40, timedelta(days=7)),
        (Timeframe.D1, 40, timedelta(days=1)),
        (Timeframe.H4, 40, timedelta(hours=4)),
        (Timeframe.H1, 260, timedelta(hours=1)),
        (Timeframe.M30, 260, timedelta(minutes=30)),
        (Timeframe.M15, 260, timedelta(minutes=15)),
    ):
        candles = _make_uptrend_candles(
            quantidade, start=_NOW - passo * quantidade, step=passo
        )
        _seed_timeframe(db_session, symbol_name, timeframe, candles)


class _CountingNewsProvider:
    """Conta consultas — e o que prova a economia de cota."""

    def __init__(self) -> None:
        self.calls = 0

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        self.calls += 1
        return NewsAssessment(
            status=ProviderStatus.OK, score_contribution=100.0, message="noticia forte"
        )


class _FakeNewsProvider:
    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        return NewsAssessment(
            status=ProviderStatus.OK, score_contribution=100.0, message="noticia forte"
        )


class _FakeFundamentalsProvider:
    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
        return FundamentalsAssessment(
            status=ProviderStatus.OK, score_contribution=100.0, message="fundamento forte"
        )


def test_unknown_symbol_propagates_symbol_not_found(db_session) -> None:
    with pytest.raises(SymbolNotFoundError):
        analyze_symbol(db_session, symbol="DOES_NOT_EXIST_ANALYSIS", now=_NOW)


def test_clean_uptrend_produces_enter_recommendation(db_session) -> None:
    """Caminho completo: com cobertura real e fontes externas respondendo,
    o sistema CONSEGUE chegar a ENTER — passando pelos portoes, nao
    contornando-os."""
    _seed_full_coverage(db_session, "ANALYSIS_UPTREND")

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_UPTREND",
        primary_timeframe=Timeframe.M15,
        threshold=60.0,
        news_provider=_FakeNewsProvider(),
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    assert report.recommendation == "ENTER"
    assert report.score.total_score >= 60.0
    assert report.rejection_reasons == []
    assert report.trade_levels is not None
    assert report.confluences
    assert len(report.justification) == 7
    assert set(report.multi_timeframe_alignment.keys()) == set(ANALYSIS_TIMEFRAMES)

    levels = report.trade_levels
    if levels.risk_reward_1 > 0 and report.dominant_pattern is not None:
        # Marubozu de alta no final da serie -> viés de alta -> LONG.
        assert levels.stop_loss < levels.entry
        assert levels.take_profit_1 < levels.take_profit_2 < levels.take_profit_3


def test_choppy_market_produces_do_not_enter_with_reasons(db_session) -> None:
    candles = _make_choppy_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    _seed_timeframe(db_session, "ANALYSIS_CHOPPY", Timeframe.M15, candles)

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_CHOPPY",
        primary_timeframe=Timeframe.M15,
        threshold=90.0,
        now=_NOW,
    )

    assert report.recommendation == "DO_NOT_ENTER"
    assert report.rejection_reasons
    assert report.trade_levels is None


def test_only_lower_timeframes_collected_never_raises_and_shows_gap(db_session) -> None:
    candles_m1 = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    candles_m5 = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260 * 5), step=timedelta(minutes=5), seed=17
    )
    _seed_timeframe(db_session, "ANALYSIS_PARTIAL", Timeframe.M1, candles_m1)
    _seed_timeframe(db_session, "ANALYSIS_PARTIAL", Timeframe.M5, candles_m5)

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_PARTIAL",
        primary_timeframe=Timeframe.M15,  # nao coletado
        now=_NOW,
    )

    assert report.multi_timeframe_alignment[Timeframe.M1] != "SEM_DADOS"
    assert report.multi_timeframe_alignment[Timeframe.M5] != "SEM_DADOS"
    assert report.multi_timeframe_alignment[Timeframe.MN1] == "SEM_DADOS"
    assert report.multi_timeframe_alignment[Timeframe.M15] == "SEM_DADOS"

    structure_factor = next(f for f in report.score.factors if f.name == "structure")
    assert any(
        "Cobertura multi-timeframe incompleta" in line for line in structure_factor.rationale
    )
    assert report.recommendation == "DO_NOT_ENTER"
    assert any("Cobertura insuficiente" in reason for reason in report.rejection_reasons)
    assert report.trade_levels is None


def test_injected_fake_providers_are_reflected_in_score(db_session) -> None:
    candles = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    _seed_timeframe(db_session, "ANALYSIS_FAKE_PROVIDERS", Timeframe.M15, candles)

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_FAKE_PROVIDERS",
        primary_timeframe=Timeframe.M15,
        news_provider=_FakeNewsProvider(),
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
        enforce_gates=False,
    )

    news_factor = next(f for f in report.score.factors if f.name == "news")
    fundamentals_factor = next(f for f in report.score.factors if f.name == "fundamentals")
    assert news_factor.raw_score == 100.0
    assert fundamentals_factor.raw_score == 100.0


def test_custom_weights_are_used(db_session) -> None:
    candles = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    _seed_timeframe(db_session, "ANALYSIS_WEIGHTS", Timeframe.M15, candles)

    weights = ScoreWeights(
        structure=0.7,
        price_action=0.1,
        liquidity=0.1,
        volume=0.05,
        news=0.025,
        fundamentals=0.0,
        correlation=0.025,
    )
    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_WEIGHTS",
        primary_timeframe=Timeframe.M15,
        weights=weights,
        now=_NOW,
        enforce_gates=False,
    )

    structure_factor = next(f for f in report.score.factors if f.name == "structure")
    # Fundamentos tem peso zero e correlacao nunca tem dado; o peso util e
    # redistribuido, entao estrutura fica acima dos 0.7 nominais.
    assert structure_factor.weight > 0.7


def test_direction_is_long_for_bullish_latest_event(db_session) -> None:
    candles = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    _seed_timeframe(db_session, "ANALYSIS_DIRECTION", Timeframe.M15, candles)

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_DIRECTION",
        primary_timeframe=Timeframe.M15,
        threshold=50.0,
        news_provider=_FakeNewsProvider(),
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    if report.trade_levels is not None:
        # Serie de alta -- stop deve ficar abaixo da entrada (LONG).
        assert report.trade_levels.stop_loss < report.trade_levels.entry


def test_api_is_not_consulted_when_a_local_gate_already_blocks(db_session) -> None:
    """A economia que faltava: sem cobertura, a entrada ja esta decidida —
    pagar pela MarketPulse para descobrir isso depois era queimar cota."""
    candles = _make_uptrend_candles(
        260, start=_NOW - timedelta(minutes=260), step=timedelta(minutes=1)
    )
    _seed_timeframe(db_session, "ANALYSIS_NO_CALL", Timeframe.M15, candles)
    provider = _CountingNewsProvider()

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_NO_CALL",
        primary_timeframe=Timeframe.M15,
        news_provider=provider,
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    assert provider.calls == 0
    assert report.recommendation == "DO_NOT_ENTER"
    news_factor = next(f for f in report.score.factors if f.name == "news")
    assert news_factor.has_data is False
    assert "nao consultada" in news_factor.rationale[0]


def test_api_is_consulted_when_the_local_gates_pass(db_session) -> None:
    _seed_full_coverage(db_session, "ANALYSIS_CALL_OK")
    provider = _CountingNewsProvider()

    analyze_symbol(
        db_session,
        symbol="ANALYSIS_CALL_OK",
        primary_timeframe=Timeframe.M15,
        news_provider=provider,
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    assert provider.calls == 1


def test_a_factor_without_data_is_left_out_instead_of_scoring_fifty(db_session) -> None:
    """Correlacao nunca foi implementada. Antes ela valia 50 e derrubava o
    score total; agora sai da conta e o peso vai para quem tem dado."""
    _seed_full_coverage(db_session, "ANALYSIS_NO_DATA_FACTOR")

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_NO_DATA_FACTOR",
        primary_timeframe=Timeframe.M15,
        news_provider=_FakeNewsProvider(),
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    correlation = next(f for f in report.score.factors if f.name == "correlation")
    assert correlation.has_data is False
    assert correlation.weight == 0.0
    assert correlation.weighted_contribution == 0.0
    # O peso util continua somando 100%: nada se perde no caminho.
    assert sum(f.weight for f in report.score.factors) == pytest.approx(1.0)


def test_the_macro_context_does_not_require_sixteen_years_of_history(db_session) -> None:
    """MN1 com 200 candles seriam 16 anos. O portao exige presenca util,
    nao um historico que corretora nenhuma entrega."""
    _seed_full_coverage(db_session, "ANALYSIS_SHALLOW_MACRO")

    report = analyze_symbol(
        db_session,
        symbol="ANALYSIS_SHALLOW_MACRO",
        primary_timeframe=Timeframe.M15,
        news_provider=_FakeNewsProvider(),
        fundamentals_provider=_FakeFundamentalsProvider(),
        now=_NOW,
    )

    assert not any("Contexto macro ausente" in reason for reason in report.rejection_reasons)

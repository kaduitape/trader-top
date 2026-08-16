"""OpportunityHeatmapEngine.

O que estes testes protegem nao e "o score muda". E que o score mude pelos
motivos certos — e que o take pedido participe de verdade, que era a razao
declarada de o mapa existir.
"""

from __future__ import annotations

from app.foto_analise.heatmap import (
    HeatmapDetail,
    HeatmapInputs,
    OpportunityHeatmapEngine,
)
from app.market.regimes import Trend
from app.market.structure import SRLevel


def _suporte(price: float, touches: int = 3) -> SRLevel:
    return SRLevel(price=price, kind="SUPPORT", touches=touches, first_index=0, last_index=9)


def _resistencia(price: float, touches: int = 3) -> SRLevel:
    return SRLevel(price=price, kind="RESISTANCE", touches=touches, first_index=0, last_index=9)


def _entradas(**overrides) -> HeatmapInputs:
    base = {
        "current_price": 24600.0,
        "atr": 12.0,
        "trend": Trend.UP,
        "tick_size": 0.25,
        "take_ticks": 20,
    }
    base.update(overrides)
    return HeatmapInputs(**base)


def _faixa_mais_proxima(bands, price: float):
    return min(bands, key=lambda b: abs(b.price - price))


# --- o take muda o mapa ----------------------------------------------------


def test_a_resistance_before_the_target_kills_the_buy_score() -> None:
    """O ponto declarado do modulo: a mesma faixa vale coisas diferentes
    para takes diferentes. Sem isto, o mapa serviria a objetivos opostos."""
    entradas = _entradas(take_ticks=80, resistances=[_resistencia(24605)])
    perto = _faixa_mais_proxima(
        OpportunityHeatmapEngine().build(entradas), 24595
    )

    curto = _faixa_mais_proxima(
        OpportunityHeatmapEngine().build(
            _entradas(take_ticks=4, resistances=[_resistencia(24605)])
        ),
        24595,
    )

    assert perto.buy_score < curto.buy_score
    assert any("resistencia entre" in f for f in perto.factors)


def test_the_same_band_scores_differently_per_take() -> None:
    resistencias = [_resistencia(24640)]
    curto = OpportunityHeatmapEngine().build(_entradas(take_ticks=10, resistances=resistencias))
    longo = OpportunityHeatmapEngine().build(_entradas(take_ticks=200, resistances=resistencias))

    assert [b.buy_score for b in curto] != [b.buy_score for b in longo]


# --- confluencia -----------------------------------------------------------


def test_support_raises_buy_and_lowers_sell() -> None:
    bands = OpportunityHeatmapEngine().build(_entradas(supports=[_suporte(24590)]))
    faixa = _faixa_mais_proxima(bands, 24590)

    assert faixa.buy_score > faixa.sell_score
    assert any("suporte" in f for f in faixa.factors)


def test_scores_are_not_complementary() -> None:
    """Forcar `sell = 100 - buy` inventaria vantagem vendedora onde ha
    apenas ausencia de vantagem compradora."""
    bands = OpportunityHeatmapEngine().build(
        _entradas(supports=[_suporte(24590)], resistances=[_resistencia(24590)])
    )

    assert any(abs(b.buy_score + b.sell_score - 100.0) > 1.0 for b in bands)


def test_every_score_stays_in_range() -> None:
    bands = OpportunityHeatmapEngine().build(
        _entradas(
            supports=[_suporte(24590, touches=99)],
            resistances=[_resistencia(24591, touches=99)],
            vwap=24590.5,
            emas={9: 24590.0, 21: 24590.2, 50: 24590.4, 200: 24590.6},
        )
    )

    assert all(0.0 <= b.buy_score <= 100.0 for b in bands)
    assert all(0.0 <= b.sell_score <= 100.0 for b in bands)


def test_missing_data_scores_neutral_not_zero() -> None:
    """Zero seria uma opiniao negativa sobre algo que ninguem mediu."""
    bands = OpportunityHeatmapEngine().build(_entradas(trend=Trend.SIDEWAYS))

    assert all(b.buy_score > 0 for b in bands)
    assert all(b.sell_score > 0 for b in bands)


def test_a_mitigated_order_block_is_ignored() -> None:
    """O fluxo que criou o bloco ja foi consumido — ele nao e mais zona."""
    from datetime import UTC, datetime

    from app.market.price_action import PatternDirection
    from app.market.smc import OrderBlock

    def bloco(mitigated: bool) -> OrderBlock:
        return OrderBlock(
            index=5,
            open_time=datetime(2026, 1, 1, tzinfo=UTC),
            direction=PatternDirection.BULLISH,
            high=24592.0,
            low=24588.0,
            mitigated=mitigated,
            mitigated_at_index=7 if mitigated else None,
            is_breaker=False,
        )

    ativo = _faixa_mais_proxima(
        OpportunityHeatmapEngine().build(_entradas(order_blocks=[bloco(False)])), 24590
    )
    consumido = _faixa_mais_proxima(
        OpportunityHeatmapEngine().build(_entradas(order_blocks=[bloco(True)])), 24590
    )

    assert ativo.buy_score > consumido.buy_score


# --- geometria -------------------------------------------------------------


def test_detail_controls_granularity_only() -> None:
    entradas = _entradas(supports=[_suporte(24590)])
    simples = OpportunityHeatmapEngine(detail=HeatmapDetail.SIMPLIFICADO).build(entradas)
    avancado = OpportunityHeatmapEngine(detail=HeatmapDetail.AVANCADO).build(entradas)

    assert len(simples) < len(avancado)
    # Mesma cobertura de preco: menos faixas e menos ruido, nao menos mapa.
    assert abs(min(b.price for b in simples) - min(b.price for b in avancado)) < 1.0


def test_prices_land_on_real_ticks() -> None:
    """Preco que nao existe na corretora nao serve de nivel: ninguem
    consegue colocar ordem em 24583.7143."""
    bands = OpportunityHeatmapEngine().build(_entradas(tick_size=0.25))

    for faixa in bands:
        assert abs((faixa.price / 0.25) - round(faixa.price / 0.25)) < 1e-6


def test_the_range_follows_volatility() -> None:
    """Largura fixa seria grosseira num ativo volatil e cega num parado."""
    calmo = OpportunityHeatmapEngine().build(_entradas(atr=2.0))
    agitado = OpportunityHeatmapEngine().build(_entradas(atr=60.0))

    largura = lambda bands: max(b.price for b in bands) - min(b.price for b in bands)  # noqa: E731

    assert largura(agitado) > largura(calmo)


def test_far_bands_are_penalised() -> None:
    """Plano distante demais raramente vira ordem."""
    bands = OpportunityHeatmapEngine(detail=HeatmapDetail.AVANCADO).build(_entradas())
    perto = _faixa_mais_proxima(bands, 24600)
    longe = min(bands, key=lambda b: b.price)

    assert perto.buy_score > longe.buy_score

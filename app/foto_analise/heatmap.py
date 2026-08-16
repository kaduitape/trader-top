"""OpportunityHeatmapEngine — confluencia projetada no eixo de preco.

## A pergunta que ele responde

`AnalysisReport` responde "o que esta acontecendo". Ele nao responde "em
QUE PRECO isso vale". Suporte a 24580 e VWAP a 24592 sao fatos do
relatorio; que a faixa 24582-24586 concentra os dois e conclusao
geometrica, e e ela que o operador precisa ver.

Este motor faz so essa projecao. Cada fator abaixo vem PRONTO da analise
existente — nenhum indicador e recalculado aqui.

## Por que buy e sell sao scores separados

Nao sao complementares. Uma faixa encostada numa resistencia forte e ruim
para comprar E ruim para vender (comprar contra o nivel, vender sem
confirmacao). Forcar `sell = 100 - buy` inventaria uma vantagem vendedora
que nenhum dado sustenta.

## Por que o take entra no score

Uma faixa boa para 10 ticks pode ser ruim para 50: se ha resistencia no
meio do caminho, o alvo nao e alcancavel dali. Ignorar isso faria o mesmo
mapa servir para objetivos opostos — que e precisamente o erro que este
modulo existe para evitar.

## Vocabulario

O score e CONFLUENCIA e qualidade relativa da faixa. Nao e probabilidade
de lucro, e nada aqui deve ser apresentado como tal: nao ha estatistica
historica neste projeto que sustente essa leitura.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from app.market.price_action import PatternDirection
from app.market.regimes import Trend
from app.market.smc import LiquiditySweep, OrderBlock, PremiumDiscountZone
from app.market.structure import SRLevel

NEUTRAL = 50.0
"""Ausencia de informacao vale neutro, nunca zero. Zero seria uma opiniao
negativa sobre algo que ninguem mediu."""


class HeatmapDetail(enum.StrEnum):
    """Granularidade do mapa. Menos faixas nao e menos analise: e menos
    ruido para quem so quer saber onde estao as melhores regioes."""

    SIMPLIFICADO = "SIMPLIFICADO"
    NORMAL = "NORMAL"
    AVANCADO = "AVANCADO"


_BANDS_BY_DETAIL: dict[HeatmapDetail, int] = {
    HeatmapDetail.SIMPLIFICADO: 9,
    HeatmapDetail.NORMAL: 17,
    HeatmapDetail.AVANCADO: 33,
}


@dataclass(frozen=True, slots=True)
class HeatmapBand:
    """Uma faixa de preco e por que ela pontuou o que pontuou.

    `factors` existe para que nenhum numero apareca na tela sem origem
    rastreavel — score sem explicacao vira superticao.
    """

    price: float
    buy_score: float
    sell_score: float
    factors: list[str] = field(default_factory=list)

    @property
    def best_side(self) -> str:
        if abs(self.buy_score - self.sell_score) < 5.0:
            return "NEUTRO"
        return "BUY" if self.buy_score > self.sell_score else "SELL"


@dataclass(frozen=True, slots=True)
class HeatmapInputs:
    """Tudo que o motor consome — e tudo ja vem calculado.

    O tipo existe para tornar essa dependencia explicita: se um dia alguem
    precisar de um fator novo, tera que busca-lo em quem ja o calcula, em
    vez de computa-lo aqui em silencio.
    """

    current_price: float
    atr: float
    trend: Trend
    tick_size: float
    take_ticks: int
    supports: list[SRLevel] = field(default_factory=list)
    resistances: list[SRLevel] = field(default_factory=list)
    vwap: float | None = None
    emas: dict[int, float] = field(default_factory=dict)
    order_blocks: list[OrderBlock] = field(default_factory=list)
    sweeps: list[LiquiditySweep] = field(default_factory=list)
    premium_discount: PremiumDiscountZone | None = None


class OpportunityHeatmapEngine:
    """Projeta as conclusoes da analise no eixo de preco."""

    def __init__(self, *, detail: HeatmapDetail = HeatmapDetail.NORMAL) -> None:
        self._detail = detail

    # --- geometria --------------------------------------------------------

    def _band_prices(self, inputs: HeatmapInputs) -> list[float]:
        """Faixas cobrindo o alcance plausivel do movimento.

        O alcance vem do ATR e do take pedido — nao de um numero fixo. Um
        mapa de largura fixa seria grosseiro num ativo volatil e cego num
        parado, e nos dois casos mostraria regioes que o preco nao alcanca
        no horizonte da operacao.
        """
        take_distance = inputs.take_ticks * inputs.tick_size
        alcance = max(inputs.atr * 2.0, take_distance * 2.0)
        if alcance <= 0:
            return [inputs.current_price]

        quantidade = _BANDS_BY_DETAIL[self._detail]
        passo = (alcance * 2) / (quantidade - 1)
        inicio = inputs.current_price - alcance
        return [_round_to_tick(inicio + passo * i, inputs.tick_size) for i in range(quantidade)]

    # --- pontuacao --------------------------------------------------------

    def _score_band(self, price: float, inputs: HeatmapInputs) -> HeatmapBand:
        buy = NEUTRAL
        sell = NEUTRAL
        fatores: list[str] = []
        tolerancia = max(inputs.atr * 0.25, inputs.tick_size * 2)

        # --- suporte/resistencia -----------------------------------------
        for nivel in inputs.supports:
            if abs(price - nivel.price) <= tolerancia:
                peso = min(15.0, 5.0 + nivel.touches * 2.5)
                buy += peso
                sell -= peso * 0.6
                fatores.append(f"suporte {nivel.price:.5g} ({nivel.touches} toques)")
                break

        for nivel in inputs.resistances:
            if abs(price - nivel.price) <= tolerancia:
                peso = min(15.0, 5.0 + nivel.touches * 2.5)
                sell += peso
                buy -= peso * 0.6
                fatores.append(f"resistencia {nivel.price:.5g} ({nivel.touches} toques)")
                break

        # --- VWAP e EMAs --------------------------------------------------
        if inputs.vwap is not None and abs(price - inputs.vwap) <= tolerancia:
            buy += 8.0
            sell += 8.0
            fatores.append("VWAP proxima")

        for periodo, valor in sorted(inputs.emas.items()):
            if abs(price - valor) <= tolerancia:
                peso = 4.0 if periodo <= 21 else 7.0
                if inputs.trend == Trend.UP:
                    buy += peso
                elif inputs.trend == Trend.DOWN:
                    sell += peso
                fatores.append(f"EMA {periodo}")
                break

        # --- tendencia ----------------------------------------------------
        if inputs.trend == Trend.UP:
            buy += 10.0
            sell -= 8.0
        elif inputs.trend == Trend.DOWN:
            sell += 10.0
            buy -= 8.0

        # --- desconto/premio (SMC) ----------------------------------------
        zona = inputs.premium_discount
        if zona is not None:
            if price <= zona.equilibrium:
                buy += 6.0
                fatores.append("regiao de desconto")
            else:
                sell += 6.0
                fatores.append("regiao de premio")

        # --- order blocks e liquidez --------------------------------------
        for bloco in inputs.order_blocks:
            # Bloco ja mitigado nao e mais zona de interesse: o fluxo que o
            # criou ja foi consumido.
            if bloco.mitigated or not bloco.low <= price <= bloco.high:
                continue
            if bloco.direction == PatternDirection.BULLISH:
                buy += 9.0
            elif bloco.direction == PatternDirection.BEARISH:
                sell += 9.0
            fatores.append("order block")
            break

        for sweep in inputs.sweeps:
            if abs(price - sweep.swept_price) > tolerancia:
                continue
            # A direcao da varredura e o que importa: liquidez tomada
            # ABAIXO favorece compra, tomada acima favorece venda.
            if sweep.direction == PatternDirection.BULLISH:
                buy += 5.0
            elif sweep.direction == PatternDirection.BEARISH:
                sell += 5.0
            fatores.append("liquidez capturada")
            break

        # --- o take pedido ------------------------------------------------
        #
        # Aqui e onde o mesmo mapa deixa de servir para objetivos
        # diferentes: se ha resistencia entre esta faixa e o alvo de
        # compra, o alvo nao e alcancavel a partir daqui.
        take_distance = inputs.take_ticks * inputs.tick_size
        alvo_compra = price + take_distance
        alvo_venda = price - take_distance

        if any(price < n.price < alvo_compra for n in inputs.resistances):
            buy -= 22.0
            fatores.append("resistencia entre a entrada e o take")
        if any(alvo_venda < n.price < price for n in inputs.supports):
            sell -= 22.0
            fatores.append("suporte entre a entrada e o take")

        # Distancia ate a faixa: entrar longe do preco atual e um plano,
        # nao uma ordem — e planos distantes demais raramente acontecem.
        distancia = abs(price - inputs.current_price)
        if distancia > take_distance * 2:
            penalidade = 12.0
            buy -= penalidade
            sell -= penalidade

        return HeatmapBand(
            price=price,
            buy_score=_clip(buy),
            sell_score=_clip(sell),
            factors=fatores,
        )

    def build(self, inputs: HeatmapInputs) -> list[HeatmapBand]:
        """O mapa completo, do preco mais baixo ao mais alto."""
        return [self._score_band(preco, inputs) for preco in self._band_prices(inputs)]


def _clip(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _round_to_tick(price: float, tick_size: float) -> float:
    """Preco que nao existe na corretora nao serve de nivel.

    Sem isto o mapa mostraria 24583.7143 num ativo que so negocia de 0.25
    em 0.25 — e o operador nao teria como colocar ordem ali.
    """
    if tick_size <= 0:
        return round(price, 5)
    return round(round(price / tick_size) * tick_size, 8)

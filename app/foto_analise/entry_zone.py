"""EntryZoneEngine — de um mapa de faixas para UMA regiao de entrada.

## Zona, nao preco

O motor devolve uma faixa e, dentro dela, um sweet spot. A ordem importa:
a faixa e o produto, o sweet spot e uma referencia dentro dela.

Tratar um preco unico como resposta seria falso na pratica — ninguem
executa no centesimo — e perigoso na leitura, porque transmite uma
precisao que a analise nao tem. Quem persegue um preco exato perde
entradas boas por um tick.

## "Entrar agora" e uma pergunta separada

Saber ONDE entrar nao responde SE entrar agora. Preco atual longe da zona
otima significa aguardar pullback, e isso precisa aparecer como estado
proprio (`WAIT_PULLBACK`) — nao como uma entrada ruim disfarcada de boa.

## Sem oportunidade tambem e resposta

Quando nenhuma faixa passa do minimo, o motor devolve a melhor faixa
mesmo assim, marcada como insuficiente. Dizer "compraria em X se
voltasse" e util; dizer "nada" nao ajuda ninguem a esperar a coisa certa.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from app.foto_analise.heatmap import HeatmapBand
from app.strategies.base import SignalDirection

MIN_ZONE_SCORE = 62.0
"""Abaixo disto a faixa nao e apresentada como oportunidade.

Nao e um numero otimizado — nao ha backtest aqui que justificasse
otimiza-lo. E um corte conservador: score neutro e 50, entao exigir 62
significa "as confluencias precisam somar algo real", nada mais."""


class EntryStatus(enum.StrEnum):
    READY = "READY"
    """Preco atual dentro da zona: a entrada e agora."""

    WAIT_PULLBACK = "WAIT_PULLBACK"
    """A zona esta abaixo do preco (compra) ou acima (venda)."""

    MISSED = "MISSED"
    """O preco ja passou pela zona e seguiu — perseguir aqui e o erro
    classico de entrar tarde no movimento."""

    NO_SETUP = "NO_SETUP"
    """Nenhuma faixa atinge o minimo."""


@dataclass(frozen=True, slots=True)
class EntryZone:
    min: float
    sweet_spot: float
    max: float
    score: float
    status: EntryStatus
    distance_ticks: int
    """Quanto o preco precisa andar ate a zona. Zero quando ja esta nela."""

    @property
    def is_actionable(self) -> bool:
        return self.status == EntryStatus.READY


class EntryZoneEngine:
    """Escolhe a regiao de entrada a partir do mapa ja calculado."""

    def __init__(self, *, min_score: float = MIN_ZONE_SCORE) -> None:
        self._min_score = min_score

    def build(
        self,
        bands: list[HeatmapBand],
        *,
        direction: SignalDirection,
        current_price: float,
        tick_size: float,
    ) -> EntryZone | None:
        if not bands:
            return None

        comprando = direction == SignalDirection.LONG
        pontuacao = (lambda b: b.buy_score) if comprando else (lambda b: b.sell_score)

        melhor = max(bands, key=pontuacao)
        vizinhas = self._contiguous_around(bands, melhor, pontuacao)

        limite_baixo = min(b.price for b in vizinhas)
        limite_alto = max(b.price for b in vizinhas)
        score = pontuacao(melhor)

        if limite_baixo == limite_alto:
            # Uma faixa isolada ainda representa uma REGIAO: ela cobre meia
            # distancia ate cada vizinha. Devolver `min == max` daria ao
            # operador um preco unico disfarcado de zona — exatamente o que
            # este motor existe para evitar.
            metade = self._half_step(bands) or tick_size
            limite_baixo -= metade
            limite_alto += metade

        if score < self._min_score:
            status = EntryStatus.NO_SETUP
        else:
            status = self._status(
                comprando=comprando,
                current_price=current_price,
                low=limite_baixo,
                high=limite_alto,
            )

        distancia = 0.0
        if current_price < limite_baixo:
            distancia = limite_baixo - current_price
        elif current_price > limite_alto:
            distancia = current_price - limite_alto

        return EntryZone(
            min=limite_baixo,
            sweet_spot=melhor.price,
            max=limite_alto,
            score=score,
            status=status,
            distance_ticks=int(round(distancia / tick_size)) if tick_size > 0 else 0,
        )

    # --- internos ---------------------------------------------------------

    def _half_step(self, bands: list[HeatmapBand]) -> float:
        """Metade do espacamento entre faixas — a largura que uma faixa
        isolada representa de fato."""
        if len(bands) < 2:
            return 0.0
        precos = sorted(b.price for b in bands)
        return (precos[1] - precos[0]) / 2

    def _contiguous_around(self, bands, melhor, pontuacao) -> list[HeatmapBand]:
        """Faixas vizinhas com score comparavel.

        A zona nasce da continuidade: uma unica faixa forte cercada de
        faixas fracas e mais provavelmente ruido do que uma regiao. Exigir
        vizinhanca torna a zona algo que o preco pode de fato percorrer.
        """
        ordenadas = sorted(bands, key=lambda b: b.price)
        indice = ordenadas.index(melhor)
        corte = pontuacao(melhor) - 8.0

        inicio = indice
        while inicio > 0 and pontuacao(ordenadas[inicio - 1]) >= corte:
            inicio -= 1

        fim = indice
        while fim < len(ordenadas) - 1 and pontuacao(ordenadas[fim + 1]) >= corte:
            fim += 1

        return ordenadas[inicio : fim + 1]

    def _status(
        self, *, comprando: bool, current_price: float, low: float, high: float
    ) -> EntryStatus:
        if low <= current_price <= high:
            return EntryStatus.READY
        if comprando:
            # Zona abaixo do preco: espera-se o recuo ate ela. Zona ACIMA
            # significa que o preco ja saiu de la para cima — comprar agora
            # seria perseguir.
            return EntryStatus.WAIT_PULLBACK if current_price > high else EntryStatus.MISSED
        return EntryStatus.WAIT_PULLBACK if current_price < low else EntryStatus.MISSED

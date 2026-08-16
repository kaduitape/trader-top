"""ChartAnnotationService — o desenho.

## Por que SVG gerado no servidor

O painel nao tem biblioteca de grafico e nao ha CDN disponivel (o CSP e as
regras do projeto proibem). SVG inline resolve sem dependencia nova, sem
canvas e sem uma segunda copia da logica de posicionamento em JavaScript:
quem calcula a zona de entrada e quem a desenha compartilham a mesma
escala, no mesmo lugar.

## O que o desenho promete

Os candles sao REAIS. As zonas vem dos motores. A seta de projecao e a
unica coisa ilustrativa da tela — e por isso ela e desenhada tracejada e
rotulada como projecao, nunca como previsao.

## Regra de leitura

A tela precisa responder em 2-3 segundos: tendencia, lado, entrar ou
esperar, melhor regiao, sweet spot, take, invalidacao. Cada elemento aqui
existe para uma dessas perguntas; nada e desenhado por enfeite.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from app.foto_analise.entry_zone import EntryStatus
from app.foto_analise.service import FotoAnalise

WIDTH = 960
HEIGHT = 520
PADDING_LEFT = 12
PADDING_RIGHT = 88
"""Espaco a direita para os rotulos de preco — sem ele os textos saem
cortados na borda, que e o defeito classico de grafico gerado a mao."""
PADDING_TOP = 34
PADDING_BOTTOM = 26


@dataclass(frozen=True, slots=True)
class _Scale:
    """Converte preco em coordenada de tela.

    Guardado como objeto para que zona, niveis e candles usem exatamente a
    mesma escala. Duas escalas ligeiramente diferentes produziriam um
    desenho onde a linha do take nao encosta no candle que a atingiu.
    """

    low: float
    high: float

    def y(self, price: float) -> float:
        if self.high <= self.low:
            return HEIGHT / 2
        proporcao = (price - self.low) / (self.high - self.low)
        util = HEIGHT - PADDING_TOP - PADDING_BOTTOM
        return PADDING_TOP + (1 - proporcao) * util


class ChartAnnotationService:
    """Monta o SVG a partir de um `FotoAnalise` ja pronto."""

    def render(self, foto: FotoAnalise, *, show_heatmap: bool = True) -> str:
        if not foto.candles:
            return (
                '<div class="text-muted text-center py-5">'
                "Sem candles para desenhar neste timeframe.</div>"
            )

        escala = self._scale(foto)
        partes: list[str] = [
            f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" width="100%" '
            f'role="img" aria-label="Foto Analise de {escape(foto.symbol)}" '
            'style="max-width:100%;height:auto;font-family:inherit">'
        ]

        if show_heatmap:
            partes.append(self._heatmap(foto, escala))
        partes.append(self._zones(foto, escala))
        partes.append(self._candles(foto, escala))
        partes.append(self._levels(foto, escala))
        partes.append(self._projection(foto, escala))
        partes.append(self._header(foto))
        partes.append("</svg>")
        return "".join(partes)

    # --- escala -----------------------------------------------------------

    def _scale(self, foto: FotoAnalise) -> _Scale:
        """Inclui candles E anotacoes no enquadramento.

        Enquadrar so pelos candles esconderia justamente o take ou o stop
        quando eles ficam fora do range recente — e sao esses dois que
        respondem "vale a pena?".
        """
        precos = [c.high for c in foto.candles] + [c.low for c in foto.candles]
        for valor in (foto.stop, foto.take, foto.decision_level):
            if valor is not None:
                precos.append(valor)
        if foto.entry_zone is not None:
            precos.extend([foto.entry_zone.min, foto.entry_zone.max])
        if foto.heatmap:
            precos.extend([b.price for b in foto.heatmap])

        baixo, alto = min(precos), max(precos)
        folga = (alto - baixo) * 0.06 or max(foto.tick_size, 1e-8)
        return _Scale(low=baixo - folga, high=alto + folga)

    # --- camadas ----------------------------------------------------------

    def _heatmap(self, foto: FotoAnalise, escala: _Scale) -> str:
        if len(foto.heatmap) < 2:
            return ""

        comprando = foto.bias == "LONG"
        faixas = sorted(foto.heatmap, key=lambda b: b.price)
        altura = abs(escala.y(faixas[0].price) - escala.y(faixas[1].price))

        saida: list[str] = ['<g class="heat">']
        for faixa in faixas:
            score = faixa.buy_score if comprando else faixa.sell_score
            cor, opacidade = _heat_color(score)
            y = escala.y(faixa.price) - altura / 2
            saida.append(
                f'<rect x="{PADDING_LEFT}" y="{y:.1f}" '
                f'width="{WIDTH - PADDING_LEFT - PADDING_RIGHT}" '
                f'height="{altura:.1f}" fill="{cor}" opacity="{opacidade}"/>'
            )
        saida.append("</g>")
        return "".join(saida)

    def _zones(self, foto: FotoAnalise, escala: _Scale) -> str:
        zona = foto.entry_zone
        if zona is None:
            return ""

        comprando = foto.bias == "LONG"
        cor = "#22c55e" if comprando else "#ef4444"
        rotulo = "BUY ZONE" if comprando else "SELL ZONE"
        topo = escala.y(zona.max)
        base = escala.y(zona.min)
        largura = WIDTH - PADDING_LEFT - PADDING_RIGHT

        return (
            f'<rect x="{PADDING_LEFT}" y="{topo:.1f}" width="{largura}" '
            f'height="{max(abs(base - topo), 3):.1f}" fill="{cor}" opacity="0.20" '
            f'stroke="{cor}" stroke-opacity="0.65"/>'
            f'<text x="{PADDING_LEFT + 8}" y="{topo - 5:.1f}" fill="{cor}" '
            f'font-size="12" font-weight="700">{rotulo} '
            f"{_fmt(zona.min, foto.tick_size)}–{_fmt(zona.max, foto.tick_size)}</text>"
            + self._star(foto, escala, zona.sweet_spot, cor)
        )

    def _star(self, foto: FotoAnalise, escala: _Scale, price: float, cor: str) -> str:
        y = escala.y(price)
        x = WIDTH - PADDING_RIGHT - 8
        return (
            f'<line x1="{PADDING_LEFT}" y1="{y:.1f}" x2="{x}" y2="{y:.1f}" '
            f'stroke="{cor}" stroke-width="2" stroke-dasharray="1 3"/>'
            f'<text x="{PADDING_LEFT + 8}" y="{y - 6:.1f}" fill="{cor}" '
            f'font-size="12" font-weight="700">★ MELHOR ENTRADA '
            f'{_fmt(price, foto.tick_size)}</text>'
        )

    def _candles(self, foto: FotoAnalise, escala: _Scale) -> str:
        total = len(foto.candles)
        util = WIDTH - PADDING_LEFT - PADDING_RIGHT
        passo = util / total
        corpo = max(passo * 0.62, 1.0)

        saida: list[str] = ['<g class="candles">']
        for indice, vela in enumerate(foto.candles):
            centro = PADDING_LEFT + passo * (indice + 0.5)
            alta = vela.close >= vela.open
            cor = "#26a69a" if alta else "#ef5350"

            saida.append(
                f'<line x1="{centro:.1f}" y1="{escala.y(vela.high):.1f}" '
                f'x2="{centro:.1f}" y2="{escala.y(vela.low):.1f}" '
                f'stroke="{cor}" stroke-width="1"/>'
            )
            topo = escala.y(max(vela.open, vela.close))
            base = escala.y(min(vela.open, vela.close))
            saida.append(
                f'<rect x="{centro - corpo / 2:.1f}" y="{topo:.1f}" '
                f'width="{corpo:.1f}" height="{max(base - topo, 1):.1f}" fill="{cor}"/>'
            )
        saida.append("</g>")
        return "".join(saida)

    def _levels(self, foto: FotoAnalise, escala: _Scale) -> str:
        estilos = {
            "TAKE": ("#22c55e", f"TAKE +{foto.take_ticks} TICKS"),
            "STOP": ("#ef4444", "STOP / INVALIDACAO"),
            "DECISION": ("#a78bfa", "⚡ DECISION LEVEL"),
            "RESISTANCE": ("#f97316", "Resistencia"),
            "SUPPORT": ("#38bdf8", "Suporte"),
        }
        saida: list[str] = []
        for nivel in foto.levels:
            if nivel.kind not in estilos:
                continue
            cor, texto = estilos[nivel.kind]
            y = escala.y(nivel.price)
            saida.append(
                f'<line x1="{PADDING_LEFT}" y1="{y:.1f}" '
                f'x2="{WIDTH - PADDING_RIGHT}" y2="{y:.1f}" stroke="{cor}" '
                f'stroke-width="1.5" stroke-dasharray="6 4"/>'
                f'<text x="{WIDTH - PADDING_RIGHT + 6}" y="{y + 4:.1f}" fill="{cor}" '
                f'font-size="11">{_fmt(nivel.price, foto.tick_size)}</text>'
                f'<text x="{WIDTH - PADDING_RIGHT - 6}" y="{y - 5:.1f}" fill="{cor}" '
                f'font-size="10" text-anchor="end" opacity="0.9">{escape(texto)}</text>'
            )
        return "".join(saida)

    def _projection(self, foto: FotoAnalise, escala: _Scale) -> str:
        """A seta do cenario esperado — tracejada porque e projecao.

        Solida, ela leria como afirmacao sobre o futuro. O rotulo repete a
        palavra "projecao" pelo mesmo motivo.
        """
        zona = foto.entry_zone
        if zona is None or foto.take is None:
            return ""

        x0 = WIDTH - PADDING_RIGHT - 210
        x1 = WIDTH - PADDING_RIGHT - 40
        y_atual = escala.y(foto.current_price)
        y_zona = escala.y(zona.sweet_spot)
        y_take = escala.y(foto.take)
        meio = (x0 + x1) / 2

        return (
            '<defs><marker id="seta" markerWidth="9" markerHeight="9" refX="6" '
            'refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#eab308"/>'
            "</marker></defs>"
            f'<path d="M {x0} {y_atual:.1f} L {meio:.1f} {y_zona:.1f} '
            f'L {x1} {y_take:.1f}" fill="none" stroke="#eab308" stroke-width="2" '
            'stroke-dasharray="7 5" marker-end="url(#seta)" opacity="0.9"/>'
            f'<text x="{x0}" y="{HEIGHT - 8}" fill="#eab308" font-size="10" '
            'opacity="0.85">projecao do cenario, nao previsao</text>'
        )

    def _header(self, foto: FotoAnalise) -> str:
        comprando = foto.bias == "LONG"
        cor = "#22c55e" if comprando else "#ef4444"
        rotulo = "COMPRA" if comprando else "VENDA"

        estado = {
            EntryStatus.READY.value: "ENTRADA AGORA",
            EntryStatus.WAIT_PULLBACK.value: "AGUARDAR PULLBACK",
            EntryStatus.MISSED.value: "PRECO JA PASSOU",
            EntryStatus.NO_SETUP.value: "SEM ENTRADA BOA AGORA",
        }.get(foto.status, foto.status)

        return (
            f'<text x="{PADDING_LEFT}" y="20" fill="{cor}" font-size="17" '
            f'font-weight="800">{rotulo}</text>'
            f'<text x="{PADDING_LEFT + 92}" y="20" fill="#cbd5e1" font-size="13">'
            f"Forca do setup: {foto.score:.0f}/100</text>"
            f'<text x="{WIDTH - PADDING_RIGHT}" y="20" fill="#e2e8f0" font-size="13" '
            f'font-weight="700" text-anchor="end">{escape(estado)}</text>'
        )


def _heat_color(score: float) -> tuple[str, float]:
    """Escala verde-forte → vermelho-forte, como especificado.

    Opacidade cresce com a intensidade para que as regioes neutras nao
    competam visualmente com as decisivas — o olho deve cair primeiro no
    verde forte.
    """
    if score >= 80:
        return "#22c55e", 0.42
    if score >= 65:
        return "#4ade80", 0.26
    if score >= 45:
        return "#eab308", 0.16
    if score >= 30:
        return "#f97316", 0.20
    return "#ef4444", 0.30


def _fmt(price: float, tick_size: float) -> str:
    """Casas decimais vindas do tick real, nunca fixas.

    Duas casas transformariam 1.10345 em 1.10 — e o operador de forex
    perderia exatamente a informacao que importa.
    """
    if tick_size <= 0:
        return f"{price:.5f}".rstrip("0").rstrip(".")
    texto = f"{tick_size:.10f}".rstrip("0")
    casas = len(texto.split(".")[1]) if "." in texto else 0
    return f"{price:.{min(casas, 8)}f}"

"""FotoAnalise — camada VISUAL de decisao sobre a analise que ja existe.

## O que este pacote nao faz

Nao detecta estrutura, nao calcula indicador, nao classifica tendencia,
nao decide entrada. Tudo isso ja existe e continua sendo feito por
`app.services.analysis_service.analyze_symbol`, que por sua vez compoe
`app.market.*`. Recalcular qualquer uma dessas coisas aqui criaria uma
segunda verdade sobre o mesmo mercado — e duas verdades divergem no pior
momento, que e quando alguem precisa confiar na tela.

## O que ele faz

Pega o `AnalysisReport` pronto e responde uma pergunta que o relatorio nao
responde: **ONDE** no eixo de preco cada conclusao se aplica.

O relatorio diz "tendencia de alta, suporte proximo, VWAP abaixo". O
FotoAnalise projeta isso em faixas de preco e diz: nesta faixa aqui as
confluencias se somam, naquela ali elas se anulam. E dai saem a zona de
entrada, o sweet spot e o mapa de calor.

## A regra do Take

O mapa muda conforme o take pedido, e isso nao e detalhe: uma entrada
otima para 10 ticks pode ser ruim para 50 se houver resistencia no meio do
caminho. O `take_ticks` entra no score de cada faixa, nunca so no desenho.

## Vocabulario

Score de faixa e **confluencia**, nao probabilidade de lucro. Nenhum texto
deste pacote afirma chance de ganho: nao existe backtest que sustente essa
afirmacao aqui, e inventa-la seria o tipo de numero que faz alguem
arriscar dinheiro por um motivo falso.
"""

from app.foto_analise.entry_zone import EntryZone, EntryZoneEngine
from app.foto_analise.heatmap import HeatmapBand, HeatmapDetail, OpportunityHeatmapEngine
from app.foto_analise.service import FotoAnalise, FotoAnaliseService

__all__ = [
    "EntryZone",
    "EntryZoneEngine",
    "FotoAnalise",
    "FotoAnaliseService",
    "HeatmapBand",
    "HeatmapDetail",
    "OpportunityHeatmapEngine",
]

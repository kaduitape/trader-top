"""ApexFlow AI — motor de decisao por fluxo de ticks e price action.

Nao e mais um robo de RSI/MACD/cruzamento de medias. A decisao nasce do
COMPORTAMENTO do mercado em tempo real — fluxo de ticks, microestrutura,
price action, contexto multi-timeframe — e os indicadores classicos entram
apenas como contexto, nunca como gatilho.

Desenho em modulos independentes, cada um puro (sem banco, sem MetaTrader,
sem envio de ordem) para permanecer testavel e barato de rodar:

| Modulo             | Responsabilidade                                        |
|--------------------|---------------------------------------------------------|
| `tick_flow`        | Buffer circular de ticks e metricas de fluxo             |
| `context`          | Regime de mercado estendido (9 estados)                  |
| `mtf`              | Papeis por timeframe (H1 macro ... tick execucao)        |
| `momentum`         | Aceleracao, forca, exaustao, persistencia                |
| `volatility`       | ATR, volatilidade de segundos, expansao/contracao        |
| `spread`           | Tres vetos de spread                                     |
| `liquidity`        | Sweep, caca de stops, falso rompimento, retorno a faixa  |
| `features`         | Feature vector nomeado e versionado                      |
| `decision`         | COMPRAR / VENDER / NAO OPERAR com probabilidade          |
| `risk`             | Alvo dinamico, trailing, break-even, limites             |
| `journal`          | Learning Engine: registra toda decisao para reavaliacao  |

A saida do motor tem exatamente tres possibilidades — COMPRAR, VENDER ou
NAO OPERAR — e a abstencao e a resposta padrao: uma entrada so acontece
quando a confianca supera o limite configurado E nenhum veto duro dispara.
Nunca se forca uma entrada para "aproveitar" um ciclo.
"""

from app.apexflow.config import ApexFlowConfig, load_apexflow_config, save_apexflow_config
from app.apexflow.context import MarketContext, MarketContextState, classify_market_context
from app.apexflow.decision import (
    ApexFlowDecision,
    DecisionAction,
    decide,
)
from app.apexflow.features import FEATURE_VERSION, FeatureVector, build_feature_vector
from app.apexflow.momentum import MomentumReading, read_momentum
from app.apexflow.spread import SpreadReading, read_spread
from app.apexflow.tick_flow import TickBuffer, TickFlowMetrics, compute_tick_flow
from app.apexflow.volatility import VolatilityReading, read_volatility

__all__ = [
    "FEATURE_VERSION",
    "ApexFlowConfig",
    "ApexFlowDecision",
    "DecisionAction",
    "FeatureVector",
    "MarketContext",
    "MarketContextState",
    "MomentumReading",
    "SpreadReading",
    "TickBuffer",
    "TickFlowMetrics",
    "VolatilityReading",
    "build_feature_vector",
    "classify_market_context",
    "compute_tick_flow",
    "decide",
    "load_apexflow_config",
    "read_momentum",
    "read_spread",
    "read_volatility",
    "save_apexflow_config",
]

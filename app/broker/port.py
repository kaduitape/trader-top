"""Porta de corretora: o contrato que o motor de execucao enxerga.

Ate aqui o sistema falava MetaTrader diretamente — `send_market_order`,
`modify_position`, `positions_get`. Isso funcionava, mas amarrava o motor de
decisao a uma unica corretora e a um unico sistema operacional (o pacote
`MetaTrader5` so existe para Windows).

Esta porta separa DECIDIR de EXECUTAR. Sao quatro operacoes — e sao mesmo so
quatro, porque o sistema deliberadamente nao fecha posicao por conta propria:
quem encerra e o stop/alvo que ja viajam anexados na ordem, do lado do
broker.

O que NAO entra aqui, de proposito:

- Fechar posicao. Nao existe esse caminho no sistema, e abrir um aqui seria
  criar por acidente a porta que o projeto inteiro evita.
- Dados de mercado. Coleta de candles/ticks continua no conector MT5; misturar
  as duas coisas nesta porta faria dela um espelho de plataforma de novo.

Unidades: **volume sempre em lotes**, do jeito que o operador entende e que a
configuracao de risco usa. Cada adaptador converte para a unidade nativa da
sua corretora (a cTrader, por exemplo, trabalha em centesimos de unidade do
ativo base) e essa conversao e responsabilidade EXCLUSIVA do adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.strategies.base import SignalDirection


class BrokerError(RuntimeError):
    """Falha ao falar com a corretora ou pedido recusado por ela."""


class BrokerAccountMismatchError(BrokerError):
    """A conta conectada nao corresponde ao modo configurado.

    Vale nos DOIS sentidos: modo DEMO com conta real e modo REAL com conta
    demo. A segunda parece inofensiva e nao e — significa que o operador
    acredita estar arriscando dinheiro e nao esta, ou o contrario.
    """


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    login: int
    currency: str
    balance: float
    equity: float
    is_demo: bool
    leverage: int = 0
    server: str = ""


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    position_id: str
    """Identificador na corretora. Texto, nao inteiro: o MT5 usa ticket
    numerico e a cTrader usa positionId proprio — forcar int aqui vazaria o
    formato de uma delas para o resto do sistema."""

    symbol: str
    direction: SignalDirection
    volume_lots: float
    entry_price: float
    stop_loss: float | None
    take_profit: float | None
    profit: float
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class OrderRequest:
    symbol: str
    direction: SignalDirection
    volume_lots: float
    stop_loss: float
    take_profit: float
    price: float = 0.0
    """Preco de referencia. Ordem e a mercado; serve para calcular desvio."""

    deviation_points: int = 20
    label: str = ""
    comment: str = ""


@dataclass(frozen=True, slots=True)
class OrderResult:
    accepted: bool
    position_id: str | None
    price: float | None
    message: str
    raw_code: int | None = None
    """Codigo nativo da corretora, preservado para auditoria — cada uma tem
    o seu vocabulario e traduzir tudo para um enum comum perderia detalhe
    justamente no caso em que ele importa: a recusa."""


@dataclass(frozen=True, slots=True)
class ProtectionResult:
    accepted: bool
    stop_loss: float
    take_profit: float
    message: str
    raw_code: int | None = None


class BrokerPort(Protocol):
    """O que o motor de execucao precisa de uma corretora. Nada alem."""

    @property
    def name(self) -> str:
        """Identificacao curta para log e auditoria (ex.: "mt5", "ctrader")."""
        ...

    def account(self) -> BrokerAccount:
        """Estado da conta. Levanta `BrokerError` se nao conseguir ler —
        nunca devolve valores inventados para "seguir em frente"."""
        ...

    def open_positions(self, symbol: str | None = None) -> list[BrokerPosition]: ...

    def send_market_order(self, request: OrderRequest) -> OrderResult:
        """Envia ordem a mercado COM stop e alvo anexados no proprio pedido.

        Anexar e inegociavel: se a protecao fosse enviada depois, existiria
        uma janela — por menor que fosse — com posicao aberta e sem stop.
        """
        ...

    def modify_protection(
        self, position_id: str, *, stop_loss: float, take_profit: float
    ) -> ProtectionResult:
        """Altera SOMENTE stop e alvo de uma posicao aberta."""
        ...

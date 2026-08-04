"""Cliente da cTrader Open API sobre um transporte injetavel.

A separacao entre CLIENTE e TRANSPORTE existe por um motivo pratico: o
transporte real (TCP+TLS contra os servidores da Spotware) so pode ser
exercitado com credenciais e conta de verdade, enquanto TODA a logica que
erra em silencio — sequencia de autenticacao, resolucao de simbolo, conversao
de lote para centesimos, montagem do pedido — pode e deve ser testada de
forma deterministica. O `CTraderTransport` e o ponto exato onde a parte
verificavel termina e a parte que exige conta comeca.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import uuid4

from app.broker.ctrader.protocol import (
    OrderType,
    PayloadType,
    SymbolInfo,
    TradeSide,
    envelope,
    error_text,
    is_error,
    parse_symbol,
)
from app.broker.port import BrokerError
from app.strategies.base import SignalDirection

logger = logging.getLogger(__name__)


class CTraderTransport(Protocol):
    """Troca de mensagens JSON com a Open API.

    `request` envia um envelope e devolve a resposta correspondente. O
    transporte e responsavel por casar `clientMsgId` — a API pode intercalar
    eventos nao solicitados (execucao, cotacao) no mesmo canal, e devolver o
    primeiro que chegar produziria a resposta errada para a pergunta certa.
    """

    def request(self, message: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]: ...

    def close(self) -> None: ...


class CTraderClient:
    """Operacoes da Open API que este sistema usa.

    Guarda o catalogo de simbolos em memoria: cada ordem precisa do
    `symbolId` e do `lotSize`, e buscar isso a cada envio adicionaria uma ida
    a rede no caminho mais sensivel do sistema.
    """

    def __init__(
        self,
        transport: CTraderTransport,
        *,
        client_id: str,
        client_secret: str,
        access_token: str,
        account_id: int,
    ) -> None:
        self._transport = transport
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._account_id = account_id
        self._symbols: dict[str, SymbolInfo] = {}
        self._authenticated = False

    # ---- infraestrutura -------------------------------------------------

    def _send(self, payload_type: PayloadType, payload: dict[str, Any]) -> dict[str, Any]:
        message = envelope(payload_type, payload, msg_id=str(uuid4()))
        response = self._transport.request(message)
        if is_error(response):
            raise BrokerError(error_text(response))
        return response.get("payload", {})

    def authenticate(self) -> None:
        """Autentica aplicacao e conta, nesta ordem.

        A ordem nao e estetica: a Open API recusa a autenticacao de conta
        enquanto a aplicacao nao estiver autorizada na mesma conexao.
        """
        if self._authenticated:
            return
        self._send(
            PayloadType.APPLICATION_AUTH_REQ,
            {"clientId": self._client_id, "clientSecret": self._client_secret},
        )
        self._send(
            PayloadType.ACCOUNT_AUTH_REQ,
            {
                "ctidTraderAccountId": self._account_id,
                "accessToken": self._access_token,
            },
        )
        self._authenticated = True

    def close(self) -> None:
        self._transport.close()
        self._authenticated = False

    # ---- catalogo -------------------------------------------------------

    def load_symbols(self, *, force: bool = False) -> dict[str, SymbolInfo]:
        if self._symbols and not force:
            return self._symbols
        self.authenticate()
        payload = self._send(
            PayloadType.SYMBOLS_LIST_REQ,
            {"ctidTraderAccountId": self._account_id, "includeArchivedSymbols": False},
        )
        catalogo: dict[str, SymbolInfo] = {}
        for raw in payload.get("symbol", []):
            info = parse_symbol(raw)
            if info.name:
                catalogo[info.name] = info
        self._symbols = catalogo
        return catalogo

    def resolve_symbol(self, name: str) -> SymbolInfo:
        """Encontra o simbolo tolerando sufixos da corretora.

        Corretoras renomeiam instrumentos ("EURUSD.r", "XAUUSD_i"). Cair fora
        por causa de um sufixo deixaria o robo parado sem motivo real — mesma
        tolerancia que o resolvedor do lado MT5 ja aplica.
        """
        alvo = name.strip().upper()
        catalogo = self.load_symbols()
        if alvo in catalogo:
            return catalogo[alvo]
        candidatos = [info for nome, info in catalogo.items() if nome.startswith(alvo)]
        if len(candidatos) == 1:
            return candidatos[0]
        if candidatos:
            # Empate: escolher "o primeiro" seria adivinhar em qual
            # instrumento o dinheiro entra. Melhor recusar e dizer quais sao.
            nomes = ", ".join(sorted(item.name for item in candidatos)[:5])
            raise BrokerError(
                f"'{alvo}' e ambiguo nesta corretora ({nomes}). "
                "Configure o nome exato do instrumento."
            )
        raise BrokerError(f"Simbolo '{alvo}' nao existe nesta conta cTrader.")

    # ---- consultas ------------------------------------------------------

    def trader(self) -> dict[str, Any]:
        self.authenticate()
        payload = self._send(
            PayloadType.TRADER_REQ, {"ctidTraderAccountId": self._account_id}
        )
        return payload.get("trader", {})

    def reconcile(self) -> list[dict[str, Any]]:
        """Posicoes abertas e ordens pendentes da conta."""
        self.authenticate()
        payload = self._send(
            PayloadType.RECONCILE_REQ, {"ctidTraderAccountId": self._account_id}
        )
        return list(payload.get("position", []))

    # ---- execucao -------------------------------------------------------

    def new_market_order(
        self,
        *,
        symbol: SymbolInfo,
        direction: SignalDirection,
        volume_lots: float,
        stop_loss: float,
        take_profit: float,
        label: str = "",
        comment: str = "",
        slippage_points: int | None = None,
    ) -> dict[str, Any]:
        """Ordem a mercado com stop e alvo no MESMO pedido.

        Enviar a protecao depois abriria uma janela com posicao desprotegida.
        A Open API aceita `stopLoss`/`takeProfit` no proprio
        `ProtoOANewOrderReq`, entao nao ha motivo para separar.
        """
        self.authenticate()
        volume_cents = symbol.lots_to_cents(volume_lots)
        if volume_cents <= 0:
            raise BrokerError(
                f"Volume {volume_lots} lote(s) de {symbol.name} arredonda para zero "
                f"no passo da corretora ({symbol.step_volume_cents} cents)."
            )
        if not symbol.volume_is_tradable(volume_cents):
            minimo = symbol.cents_to_lots(symbol.min_volume_cents)
            maximo = symbol.cents_to_lots(symbol.max_volume_cents)
            raise BrokerError(
                f"Volume {volume_lots} lote(s) fora da faixa de {symbol.name} "
                f"({minimo:.2f} a {maximo:.2f} lotes)."
            )

        payload: dict[str, Any] = {
            "ctidTraderAccountId": self._account_id,
            "symbolId": symbol.symbol_id,
            "orderType": int(OrderType.MARKET),
            "tradeSide": int(
                TradeSide.SELL if direction == SignalDirection.SHORT else TradeSide.BUY
            ),
            "volume": volume_cents,
            "stopLoss": round(stop_loss, symbol.digits),
            "takeProfit": round(take_profit, symbol.digits),
        }
        if label:
            payload["label"] = label[:100]
        if comment:
            payload["comment"] = comment[:255]
        if slippage_points is not None:
            payload["slippageInPoints"] = int(slippage_points)

        return self._send(PayloadType.NEW_ORDER_REQ, payload)

    def amend_protection(
        self, *, position_id: int, stop_loss: float, take_profit: float, digits: int = 5
    ) -> dict[str, Any]:
        """Altera SOMENTE stop e alvo.

        `ProtoOAAmendPositionSLTPReq` nao tem campo de volume nem de
        fechamento — pela propria definicao da mensagem nao existe caminho
        daqui para abrir, aumentar ou encerrar posicao. E a mesma garantia
        estrutural que o lado MT5 obtem com `TRADE_ACTION_SLTP`.
        """
        self.authenticate()
        return self._send(
            PayloadType.AMEND_POSITION_SLTP_REQ,
            {
                "ctidTraderAccountId": self._account_id,
                "positionId": position_id,
                "stopLoss": round(stop_loss, digits),
                "takeProfit": round(take_profit, digits),
            },
        )

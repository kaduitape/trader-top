"""Transporte falso: reproduz o dialogo da Open API sem tocar na rede.

Ele grava TODO envelope enviado, e e isso que permite verificar o que de
fato sairia pelo fio — ordem, volume convertido, campos do pedido — sem
precisar de conta, credencial ou internet.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.broker.ctrader.client import CTraderClient
from app.broker.ctrader.protocol import PayloadType

EURUSD_RAW = {
    "symbolId": 1,
    "symbolName": "EURUSD",
    "digits": 5,
    "lotSize": 10_000_000,
    "minVolume": 100_000,
    "maxVolume": 1_000_000_000,
    "stepVolume": 100_000,
    "enabled": True,
}

XAUUSD_RAW = {
    "symbolId": 41,
    "symbolName": "XAUUSD",
    "digits": 2,
    "lotSize": 10_000_000,
    "minVolume": 100_000,
    "maxVolume": 500_000_000,
    "stepVolume": 100_000,
    "enabled": True,
}


class FakeTransport:
    """Responde por tipo de mensagem; guarda o que foi enviado."""

    def __init__(self, respostas: dict[int, dict[str, Any]] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._respostas: dict[int, dict[str, Any]] = {
            int(PayloadType.APPLICATION_AUTH_REQ): {"payload": {}},
            int(PayloadType.ACCOUNT_AUTH_REQ): {"payload": {}},
            int(PayloadType.SYMBOLS_LIST_REQ): {
                "payload": {"symbol": [EURUSD_RAW, XAUUSD_RAW]}
            },
            int(PayloadType.TRADER_REQ): {
                "payload": {
                    "trader": {
                        "ctidTraderAccountId": 555111,
                        "balance": 1_000_000,  # 10.000,00 na moeda da conta
                        "leverageInCents": 3000,
                        "isLive": False,
                    }
                }
            },
            int(PayloadType.RECONCILE_REQ): {"payload": {"position": []}},
            int(PayloadType.NEW_ORDER_REQ): {
                "payload": {
                    "executionType": 3,
                    "position": {"positionId": 987654, "price": 1.08412},
                }
            },
            int(PayloadType.AMEND_POSITION_SLTP_REQ): {
                "payload": {
                    "executionType": 8,
                    "position": {"stopLoss": 1.08260, "takeProfit": 1.08890},
                }
            },
        }
        if respostas:
            self._respostas.update(respostas)

    def request(self, message: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        self.sent.append(message)
        tipo = int(message["payloadType"])
        resposta = dict(self._respostas.get(tipo, {"payload": {}}))
        resposta.setdefault("payloadType", tipo + 1)
        resposta["clientMsgId"] = message["clientMsgId"]
        return resposta

    def close(self) -> None:
        self.closed = True

    # --- auxiliares de leitura para os testes ---------------------------

    def enviados(self, tipo: PayloadType) -> list[dict[str, Any]]:
        return [m["payload"] for m in self.sent if int(m["payloadType"]) == int(tipo)]

    def tipos(self) -> list[int]:
        return [int(m["payloadType"]) for m in self.sent]


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport) -> CTraderClient:
    return CTraderClient(
        transport,
        client_id="app-id",
        client_secret="app-secret",
        access_token="token-da-conta",
        account_id=555111,
    )

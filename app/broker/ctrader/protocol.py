"""Protocolo da cTrader Open API — envelope, tipos e conversoes de unidade.

Fatos do protocolo, conferidos na documentacao oficial e nos `.proto`
publicados pela Spotware (github.com/spotware/openapi-proto-messages):

- A comunicacao aceita **JSON** ou Protobuf. JSON usa a porta **5036**;
  Protobuf usa a **5035**. Este cliente fala JSON de proposito: evita a
  dependencia de protobuf e mantem o trafego legivel em depuracao, ao custo
  de mensagens maiores — irrelevante no volume que este sistema gera.
- Toda mensagem viaja no envelope `{clientMsgId, payloadType, payload}`.
- **Volume vem em centesimos da unidade do ativo base** ("cents"). O mesmo
  vale para `lotSize`, `minVolume`, `maxVolume` e `stepVolume` do simbolo.
  Ou seja: `volume_em_cents = lotes x lotSize`, e nunca `lotes x 100000`
  chutado — o `lotSize` e por simbolo e e a corretora quem diz qual e.
- **Precos sao inteiros escalonados por `digits`** em varias mensagens; aqui
  eles sao enviados como decimais nos campos `stopLoss`/`takeProfit`, que a
  API aceita em preco absoluto.
- Simbolo e identificado por `symbolId` NUMERICO, nao por nome. Traduzir
  "EURUSD" -> id e trabalho do cliente, e por isso a lista de simbolos e
  carregada e mantida em memoria.

Erros de unidade nesta camada nao aparecem como excecao: aparecem como uma
ordem cem vezes maior do que deveria. Por isso as conversoes ficam isoladas
aqui, com teste proprio.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

JSON_PORT = 5036
PROTOBUF_PORT = 5035

LIVE_HOST = "live.ctraderapi.com"
DEMO_HOST = "demo.ctraderapi.com"

CENTS_PER_UNIT = 100
"""A API expressa volume em centesimos da unidade do ativo base."""


class PayloadType(enum.IntEnum):
    """Subconjunto usado por este sistema (valores oficiais do enum
    `ProtoOAPayloadType`)."""

    APPLICATION_AUTH_REQ = 2100
    APPLICATION_AUTH_RES = 2101
    ACCOUNT_AUTH_REQ = 2102
    ACCOUNT_AUTH_RES = 2103
    NEW_ORDER_REQ = 2106
    AMEND_POSITION_SLTP_REQ = 2110
    SYMBOLS_LIST_REQ = 2114
    SYMBOLS_LIST_RES = 2115
    SYMBOL_BY_ID_REQ = 2116
    SYMBOL_BY_ID_RES = 2117
    TRADER_REQ = 2121
    TRADER_RES = 2122
    RECONCILE_REQ = 2124
    RECONCILE_RES = 2125
    EXECUTION_EVENT = 2126
    ERROR_RES = 2142


class TradeSide(enum.IntEnum):
    BUY = 1
    SELL = 2


class OrderType(enum.IntEnum):
    MARKET = 1
    LIMIT = 2
    STOP = 3


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    """O que e preciso saber de um simbolo para dimensionar uma ordem."""

    symbol_id: int
    name: str
    digits: int
    lot_size_cents: int
    min_volume_cents: int
    max_volume_cents: int
    step_volume_cents: int
    enabled: bool = True

    def lots_to_cents(self, lots: float) -> int:
        """Converte lotes para a unidade da API, respeitando o passo.

        O arredondamento e para BAIXO (nunca para cima): arredondar para
        cima aumentaria o risco da operacao alem do que o motor calculou —
        exatamente o tipo de erro silencioso que nao pode existir aqui.
        """
        if self.lot_size_cents <= 0:
            raise ValueError(f"lotSize invalido para {self.name}: {self.lot_size_cents}")
        bruto = int(lots * self.lot_size_cents)
        step = self.step_volume_cents or 1
        ajustado = (bruto // step) * step
        return max(0, ajustado)

    def cents_to_lots(self, cents: int) -> float:
        if self.lot_size_cents <= 0:  # pragma: no cover - defensivo
            raise ValueError(f"lotSize invalido para {self.name}: {self.lot_size_cents}")
        return cents / self.lot_size_cents

    def volume_is_tradable(self, cents: int) -> bool:
        return self.min_volume_cents <= cents <= self.max_volume_cents


def envelope(payload_type: PayloadType, payload: dict[str, Any], *, msg_id: str) -> dict[str, Any]:
    """Monta a mensagem no formato que a Open API espera."""
    return {
        "clientMsgId": msg_id,
        "payloadType": int(payload_type),
        "payload": payload,
    }


def parse_symbol(raw: dict[str, Any]) -> SymbolInfo:
    """Converte um `ProtoOALightSymbol`/`ProtoOASymbol` em `SymbolInfo`.

    Campos ausentes viram zero em vez de estourar: a lista de simbolos traz
    centenas de instrumentos e um campo faltando em um deles nao pode
    derrubar a carga inteira. O que protege a operacao e
    `volume_is_tradable`, checado na hora de enviar.
    """
    return SymbolInfo(
        symbol_id=int(raw.get("symbolId", 0)),
        name=str(raw.get("symbolName", raw.get("name", ""))).upper(),
        digits=int(raw.get("digits", 5)),
        lot_size_cents=int(raw.get("lotSize", 0)),
        min_volume_cents=int(raw.get("minVolume", 0)),
        max_volume_cents=int(raw.get("maxVolume", 0)),
        step_volume_cents=int(raw.get("stepVolume", 0)),
        enabled=bool(raw.get("enabled", True)),
    )


def is_error(message: dict[str, Any]) -> bool:
    return int(message.get("payloadType", 0)) == int(PayloadType.ERROR_RES)


def error_text(message: dict[str, Any]) -> str:
    payload = message.get("payload", {})
    code = payload.get("errorCode", "?")
    description = payload.get("description", "sem descricao")
    return f"cTrader recusou ({code}): {description}"

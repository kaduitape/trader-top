"""Leitura de ordens/historico e, a partir da Fase 11, envio de ordens a
mercado — sempre em conta demo, nunca em conta real.

`send_market_order` e `modify_position` sao as UNICAS funcoes deste projeto
autorizadas a chamar `client.order_send` — e as duas recusam
incondicionalmente (levantam `MT5RealAccountError`, nunca apenas logam um
aviso) se a conta conectada nao for demo (`AccountSnapshot.is_demo`). Esse
bloqueio nao pode ser contornado por configuracao.

`modify_position` altera APENAS stop-loss e take-profit de uma posicao que
ja existe (`TRADE_ACTION_SLTP`). Nao abre, nao aumenta e nao fecha posicao —
e a unica coisa que o gerenciamento de trailing stop / break-even precisa
fazer, e deliberadamente a unica coisa que ela consegue fazer."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.exceptions import MT5RealAccountError
from app.mt5.account import AccountSnapshot
from app.mt5.client import MT5ClientProtocol
from app.strategies.base import SignalDirection

logger = logging.getLogger(__name__)

# Valores documentados e estaveis da API MetaTrader5 — lidos preferencialmente
# do proprio cliente (real ou fake) via getattr, com estes como fallback.
_TRADE_ACTION_DEAL_DEFAULT = 1
_ORDER_TYPE_BUY_DEFAULT = 0
_ORDER_TYPE_SELL_DEFAULT = 1
_ORDER_FILLING_FOK_DEFAULT = 0
_ORDER_FILLING_IOC_DEFAULT = 1
_ORDER_FILLING_RETURN_DEFAULT = 2
_SYMBOL_FILLING_FOK_FLAG = 1
_SYMBOL_FILLING_IOC_FLAG = 2
_TRADE_RETCODE_DONE_DEFAULT = 10009
_TRADE_ACTION_SLTP_DEFAULT = 2
"""`TRADE_ACTION_SLTP`: altera so os niveis de protecao de uma posicao
existente. Lido do cliente via `getattr`; este e o fallback documentado."""


@dataclass(frozen=True, slots=True)
class RawOrder:
    ticket: int
    symbol: str
    volume_initial: float
    volume_current: float
    price_open: float
    sl: float
    tp: float
    order_type: int
    state: int
    created_at: datetime
    magic: int
    comment: str


@dataclass(frozen=True, slots=True)
class RawHistoryDeal:
    ticket: int
    order: int
    position_id: int
    """Ticket da posição que este deal abriu/fechou — a chave usada pela
    reconciliação (Fase 11) para associar um deal de fechamento à posição
    local correspondente."""
    symbol: str
    volume: float
    price: float
    profit: float
    deal_type: int
    entry: int
    executed_at: datetime
    magic: int
    comment: str


def _row_to_order(row: object) -> RawOrder:
    return RawOrder(
        ticket=int(getattr(row, "ticket", 0)),
        symbol=str(getattr(row, "symbol", "")),
        volume_initial=float(getattr(row, "volume_initial", 0.0)),
        volume_current=float(getattr(row, "volume_current", 0.0)),
        price_open=float(getattr(row, "price_open", 0.0)),
        sl=float(getattr(row, "sl", 0.0)),
        tp=float(getattr(row, "tp", 0.0)),
        order_type=int(getattr(row, "type", 0)),
        state=int(getattr(row, "state", 0)),
        created_at=datetime.fromtimestamp(int(getattr(row, "time_setup", 0)), tz=UTC),
        magic=int(getattr(row, "magic", 0)),
        comment=str(getattr(row, "comment", "")),
    )


def _row_to_history_deal(row: object) -> RawHistoryDeal:
    return RawHistoryDeal(
        ticket=int(getattr(row, "ticket", 0)),
        order=int(getattr(row, "order", 0)),
        position_id=int(getattr(row, "position_id", 0)),
        symbol=str(getattr(row, "symbol", "")),
        volume=float(getattr(row, "volume", 0.0)),
        price=float(getattr(row, "price", 0.0)),
        profit=float(getattr(row, "profit", 0.0)),
        deal_type=int(getattr(row, "type", 0)),
        entry=int(getattr(row, "entry", 0)),
        executed_at=datetime.fromtimestamp(int(getattr(row, "time", 0)), tz=UTC),
        magic=int(getattr(row, "magic", 0)),
        comment=str(getattr(row, "comment", "")),
    )


def fetch_pending_orders(client: MT5ClientProtocol, symbol: str | None = None) -> list[RawOrder]:
    rows = client.orders_get(symbol=symbol)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_orders_get_failed",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return []
    return [_row_to_order(row) for row in rows]


def fetch_history_deals(
    client: MT5ClientProtocol, date_from: datetime, date_to: datetime
) -> list[RawHistoryDeal]:
    rows = client.history_deals_get(date_from=date_from, date_to=date_to)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_history_deals_get_failed",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return []
    return [_row_to_history_deal(row) for row in rows]


@dataclass(frozen=True, slots=True)
class OrderSendResult:
    success: bool
    retcode: int
    order_ticket: int | None
    deal_ticket: int | None
    position_ticket: int | None
    price: float | None
    comment: str


def send_market_order(
    client: MT5ClientProtocol,
    *,
    account: AccountSnapshot,
    symbol: str,
    direction: SignalDirection,
    volume: float,
    price: float,
    stop_loss: float,
    take_profit: float,
    deviation_points: int = 20,
    magic: int,
    comment: str = "",
) -> OrderSendResult:
    """Envia uma ordem de mercado com stop-loss/take-profit já anexados
    no próprio pedido — o broker (não este processo) fica responsável
    por encerrar a posição quando um dos dois for atingido; a
    reconciliação (`app.execution.engine`) apenas detecta quando isso já
    aconteceu, nunca tenta fechar a posição por conta própria.

    Recusa incondicionalmente (`MT5RealAccountError`) se `account.is_demo`
    for `False` — o único portão de segurança que não pode ser contornado
    por nenhuma configuração ou argumento."""
    if not account.is_demo:
        raise MT5RealAccountError(
            f"recusa enviar ordem: a conta {account.login}@{account.server} não é demo."
        )

    order_type = getattr(client, "ORDER_TYPE_BUY", _ORDER_TYPE_BUY_DEFAULT)
    if direction == SignalDirection.SHORT:
        order_type = getattr(client, "ORDER_TYPE_SELL", _ORDER_TYPE_SELL_DEFAULT)

    request = {
        "action": getattr(client, "TRADE_ACTION_DEAL", _TRADE_ACTION_DEAL_DEFAULT),
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": stop_loss,
        "tp": take_profit,
        "deviation": deviation_points,
        "magic": magic,
        "comment": comment,
    }

    # ``symbol_info().filling_mode`` usa flags SYMBOL_FILLING_*; o pedido
    # usa valores ORDER_FILLING_*. Eles nao sao intercambiaveis (por
    # exemplo, flag 2 significa IOC, enquanto ORDER_FILLING 2 e RETURN).
    # Em Market Execution muitos brokers, incluindo a Tickmill, recusam
    # RETURN. Prefere IOC quando permitido, depois FOK; se a especificacao
    # nao estiver disponivel, preserva o comportamento anterior do cliente.
    symbol_info = client.symbol_info(symbol)
    if symbol_info is not None:
        filling_flags = int(getattr(symbol_info, "filling_mode", 0))
        if filling_flags & _SYMBOL_FILLING_IOC_FLAG:
            request["type_filling"] = getattr(
                client, "ORDER_FILLING_IOC", _ORDER_FILLING_IOC_DEFAULT
            )
        elif filling_flags & _SYMBOL_FILLING_FOK_FLAG:
            request["type_filling"] = getattr(
                client, "ORDER_FILLING_FOK", _ORDER_FILLING_FOK_DEFAULT
            )
        else:
            request["type_filling"] = getattr(
                client, "ORDER_FILLING_RETURN", _ORDER_FILLING_RETURN_DEFAULT
            )

    result = client.order_send(request)
    if result is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_order_send_failed",
            extra={"mt5_error_code": code, "mt5_error_description": description, "symbol": symbol},
        )
        return OrderSendResult(
            success=False,
            retcode=code,
            order_ticket=None,
            deal_ticket=None,
            position_ticket=None,
            price=None,
            comment=description,
        )

    retcode = int(getattr(result, "retcode", 0))
    done_code = getattr(client, "TRADE_RETCODE_DONE", _TRADE_RETCODE_DONE_DEFAULT)
    success = retcode == done_code

    if not success:
        logger.warning(
            "mt5_order_rejected",
            extra={"retcode": retcode, "symbol": symbol, "comment": getattr(result, "comment", "")},
        )

    return OrderSendResult(
        success=success,
        retcode=retcode,
        order_ticket=int(getattr(result, "order", 0)) or None,
        deal_ticket=int(getattr(result, "deal", 0)) or None,
        position_ticket=int(getattr(result, "position", 0)) or None,
        price=float(getattr(result, "price", 0.0)) if success else None,
        comment=str(getattr(result, "comment", "")),
    )


@dataclass(frozen=True, slots=True)
class ModifyPositionResult:
    success: bool
    retcode: int
    stop_loss: float
    take_profit: float
    comment: str


def modify_position(
    client: MT5ClientProtocol,
    *,
    account: AccountSnapshot,
    symbol: str,
    position_ticket: int,
    stop_loss: float,
    take_profit: float,
) -> ModifyPositionResult:
    """Altera stop-loss/take-profit de uma posicao aberta (trailing/break-even).

    Mesmo portao incondicional de `send_market_order`: conta que nao seja
    demo levanta `MT5RealAccountError`, sem excecao e sem configuracao que
    contorne.

    Usa `TRADE_ACTION_SLTP`, que por definicao da API do MetaTrader so
    consegue mexer nos niveis de protecao — nao existe caminho aqui para
    abrir, aumentar ou fechar posicao, nem por acidente.
    """
    if not account.is_demo:
        raise MT5RealAccountError(
            f"recusa modificar posicao: a conta {account.login}@{account.server} não é demo."
        )

    request = {
        "action": getattr(client, "TRADE_ACTION_SLTP", _TRADE_ACTION_SLTP_DEFAULT),
        "symbol": symbol,
        "position": position_ticket,
        "sl": stop_loss,
        "tp": take_profit,
    }

    result = client.order_send(request)
    if result is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_modify_position_failed",
            extra={
                "mt5_error_code": code,
                "mt5_error_description": description,
                "symbol": symbol,
                "position_ticket": position_ticket,
            },
        )
        return ModifyPositionResult(
            success=False,
            retcode=code,
            stop_loss=stop_loss,
            take_profit=take_profit,
            comment=description,
        )

    retcode = int(getattr(result, "retcode", 0))
    done_code = getattr(client, "TRADE_RETCODE_DONE", _TRADE_RETCODE_DONE_DEFAULT)
    success = retcode == done_code
    if not success:
        logger.warning(
            "mt5_modify_position_rejected",
            extra={
                "retcode": retcode,
                "symbol": symbol,
                "position_ticket": position_ticket,
                "comment": getattr(result, "comment", ""),
            },
        )

    return ModifyPositionResult(
        success=success,
        retcode=retcode,
        stop_loss=stop_loss,
        take_profit=take_profit,
        comment=str(getattr(result, "comment", "")),
    )

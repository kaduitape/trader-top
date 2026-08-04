"""Adaptador MT5 para `BrokerPort` — a implementacao de referencia.

Nao contem regra nova: traduz a porta para as funcoes que ja existiam em
`app.mt5.*` e que continuam sendo o caminho de producao. Toda a protecao que
elas ja faziam (coerencia entre modo e tipo de conta, stop/alvo anexados ao
pedido, `TRADE_ACTION_SLTP` que so mexe em protecao) continua valendo — este
arquivo nao pode afroxar nada, so reempacotar.
"""

from __future__ import annotations

from app.broker.port import (
    BrokerAccount,
    BrokerAccountMismatchError,
    BrokerError,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ProtectionResult,
)
from app.mt5.account import AccountSnapshot, fetch_account_snapshot
from app.mt5.client import MT5ClientProtocol
from app.mt5.orders import MT5RealAccountError, modify_position, send_market_order
from app.mt5.positions import RawPosition, fetch_open_positions
from app.strategies.base import SignalDirection

_POSITION_TYPE_SELL = 1


def _to_position(raw: RawPosition) -> BrokerPosition:
    return BrokerPosition(
        position_id=str(raw.ticket),
        symbol=raw.symbol,
        direction=(
            SignalDirection.SHORT
            if raw.position_type == _POSITION_TYPE_SELL
            else SignalDirection.LONG
        ),
        volume_lots=raw.volume,
        entry_price=raw.price_open,
        # O MT5 devolve 0.0 quando nao ha nivel definido; None diz a verdade.
        stop_loss=None,
        take_profit=None,
        profit=raw.profit,
        opened_at=raw.opened_at,
    )


class MT5Broker:
    """`BrokerPort` sobre um terminal MetaTrader 5 conectado."""

    name = "mt5"

    def __init__(
        self,
        client: MT5ClientProtocol,
        *,
        account: AccountSnapshot,
        allow_real_account: bool = False,
        magic: int = 0,
    ) -> None:
        self._client = client
        self._account = account
        self._allow_real_account = allow_real_account
        self._magic = magic

    def account(self) -> BrokerAccount:
        snapshot = fetch_account_snapshot(self._client) or self._account
        if snapshot is None:  # pragma: no cover - defensivo
            raise BrokerError("O MetaTrader nao respondeu com os dados da conta.")
        return BrokerAccount(
            login=snapshot.login,
            currency=snapshot.currency,
            balance=snapshot.balance,
            equity=snapshot.equity,
            is_demo=snapshot.is_demo,
            leverage=snapshot.leverage,
            server=snapshot.server,
        )

    def open_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        return [_to_position(raw) for raw in fetch_open_positions(self._client, symbol)]

    def send_market_order(self, request: OrderRequest) -> OrderResult:
        try:
            result = send_market_order(
                self._client,
                account=self._account,
                allow_real_account=self._allow_real_account,
                symbol=request.symbol,
                direction=request.direction,
                volume=request.volume_lots,
                price=request.price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                deviation_points=request.deviation_points,
                magic=self._magic,
                comment=request.comment,
            )
        except MT5RealAccountError as exc:
            raise BrokerAccountMismatchError(str(exc)) from exc

        return OrderResult(
            accepted=result.success,
            position_id=(
                str(result.position_ticket) if result.position_ticket is not None else None
            ),
            price=result.price,
            message=result.comment,
            raw_code=result.retcode,
        )

    def modify_protection(
        self, position_id: str, *, stop_loss: float, take_profit: float
    ) -> ProtectionResult:
        try:
            ticket = int(position_id)
        except ValueError as exc:
            raise BrokerError(
                f"Ticket MT5 invalido: {position_id!r} (esperado um numero)."
            ) from exc

        symbol = ""
        for position in self.open_positions():
            if position.position_id == position_id:
                symbol = position.symbol
                break
        if not symbol:
            raise BrokerError(f"Posicao {position_id} nao esta mais aberta no MetaTrader.")

        try:
            result = modify_position(
                self._client,
                account=self._account,
                allow_real_account=self._allow_real_account,
                symbol=symbol,
                position_ticket=ticket,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )
        except MT5RealAccountError as exc:
            raise BrokerAccountMismatchError(str(exc)) from exc

        return ProtectionResult(
            accepted=result.success,
            stop_loss=result.stop_loss,
            take_profit=result.take_profit,
            message=result.comment,
            raw_code=result.retcode,
        )

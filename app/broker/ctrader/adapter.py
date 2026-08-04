"""Adaptador cTrader para `BrokerPort`.

Traduz o vocabulario da Open API para o que o motor de execucao entende. As
diferencas conceituais em relacao ao MetaTrader ficam TODAS contidas aqui:

- volume em centesimos de unidade, nao em lotes;
- simbolo por id numerico, nao por nome;
- posicao com `positionId` proprio, nao ticket;
- preco de entrada em `price`, e o par stop/alvo dentro de `tradeData`.

Se o motor precisasse saber qualquer uma dessas coisas, a porta teria
falhado no seu proposito.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.broker.ctrader.client import CTraderClient
from app.broker.port import (
    BrokerAccount,
    BrokerAccountMismatchError,
    BrokerError,
    BrokerPosition,
    OrderRequest,
    OrderResult,
    ProtectionResult,
)
from app.strategies.base import SignalDirection

_MONEY_SCALE = 100.0
"""A Open API devolve saldo/patrimonio em centesimos da moeda da conta."""


def _to_direction(raw: Any) -> SignalDirection:
    # tradeSide: 1 = BUY, 2 = SELL.
    return SignalDirection.SHORT if int(raw or 1) == 2 else SignalDirection.LONG


def _optional_price(value: Any) -> float | None:
    if value in (None, "", 0, 0.0):
        return None
    return float(value)


class CTraderBroker:
    """`BrokerPort` sobre a cTrader Open API."""

    name = "ctrader"

    def __init__(
        self,
        client: CTraderClient,
        *,
        allow_real_account: bool = False,
        expect_demo: bool | None = None,
        label: str = "",
    ) -> None:
        self._client = client
        self._allow_real_account = allow_real_account
        self._expect_demo = expect_demo
        self._label = label

    # ---- conta ----------------------------------------------------------

    def account(self) -> BrokerAccount:
        trader = self._client.trader()
        if not trader:
            raise BrokerError("A cTrader nao respondeu com os dados da conta.")

        is_demo = self._resolve_is_demo(trader)
        self._guard_account_type(is_demo)

        balance = float(trader.get("balance", 0)) / _MONEY_SCALE
        return BrokerAccount(
            login=int(trader.get("ctidTraderAccountId", 0)),
            currency=str(trader.get("depositAssetId", "") or ""),
            balance=balance,
            # A Open API nao devolve patrimonio nesta mensagem; usar o saldo
            # e honesto e conservador — nunca somar lucro em aberto que nao
            # foi informado.
            equity=balance,
            is_demo=is_demo,
            leverage=int(trader.get("leverageInCents", 0)) // 100,
        )

    def _resolve_is_demo(self, trader: dict[str, Any]) -> bool:
        """Descobre se a conta e demo.

        A mensagem traz `isLive` em algumas versoes e nada em outras. Quando
        a corretora nao informa, o sistema NAO adivinha: cai na expectativa
        declarada na configuracao e, se nem isso existir, recusa. Chutar
        aqui e chutar se o dinheiro e de verdade.
        """
        if "isLive" in trader:
            return not bool(trader["isLive"])
        if self._expect_demo is not None:
            return self._expect_demo
        raise BrokerError(
            "A cTrader nao informou se a conta e demo ou real, e nenhuma "
            "expectativa foi configurada. Defina CTRADER_ACCOUNT_IS_DEMO."
        )

    def _guard_account_type(self, is_demo: bool) -> None:
        """Mesma guarda de coerencia do lado MT5, nos dois sentidos."""
        if not self._allow_real_account and not is_demo:
            raise BrokerAccountMismatchError(
                "Configurado em modo DEMO com conta REAL na cTrader — ordem recusada."
            )
        if self._allow_real_account and is_demo:
            raise BrokerAccountMismatchError(
                "Configurado em modo REAL com conta demo na cTrader — ordem recusada."
            )

    # ---- posicoes -------------------------------------------------------

    def open_positions(self, symbol: str | None = None) -> list[BrokerPosition]:
        alvo_id: int | None = None
        if symbol:
            alvo_id = self._client.resolve_symbol(symbol).symbol_id

        catalogo = {info.symbol_id: info for info in self._client.load_symbols().values()}
        posicoes: list[BrokerPosition] = []
        for raw in self._client.reconcile():
            trade = raw.get("tradeData", {})
            symbol_id = int(trade.get("symbolId", 0))
            if alvo_id is not None and symbol_id != alvo_id:
                continue
            info = catalogo.get(symbol_id)
            volume_cents = int(trade.get("volume", 0))
            aberta_em = trade.get("openTimestamp")
            posicoes.append(
                BrokerPosition(
                    position_id=str(raw.get("positionId", "")),
                    symbol=info.name if info else str(symbol_id),
                    direction=_to_direction(trade.get("tradeSide")),
                    volume_lots=info.cents_to_lots(volume_cents) if info else 0.0,
                    entry_price=float(raw.get("price", 0.0)),
                    stop_loss=_optional_price(raw.get("stopLoss")),
                    take_profit=_optional_price(raw.get("takeProfit")),
                    profit=float(raw.get("swap", 0) or 0) / _MONEY_SCALE,
                    opened_at=(
                        datetime.fromtimestamp(int(aberta_em) / 1000, tz=UTC)
                        if aberta_em
                        else None
                    ),
                )
            )
        return posicoes

    # ---- execucao -------------------------------------------------------

    def send_market_order(self, request: OrderRequest) -> OrderResult:
        # A conta e verificada ANTES de qualquer envio: a guarda de
        # coerencia entre modo e tipo de conta so serve se rodar primeiro.
        self.account()

        symbol = self._client.resolve_symbol(request.symbol)
        payload = self._client.new_market_order(
            symbol=symbol,
            direction=request.direction,
            volume_lots=request.volume_lots,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            label=self._label or request.label,
            comment=request.comment,
            slippage_points=request.deviation_points or None,
        )

        # A resposta e um ProtoOAExecutionEvent; a posicao pode ainda nao
        # existir se a ordem foi aceita mas nao preenchida.
        posicao = payload.get("position", {})
        ordem = payload.get("order", {})
        position_id = posicao.get("positionId") or ordem.get("positionId")
        preco = posicao.get("price") or ordem.get("executionPrice")

        return OrderResult(
            accepted=bool(position_id),
            position_id=str(position_id) if position_id else None,
            price=float(preco) if preco else None,
            message=str(payload.get("executionType", "") or "ordem enviada"),
            raw_code=int(payload.get("executionType", 0) or 0),
        )

    def modify_protection(
        self, position_id: str, *, stop_loss: float, take_profit: float
    ) -> ProtectionResult:
        self.account()

        try:
            identificador = int(position_id)
        except ValueError as exc:
            raise BrokerError(
                f"positionId cTrader invalido: {position_id!r} (esperado um numero)."
            ) from exc

        digits = 5
        for posicao in self.open_positions():
            if posicao.position_id == position_id:
                info = self._client.load_symbols().get(posicao.symbol)
                if info is not None:
                    digits = info.digits
                break
        else:
            raise BrokerError(f"Posicao {position_id} nao esta mais aberta na cTrader.")

        payload = self._client.amend_protection(
            position_id=identificador,
            stop_loss=stop_loss,
            take_profit=take_profit,
            digits=digits,
        )
        posicao = payload.get("position", {})
        return ProtectionResult(
            accepted=True,
            stop_loss=float(posicao.get("stopLoss", stop_loss) or stop_loss),
            take_profit=float(posicao.get("takeProfit", take_profit) or take_profit),
            message=str(payload.get("executionType", "") or "protecao alterada"),
        )

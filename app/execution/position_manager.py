"""Gerenciamento da posicao ABERTA: trailing stop e break-even aplicados.

`app.apexflow.risk` calcula PARA ONDE o stop deveria ir; este modulo e o
braco que de fato move — envia `modify_position` ao MetaTrader e persiste o
novo nivel na linha do trade. Sem ele o gerenciamento seria uma conta que
ninguem executa, e o operador acreditaria estar protegido sem estar.

Tres garantias, todas cobertas por teste:

1. **Nunca aumenta risco.** O intent so e aplicado quando melhora o stop
   (`StopIntent.should_move` ja garante isso); se a corretora recusar, a
   linha do trade permanece com o stop ANTIGO — nunca se grava um nivel que
   nao foi aceito de verdade.
2. **Nunca fecha posicao.** `modify_position` usa `TRADE_ACTION_SLTP`, que
   por definicao da API so mexe nos niveis de protecao.
3. **Conta real continua bloqueada** pelo mesmo portao incondicional de
   `send_market_order`.

O preco corrente vem de `RawPosition.price_current` — o que a corretora
reporta para AQUELA posicao, nao um tick separado que poderia estar
dessincronizado do estado dela.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.apexflow.config import ApexFlowConfig
from app.apexflow.risk import StopIntent, StopMoveKind, evaluate_stop_move
from app.database.models.live_trade import LiveTrade
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.mt5.account import AccountSnapshot
from app.mt5.client import MT5ClientProtocol
from app.mt5.orders import modify_position
from app.mt5.positions import fetch_open_positions
from app.strategies.base import SignalDirection

logger = logging.getLogger(__name__)


class StopMoveOutcome(enum.StrEnum):
    NOT_NEEDED = "NOT_NEEDED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    """Faltou dado para decidir (posicao nao reportada, sem stop registrado)
    — reportado, nunca tratado como "nada a fazer"."""


@dataclass(frozen=True, slots=True)
class StopMoveReport:
    outcome: StopMoveOutcome
    kind: StopMoveKind
    previous_stop: float | None
    new_stop: float | None
    current_r: float | None
    message: str

    @property
    def moved(self) -> bool:
        return self.outcome == StopMoveOutcome.APPLIED


def _direction_of(trade: LiveTrade) -> SignalDirection:
    return (
        SignalDirection.LONG
        if str(trade.direction).upper() == SignalDirection.LONG.value
        else SignalDirection.SHORT
    )


def manage_open_position(
    session: Session,
    client: MT5ClientProtocol,
    trade: LiveTrade,
    *,
    account: AccountSnapshot,
    symbol: str,
    config: ApexFlowConfig,
    allow_real_account: bool = False,
) -> StopMoveReport:
    """Avalia e, se for o caso, move o stop da posicao aberta `trade`.

    Nunca levanta excecao por condicao de mercado; so propaga
    `MT5RealAccountError`, que e um erro de seguranca e deve interromper
    tudo.
    """
    if trade.entry_price is None or trade.stop_loss is None:
        return StopMoveReport(
            outcome=StopMoveOutcome.UNAVAILABLE,
            kind=StopMoveKind.NONE,
            previous_stop=None,
            new_stop=None,
            current_r=None,
            message=(
                "Posicao sem preco de entrada ou stop registrado — trailing e "
                "break-even nao podem ser calculados."
            ),
        )
    if trade.mt5_position_ticket is None:
        return StopMoveReport(
            outcome=StopMoveOutcome.UNAVAILABLE,
            kind=StopMoveKind.NONE,
            previous_stop=float(trade.stop_loss),
            new_stop=None,
            current_r=None,
            message="Posicao sem ticket do MetaTrader — nada a modificar.",
        )

    position = next(
        (
            item
            for item in fetch_open_positions(client, symbol)
            if item.ticket == trade.mt5_position_ticket
        ),
        None,
    )
    if position is None:
        return StopMoveReport(
            outcome=StopMoveOutcome.UNAVAILABLE,
            kind=StopMoveKind.NONE,
            previous_stop=float(trade.stop_loss),
            new_stop=None,
            current_r=None,
            message=(
                "A corretora nao reporta mais esta posicao — a reconciliacao "
                "cuida disso; nenhuma modificacao e tentada."
            ),
        )

    direction = _direction_of(trade)
    entry_price = float(trade.entry_price)
    previous_stop = float(trade.stop_loss)
    intent: StopIntent = evaluate_stop_move(
        direction=direction,
        entry_price=entry_price,
        current_price=position.price_current,
        stop_loss=previous_stop,
        config=config,
    )

    if not intent.should_move:
        return StopMoveReport(
            outcome=StopMoveOutcome.NOT_NEEDED,
            kind=intent.kind,
            previous_stop=previous_stop,
            new_stop=None,
            current_r=intent.current_r,
            message=intent.reason,
        )

    assert intent.new_stop_loss is not None  # garantido por `should_move`
    take_profit = float(trade.take_profit) if trade.take_profit is not None else 0.0
    result = modify_position(
        client,
        account=account,
        allow_real_account=allow_real_account,
        symbol=symbol,
        position_ticket=trade.mt5_position_ticket,
        stop_loss=intent.new_stop_loss,
        take_profit=take_profit,
    )

    if not result.success:
        # O stop persistido continua sendo o ANTIGO: gravar um nivel que a
        # corretora recusou faria o sistema acreditar em uma protecao que
        # nao existe.
        return StopMoveReport(
            outcome=StopMoveOutcome.REJECTED,
            kind=intent.kind,
            previous_stop=previous_stop,
            new_stop=None,
            current_r=intent.current_r,
            message=(
                f"Corretora recusou mover o stop para {intent.new_stop_loss:.5f}: "
                f"{result.comment}"
            ),
        )

    LiveTradeRepository(session).update_stop_loss(
        trade, Decimal(str(intent.new_stop_loss))
    )
    logger.info(
        "position_stop_moved",
        extra={
            "trade_id": trade.id,
            "kind": intent.kind.value,
            "previous_stop": previous_stop,
            "new_stop": intent.new_stop_loss,
            "current_r": intent.current_r,
        },
    )
    label = "Break-even" if intent.kind == StopMoveKind.BREAK_EVEN else "Trailing"
    return StopMoveReport(
        outcome=StopMoveOutcome.APPLIED,
        kind=intent.kind,
        previous_stop=previous_stop,
        new_stop=intent.new_stop_loss,
        current_r=intent.current_r,
        message=(
            f"{label}: stop movido de {previous_stop:.5f} para "
            f"{intent.new_stop_loss:.5f} ({intent.current_r:.2f}R). {intent.reason}"
        ),
    )

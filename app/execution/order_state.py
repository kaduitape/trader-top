"""Máquina de estados de ordem (Fase 11), `docs/architecture.md` seção 5:

```text
SIGNAL_CREATED -> RISK_REJECTED
               -> RISK_APPROVED -> ORDER_CHECKED -> ORDER_SENT -> POSITION_OPEN
                                                               -> REJECTED
                    POSITION_OPEN -> CLOSE_PENDING -> CLOSED
                    POSITION_OPEN -> RECONCILING -> CLOSED | POSITION_OPEN
```

Puramente funcional (sem I/O), mesmo padrão de `app.core.system_mode` —
testável em isolamento, importável de qualquer camada sem risco de
import circular com `app.database`.
"""

from __future__ import annotations

import enum


class OrderState(enum.StrEnum):
    SIGNAL_CREATED = "SIGNAL_CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_APPROVED = "RISK_APPROVED"
    ORDER_CHECKED = "ORDER_CHECKED"
    ORDER_SENT = "ORDER_SENT"
    POSITION_OPEN = "POSITION_OPEN"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    CLOSE_PENDING = "CLOSE_PENDING"
    CLOSED = "CLOSED"
    RECONCILING = "RECONCILING"


_ALLOWED_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.SIGNAL_CREATED: frozenset({OrderState.RISK_REJECTED, OrderState.RISK_APPROVED}),
    OrderState.RISK_APPROVED: frozenset({OrderState.ORDER_CHECKED}),
    OrderState.ORDER_CHECKED: frozenset({OrderState.ORDER_SENT}),
    OrderState.ORDER_SENT: frozenset(
        {OrderState.POSITION_OPEN, OrderState.REJECTED, OrderState.CANCELLED}
    ),
    OrderState.POSITION_OPEN: frozenset({OrderState.CLOSE_PENDING, OrderState.RECONCILING}),
    OrderState.CLOSE_PENDING: frozenset({OrderState.CLOSED}),
    OrderState.RECONCILING: frozenset({OrderState.CLOSED, OrderState.POSITION_OPEN}),
}

TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {OrderState.RISK_REJECTED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.CLOSED}
)


class OrderStateError(Exception):
    """Transição de estado de ordem inválida."""


def validate_order_transition(current: OrderState, target: OrderState) -> None:
    if current in TERMINAL_STATES:
        raise OrderStateError(
            f"{current.value} é um estado terminal — nenhuma transição permitida."
        )

    allowed = _ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise OrderStateError(f"transição inválida: {current.value} -> {target.value}.")

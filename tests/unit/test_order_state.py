import pytest

from app.execution.order_state import OrderState, OrderStateError, validate_order_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderState.SIGNAL_CREATED, OrderState.RISK_REJECTED),
        (OrderState.SIGNAL_CREATED, OrderState.RISK_APPROVED),
        (OrderState.RISK_APPROVED, OrderState.ORDER_CHECKED),
        (OrderState.ORDER_CHECKED, OrderState.ORDER_SENT),
        (OrderState.ORDER_SENT, OrderState.POSITION_OPEN),
        (OrderState.ORDER_SENT, OrderState.REJECTED),
        (OrderState.ORDER_SENT, OrderState.CANCELLED),
        (OrderState.POSITION_OPEN, OrderState.CLOSE_PENDING),
        (OrderState.POSITION_OPEN, OrderState.RECONCILING),
        (OrderState.CLOSE_PENDING, OrderState.CLOSED),
        (OrderState.RECONCILING, OrderState.CLOSED),
        (OrderState.RECONCILING, OrderState.POSITION_OPEN),
    ],
)
def test_allowed_transitions_do_not_raise(current: OrderState, target: OrderState) -> None:
    validate_order_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (OrderState.SIGNAL_CREATED, OrderState.ORDER_SENT),
        (OrderState.SIGNAL_CREATED, OrderState.POSITION_OPEN),
        (OrderState.RISK_APPROVED, OrderState.ORDER_SENT),
        (OrderState.ORDER_CHECKED, OrderState.POSITION_OPEN),
        (OrderState.POSITION_OPEN, OrderState.SIGNAL_CREATED),
        (OrderState.ORDER_SENT, OrderState.CLOSE_PENDING),
    ],
)
def test_disallowed_transitions_raise(current: OrderState, target: OrderState) -> None:
    with pytest.raises(OrderStateError):
        validate_order_transition(current, target)


@pytest.mark.parametrize(
    "terminal",
    [OrderState.RISK_REJECTED, OrderState.REJECTED, OrderState.CANCELLED, OrderState.CLOSED],
)
def test_terminal_states_allow_no_further_transition(terminal: OrderState) -> None:
    with pytest.raises(OrderStateError):
        validate_order_transition(terminal, OrderState.SIGNAL_CREATED)

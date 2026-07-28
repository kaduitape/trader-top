import pytest

from app.core.enums import SystemMode
from app.core.system_mode import SystemModeError, validate_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SystemMode.DISABLED, SystemMode.DATA_ONLY),
        (SystemMode.DATA_ONLY, SystemMode.BACKTEST),
        (SystemMode.BACKTEST, SystemMode.REPLAY),
        (SystemMode.REPLAY, SystemMode.PAPER),
        (SystemMode.PAPER, SystemMode.DEMO),
    ],
)
def test_single_step_forward_transitions_are_allowed(
    current: SystemMode, target: SystemMode
) -> None:
    validate_transition(current, target)  # nao levanta


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SystemMode.DISABLED, SystemMode.BACKTEST),
        (SystemMode.DISABLED, SystemMode.REPLAY),
        (SystemMode.DISABLED, SystemMode.PAPER),
        (SystemMode.DISABLED, SystemMode.DEMO),
        (SystemMode.DATA_ONLY, SystemMode.PAPER),
        (SystemMode.DATA_ONLY, SystemMode.DEMO),
        (SystemMode.REPLAY, SystemMode.DEMO),
    ],
)
def test_skipping_intermediate_states_is_rejected(current: SystemMode, target: SystemMode) -> None:
    with pytest.raises(SystemModeError):
        validate_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (SystemMode.PAPER, SystemMode.DATA_ONLY),
        (SystemMode.PAPER, SystemMode.DISABLED),
        (SystemMode.REPLAY, SystemMode.DISABLED),
        (SystemMode.BACKTEST, SystemMode.DATA_ONLY),
        (SystemMode.DEMO, SystemMode.PAPER),
        (SystemMode.DEMO, SystemMode.DISABLED),
    ],
)
def test_stepping_backward_is_always_allowed(current: SystemMode, target: SystemMode) -> None:
    validate_transition(current, target)  # nao levanta


@pytest.mark.parametrize(
    "current",
    [
        SystemMode.DATA_ONLY,
        SystemMode.BACKTEST,
        SystemMode.REPLAY,
        SystemMode.PAPER,
        SystemMode.DEMO,
    ],
)
def test_emergency_stop_reachable_from_any_active_state(current: SystemMode) -> None:
    validate_transition(current, SystemMode.EMERGENCY_STOP)  # nao levanta


def test_emergency_stop_not_reachable_from_disabled() -> None:
    with pytest.raises(SystemModeError):
        validate_transition(SystemMode.DISABLED, SystemMode.EMERGENCY_STOP)


def test_recovery_from_emergency_stop_only_to_disabled() -> None:
    validate_transition(SystemMode.EMERGENCY_STOP, SystemMode.DISABLED)  # nao levanta

    with pytest.raises(SystemModeError):
        validate_transition(SystemMode.EMERGENCY_STOP, SystemMode.DATA_ONLY)


def test_real_locked_is_reachable_only_right_after_demo() -> None:
    """REAL foi liberado por decisao explicita do dono do sistema; o que
    continua valendo e a escada — nenhum degrau pode ser pulado."""
    validate_transition(SystemMode.DEMO, SystemMode.REAL_LOCKED)  # nao levanta

    with pytest.raises(SystemModeError):
        validate_transition(SystemMode.PAPER, SystemMode.REAL_LOCKED)


def test_real_enabled_cannot_be_reached_by_skipping_real_locked() -> None:
    validate_transition(SystemMode.REAL_LOCKED, SystemMode.REAL_ENABLED)  # nao levanta

    with pytest.raises(SystemModeError):
        validate_transition(SystemMode.DEMO, SystemMode.REAL_ENABLED)


def test_transition_to_same_mode_is_rejected() -> None:
    with pytest.raises(SystemModeError):
        validate_transition(SystemMode.PAPER, SystemMode.PAPER)

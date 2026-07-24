import pytest

from app.risk.position_sizing import compute_position_size


def test_basic_position_size_matches_expected_risk() -> None:
    # Risco: 1% de 10_000 = 100. Distancia ate o stop: 0.0010 (10 pips em
    # EURUSD, point=0.0001). Contrato: 100_000. Perda por lote = 0.0010 *
    # 100_000 = 100 -> volume = 100/100 = 1.0 lote.
    volume = compute_position_size(
        balance=10_000.0,
        risk_pct=1.0,
        stop_distance_price=0.0010,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    assert volume == pytest.approx(1.0)


def test_volume_normalized_to_step() -> None:
    volume = compute_position_size(
        balance=1_000.0,
        risk_pct=1.0,
        stop_distance_price=0.0010,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    # Risco = 10; perda por lote = 100 -> raw = 0.10 -> ja no step.
    assert volume == pytest.approx(0.10)
    assert round(volume / 0.01) == pytest.approx(volume / 0.01)


def test_volume_below_minimum_returns_zero_never_a_smaller_lot() -> None:
    volume = compute_position_size(
        balance=10.0,
        risk_pct=0.1,
        stop_distance_price=0.0010,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )
    assert volume == 0.0


def test_volume_clamped_to_maximum() -> None:
    volume = compute_position_size(
        balance=10_000_000.0,
        risk_pct=5.0,
        stop_distance_price=0.0001,
        contract_size=100_000.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
    )
    assert volume == pytest.approx(50.0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"balance": 0.0},
        {"balance": -100.0},
        {"risk_pct": 0.0},
        {"risk_pct": -1.0},
        {"stop_distance_price": 0.0},
        {"stop_distance_price": -0.001},
        {"contract_size": 0.0},
    ],
)
def test_invalid_inputs_return_zero_never_raise(kwargs: dict) -> None:
    base = {
        "balance": 10_000.0,
        "risk_pct": 1.0,
        "stop_distance_price": 0.0010,
        "contract_size": 100_000.0,
        "volume_min": 0.01,
        "volume_max": 100.0,
        "volume_step": 0.01,
    }
    base.update(kwargs)
    assert compute_position_size(**base) == 0.0


def test_never_scales_with_a_multiplier_parameter() -> None:
    """Documenta a garantia estrutural anti-martingale: a funcao nao tem
    NENHUM parametro relacionado a sequencia/resultado anterior — apenas
    saldo atual, risco fixo e distancia do stop DESTE sinal."""
    import inspect

    signature = inspect.signature(compute_position_size)
    param_names = set(signature.parameters.keys())
    forbidden = {"consecutive_losses", "previous_result", "multiplier", "streak", "martingale"}
    assert not (param_names & forbidden)

import pytest

from app.core.exceptions import MT5ConnectionError
from app.mt5.connection import MT5Connection, MT5ConnectionConfig
from tests.fixtures.fake_mt5_client import FakeMT5Client


def _config(**overrides: object) -> MT5ConnectionConfig:
    base: dict[str, object] = {
        "terminal_path": None,
        "login": None,
        "password": None,
        "server": None,
        "bridge_host": None,
        "bridge_port": 18812,
        "timeout_ms": 1000,
        "max_reconnect_attempts": 3,
        "reconnect_backoff_seconds": 1.0,
    }
    base.update(overrides)
    return MT5ConnectionConfig(**base)  # type: ignore[arg-type]


def test_connect_success() -> None:
    client = FakeMT5Client(initialize_results=[True])
    conn = MT5Connection(_config(), client=client)

    assert conn.connect() is True
    assert conn.is_connected is True
    assert client.initialize_calls == 1


def test_connect_failure_no_retry() -> None:
    client = FakeMT5Client(initialize_results=[False])
    client.last_error_result = (-6, "Terminal: Authorization failed")
    conn = MT5Connection(_config(), client=client)

    assert conn.connect() is False
    assert conn.is_connected is False
    assert client.initialize_calls == 1


class _KwargsSpyClient(FakeMT5Client):
    """So para este teste: registra os NOMES exatos dos kwargs recebidos
    por `initialize`, algo que `FakeMT5Client.initialize` (assinatura
    tipada, todo parametro com default `None`) nao consegue distinguir
    -- "omitido" e "passado como None explicito" chegam identicos nela."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.last_kwargs: dict[str, object] = {}

    def initialize(self, **kwargs: object) -> bool:  # type: ignore[override]
        self.last_kwargs = kwargs
        return super().initialize(**kwargs)  # type: ignore[arg-type]


def test_connect_omits_login_password_server_when_not_configured() -> None:
    """Bug real, achado testando contra um terminal MT5 de verdade (Fase
    16): a API real do MetaTrader5 rejeita `login=None` explicito
    ("Invalid \"login\" argument"), mesmo quando o terminal ja tem uma
    sessao autenticada lembrada -- so funciona se o kwarg for omitido por
    completo. `FakeMT5Client` nao expunha esse bug (aceita `None` de boa),
    entao este teste verifica os kwargs BRUTOS recebidos, nao so o
    resultado."""
    client = _KwargsSpyClient(initialize_results=[True])
    conn = MT5Connection(_config(), client=client)

    conn.connect()

    assert "login" not in client.last_kwargs
    assert "password" not in client.last_kwargs
    assert "server" not in client.last_kwargs
    # `path` segue a mesma regra dos outros: sem valor, o kwarg some. Passar
    # `path=None` explicito nao e neutro para a API real do MetaTrader5.
    assert "path" not in client.last_kwargs
    assert client.last_kwargs["timeout"] == 1000


def test_connect_includes_login_password_server_when_configured() -> None:
    client = _KwargsSpyClient(initialize_results=[True])
    conn = MT5Connection(
        _config(login=12345, password="secret", server="Broker-Demo"), client=client
    )

    conn.connect()

    assert client.last_kwargs["login"] == 12345
    assert client.last_kwargs["password"] == "secret"
    assert client.last_kwargs["server"] == "Broker-Demo"


def test_connect_with_retry_succeeds_after_failures() -> None:
    client = FakeMT5Client(initialize_results=[False, False, True])
    sleep_calls: list[float] = []
    conn = MT5Connection(
        _config(max_reconnect_attempts=5), client=client, sleep_fn=sleep_calls.append
    )

    assert conn.connect_with_retry() is True
    assert client.initialize_calls == 3
    assert sleep_calls == [1.0, 2.0]


def test_connect_with_retry_exhausted_returns_false() -> None:
    client = FakeMT5Client(initialize_results=[False, False, False])
    sleep_calls: list[float] = []
    conn = MT5Connection(
        _config(max_reconnect_attempts=3), client=client, sleep_fn=sleep_calls.append
    )

    assert conn.connect_with_retry() is False
    assert client.initialize_calls == 3
    assert sleep_calls == [1.0, 2.0]


def test_context_manager_raises_when_exhausted() -> None:
    client = FakeMT5Client(initialize_results=[False, False, False])
    conn = MT5Connection(
        _config(max_reconnect_attempts=3), client=client, sleep_fn=lambda _seconds: None
    )

    with pytest.raises(MT5ConnectionError), conn:
        pass

    assert client.shutdown_calls == 0


def test_context_manager_disconnects_on_success() -> None:
    client = FakeMT5Client(initialize_results=[True])
    conn = MT5Connection(_config(), client=client)

    with conn as active:
        assert active.is_connected is True

    assert client.shutdown_calls == 1
    assert conn.is_connected is False

"""Conexao com o terminal MetaTrader 5: inicializacao, reconexao com backoff
exponencial e encerramento.

Nenhuma outra parte do sistema deve chamar `MetaTrader5.initialize`/
`shutdown` diretamente — sempre atraves de `MT5Connection`, para que o
comportamento de reconexao, timeout e logging seja consistente em toda a
aplicacao.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Self

from app.core.config import Settings
from app.core.exceptions import MT5ConnectionError
from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MT5ConnectionConfig:
    terminal_path: str | None
    login: int | None
    password: str | None
    server: str | None
    timeout_ms: int
    max_reconnect_attempts: int
    reconnect_backoff_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        return cls(
            terminal_path=settings.mt5_terminal_path,
            login=settings.mt5_login,
            password=settings.mt5_password,
            server=settings.mt5_server,
            timeout_ms=settings.mt5_timeout_ms,
            max_reconnect_attempts=settings.mt5_max_reconnect_attempts,
            reconnect_backoff_seconds=settings.mt5_reconnect_backoff_seconds,
        )


def _import_real_client() -> MT5ClientProtocol:
    try:
        import MetaTrader5
    except ModuleNotFoundError as exc:
        raise MT5ConnectionError(
            "Pacote 'MetaTrader5' nao instalado. Ele so publica wheel para "
            "Windows (fala com o terminal via DLL/named pipe) -- instale-o "
            "com 'pip install \".[mt5]\"' num host Windows com o terminal "
            "MT5 configurado; nao funciona dentro de um container Linux."
        ) from exc

    return MetaTrader5


class MT5Connection:
    """Gerencia o ciclo de vida de uma conexao com o terminal MetaTrader 5.

    Injecao de dependencia: `client` e `sleep_fn` sao substituidos por
    fakes nos testes, para que nenhum teste dependa de um terminal MT5
    instalado nem execute `time.sleep` de verdade durante o backoff.
    """

    def __init__(
        self,
        config: MT5ConnectionConfig,
        client: MT5ClientProtocol | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._client = client if client is not None else _import_real_client()
        self._sleep = sleep_fn
        self._connected = False
        self.last_known_login: int | None = None

    @property
    def client(self) -> MT5ClientProtocol:
        return self._client

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> bool:
        """Uma unica tentativa de conexao (sem retry). Retorna False e loga
        o motivo em caso de falha, sem levantar excecao.

        `login`/`password`/`server` so entram nos kwargs quando configurados
        de verdade (nao `None`): a API real do `MetaTrader5.initialize`
        rejeita `login=None` explicito ("Invalid \"login\" argument"),
        mesmo quando o terminal ja tem uma sessao autenticada lembrada --
        omitir o kwarg por completo (em vez de passar `None`) e o unico
        jeito de reaproveitar essa sessao ja logada, sem MT5_LOGIN/
        MT5_PASSWORD/MT5_SERVER no `.env`."""
        kwargs: dict[str, Any] = {
            "path": self._config.terminal_path,
            "timeout": self._config.timeout_ms,
        }
        if self._config.login is not None:
            kwargs["login"] = self._config.login
        if self._config.password is not None:
            kwargs["password"] = self._config.password
        if self._config.server is not None:
            kwargs["server"] = self._config.server

        ok = self._client.initialize(**kwargs)
        if not ok:
            code, description = self._client.last_error()
            logger.warning(
                "mt5_connect_failed",
                extra={"mt5_error_code": code, "mt5_error_description": description},
            )
            self._connected = False
            return False

        self._connected = True
        logger.info("mt5_connected", extra={"server": self._config.server})
        return True

    def connect_with_retry(self) -> bool:
        """Tenta conectar com backoff exponencial ate
        `max_reconnect_attempts`. Retorna False (nao levanta excecao) se
        todas as tentativas falharem — quem chama decide o que fazer."""
        attempts = 0
        while attempts < self._config.max_reconnect_attempts:
            if self.connect():
                return True
            attempts += 1
            if attempts >= self._config.max_reconnect_attempts:
                break
            backoff_seconds = self._config.reconnect_backoff_seconds * (2 ** (attempts - 1))
            logger.info(
                "mt5_reconnect_backoff",
                extra={"attempt": attempts, "backoff_seconds": backoff_seconds},
            )
            self._sleep(backoff_seconds)

        logger.error("mt5_reconnect_exhausted", extra={"attempts": attempts})
        return False

    def disconnect(self) -> None:
        self._client.shutdown()
        self._connected = False
        logger.info("mt5_disconnected")

    def __enter__(self) -> Self:
        if not self.connect_with_retry():
            raise MT5ConnectionError(
                "Nao foi possivel conectar ao terminal MetaTrader 5 apos "
                f"{self._config.max_reconnect_attempts} tentativas."
            )
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.disconnect()

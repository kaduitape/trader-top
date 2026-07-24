"""Verificacao de saude do terminal MetaTrader 5 e deteccao de troca de
conta. Somente leitura — nenhuma funcao aqui modifica estado."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TerminalHealth:
    connected: bool
    trade_allowed: bool
    community_connected: bool
    company: str
    terminal_name: str
    terminal_path: str
    checked_at: datetime


def fetch_terminal_health(client: MT5ClientProtocol) -> TerminalHealth | None:
    """Le `terminal_info()` e traduz para um snapshot tipado.

    Retorna `None` se o terminal nao respondeu (`terminal_info()` retornou
    `None`) — quem chama decide se isso e um circuit breaker ou apenas um
    aviso, dependendo do contexto (ver app/risk em fases futuras)."""
    info = client.terminal_info()
    if info is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_terminal_info_unavailable",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return None

    return TerminalHealth(
        connected=bool(getattr(info, "connected", False)),
        trade_allowed=bool(getattr(info, "trade_allowed", False)),
        community_connected=bool(getattr(info, "community_connected", False)),
        company=str(getattr(info, "company", "")),
        terminal_name=str(getattr(info, "name", "")),
        terminal_path=str(getattr(info, "path", "")),
        checked_at=datetime.now(UTC),
    )


def detect_account_change(previous_login: int | None, current_login: int) -> bool:
    """True se a conta conectada mudou desde a ultima checagem conhecida.

    `previous_login is None` (primeira checagem) nunca conta como troca de
    conta."""
    return previous_login is not None and previous_login != current_login

"""Informacoes da conta conectada — somente leitura.

Inclui a identificacao demo/real exigida pelo prompt mestre: nenhuma
decisao de risco ou execucao (fases futuras) pode assumir o tipo de conta;
ela deve ser lida aqui, a cada checagem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)

# Valores documentados e estaveis da API MetaTrader5 para ACCOUNT_TRADE_MODE.
# Lidos preferencialmente do proprio cliente (real ou fake) via getattr,
# com estes como fallback, para nao depender de um terminal instalado para
# saber os valores.
_ACCOUNT_TRADE_MODE_DEMO_DEFAULT = 0
_ACCOUNT_TRADE_MODE_REAL_DEFAULT = 2


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    login: int
    server: str
    balance: float
    equity: float
    margin: float
    margin_free: float
    currency: str
    leverage: int
    trade_mode: int
    is_demo: bool


def fetch_account_snapshot(client: MT5ClientProtocol) -> AccountSnapshot | None:
    info = client.account_info()
    if info is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_account_info_unavailable",
            extra={"mt5_error_code": code, "mt5_error_description": description},
        )
        return None

    trade_mode = int(getattr(info, "trade_mode", _ACCOUNT_TRADE_MODE_REAL_DEFAULT))
    demo_mode = getattr(client, "ACCOUNT_TRADE_MODE_DEMO", _ACCOUNT_TRADE_MODE_DEMO_DEFAULT)
    is_demo = trade_mode == demo_mode

    snapshot = AccountSnapshot(
        login=int(getattr(info, "login", 0)),
        server=str(getattr(info, "server", "")),
        balance=float(getattr(info, "balance", 0.0)),
        equity=float(getattr(info, "equity", 0.0)),
        margin=float(getattr(info, "margin", 0.0)),
        margin_free=float(getattr(info, "margin_free", 0.0)),
        currency=str(getattr(info, "currency", "")),
        leverage=int(getattr(info, "leverage", 0)),
        trade_mode=trade_mode,
        is_demo=is_demo,
    )

    if not snapshot.is_demo:
        logger.warning(
            "mt5_account_is_real",
            extra={"login": snapshot.login, "server": snapshot.server},
        )

    return snapshot

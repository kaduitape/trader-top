"""Escolha da corretora por configuracao.

`BROKER=mt5` (padrao) mantem exatamente o caminho que ja esta em producao.
`BROKER=ctrader` liga o adaptador da Open API.

O padrao nao e uma preferencia estetica: MT5 e o unico caminho hoje validado
contra uma conta real neste projeto. Trocar exige um ato deliberado, e a
troca falha alto se faltar credencial — nunca cai de volta no MT5 em
silencio, porque "achei que estava operando na cTrader" e um jeito ruim de
descobrir onde o dinheiro foi parar.
"""

from __future__ import annotations

from app.broker.port import BrokerError, BrokerPort
from app.core.config import Settings

BROKER_MT5 = "mt5"
BROKER_CTRADER = "ctrader"
SUPPORTED_BROKERS = frozenset({BROKER_MT5, BROKER_CTRADER})


def build_ctrader_broker(
    settings: Settings, *, allow_real_account: bool = False
) -> BrokerPort:
    """Monta o adaptador cTrader com transporte TCP+TLS real."""
    from app.broker.ctrader.adapter import CTraderBroker
    from app.broker.ctrader.client import CTraderClient
    from app.broker.ctrader.transport import CTraderTcpTransport

    faltando = [
        nome
        for nome, valor in (
            ("CTRADER_CLIENT_ID", settings.ctrader_client_id),
            ("CTRADER_CLIENT_SECRET", settings.ctrader_client_secret),
            ("CTRADER_ACCESS_TOKEN", settings.ctrader_access_token),
            ("CTRADER_ACCOUNT_ID", settings.ctrader_account_id),
        )
        if not valor
    ]
    if faltando:
        raise BrokerError(
            "cTrader selecionada, mas faltam credenciais: " + ", ".join(faltando)
        )

    transport = CTraderTcpTransport(demo=settings.ctrader_account_is_demo is not False)
    client = CTraderClient(
        transport,
        client_id=str(settings.ctrader_client_id),
        client_secret=str(settings.ctrader_client_secret),
        access_token=str(settings.ctrader_access_token),
        account_id=int(settings.ctrader_account_id or 0),
    )
    return CTraderBroker(
        client,
        allow_real_account=allow_real_account,
        expect_demo=settings.ctrader_account_is_demo,
        label=settings.ctrader_order_label,
    )


def resolve_broker_name(settings: Settings) -> str:
    escolhido = (settings.broker or BROKER_MT5).strip().lower()
    if escolhido not in SUPPORTED_BROKERS:
        raise BrokerError(
            f"BROKER={escolhido!r} nao e suportado. Use um de: "
            + ", ".join(sorted(SUPPORTED_BROKERS))
        )
    return escolhido

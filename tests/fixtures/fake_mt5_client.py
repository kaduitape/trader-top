"""Cliente MT5 fake usado por toda a suite de testes de `app.mt5`.

Implementa `MT5ClientProtocol` sem depender de um terminal MetaTrader 5
instalado nem inventar respostas reais — os valores retornados sao sempre
dados de teste explicitos, configurados por quem escreve o teste.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

# Valores arbitrarios de teste (nao correspondem necessariamente aos
# valores reais usados pelo pacote MetaTrader5, apenas precisam ser
# internamente consistentes).
TIMEFRAME_M1 = 1
TIMEFRAME_M2 = 2
TIMEFRAME_M5 = 5
TIMEFRAME_M15 = 15
TIMEFRAME_M30 = 30
TIMEFRAME_H1 = 16385
TIMEFRAME_H4 = 16388
TIMEFRAME_D1 = 16408
TIMEFRAME_W1 = 32769
TIMEFRAME_MN1 = 49153
COPY_TICKS_ALL = -1
ACCOUNT_TRADE_MODE_DEMO = 0
ACCOUNT_TRADE_MODE_REAL = 2
TRADE_RETCODE_DONE = 10009
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
TRADE_ACTION_DEAL = 1


class FakeMT5Client:
    """Fake configuravel de `MT5ClientProtocol`.

    Cada atributo `*_result` guarda o que a chamada correspondente deve
    devolver; `initialize_results` e uma fila consumida a cada chamada
    (permite simular N falhas seguidas de sucesso, para testar reconexao)."""

    TIMEFRAME_M1 = TIMEFRAME_M1
    TIMEFRAME_M2 = TIMEFRAME_M2
    TIMEFRAME_M5 = TIMEFRAME_M5
    TIMEFRAME_M15 = TIMEFRAME_M15
    TIMEFRAME_M30 = TIMEFRAME_M30
    TIMEFRAME_H1 = TIMEFRAME_H1
    TIMEFRAME_H4 = TIMEFRAME_H4
    TIMEFRAME_D1 = TIMEFRAME_D1
    TIMEFRAME_W1 = TIMEFRAME_W1
    TIMEFRAME_MN1 = TIMEFRAME_MN1
    COPY_TICKS_ALL = COPY_TICKS_ALL
    ACCOUNT_TRADE_MODE_DEMO = ACCOUNT_TRADE_MODE_DEMO
    ACCOUNT_TRADE_MODE_REAL = ACCOUNT_TRADE_MODE_REAL
    TRADE_RETCODE_DONE = TRADE_RETCODE_DONE
    ORDER_TYPE_BUY = ORDER_TYPE_BUY
    ORDER_TYPE_SELL = ORDER_TYPE_SELL
    TRADE_ACTION_DEAL = TRADE_ACTION_DEAL

    def __init__(self, initialize_results: list[bool] | None = None) -> None:
        self.initialize_results = list(initialize_results) if initialize_results else [True]
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.last_error_result: tuple[int, str] = (1, "no error")

        self.terminal_info_result: Any | None = None
        self.account_info_result: Any | None = None
        self.symbols_get_result: tuple[Any, ...] | None = ()
        self.symbol_info_result: Any | None = None
        self.symbol_info_tick_result: Any | None = None
        self.copy_rates_from_pos_result: Any | None = None
        self.copy_rates_range_result: Any | None = None
        self.copy_ticks_range_result: Any | None = None
        self.positions_get_result: tuple[Any, ...] | None = ()
        self.orders_get_result: tuple[Any, ...] | None = ()
        self.history_deals_get_result: tuple[Any, ...] | None = ()
        self.copy_rates_range_calls: list[tuple[str, int, Any, Any]] = []
        self.copy_ticks_range_calls: list[tuple[str, Any, Any, int]] = []

        self.order_check_result: Any | None = None
        self.order_send_result: Any | None = None
        self.order_send_calls: list[dict[str, Any]] = []

    def initialize(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int | None = None,
    ) -> bool:
        self.initialize_calls += 1
        if self.initialize_results:
            return self.initialize_results.pop(0)
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def last_error(self) -> tuple[int, str]:
        return self.last_error_result

    def terminal_info(self) -> Any | None:
        return self.terminal_info_result

    def version(self) -> tuple[int, int, str] | None:
        return (500, 5735, "21 Jul 2026")

    def account_info(self) -> Any | None:
        return self.account_info_result

    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None:
        return self.symbols_get_result

    def symbol_info(self, symbol: str) -> Any | None:
        return self.symbol_info_result

    def symbol_info_tick(self, symbol: str) -> Any | None:
        return self.symbol_info_tick_result

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        return True

    def copy_rates_from(
        self, symbol: str, timeframe: int, date_from: Any, count: int
    ) -> Any | None:
        return self.copy_rates_from_pos_result

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> Any | None:
        return self.copy_rates_from_pos_result

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: Any, date_to: Any
    ) -> Any | None:
        self.copy_rates_range_calls.append((symbol, timeframe, date_from, date_to))
        if self.copy_rates_range_result is not None:
            return self.copy_rates_range_result
        return self.copy_rates_from_pos_result

    def copy_ticks_from(self, symbol: str, date_from: Any, count: int, flags: int) -> Any | None:
        return self.copy_ticks_range_result

    def copy_ticks_range(self, symbol: str, date_from: Any, date_to: Any, flags: int) -> Any | None:
        self.copy_ticks_range_calls.append((symbol, date_from, date_to, flags))
        return self.copy_ticks_range_result

    def market_book_add(self, symbol: str) -> bool:
        return False

    def market_book_get(self, symbol: str) -> tuple[Any, ...] | None:
        return None

    def market_book_release(self, symbol: str) -> bool:
        return True

    def positions_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None:
        return self.positions_get_result

    def orders_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None:
        return self.orders_get_result

    def history_orders_get(self, **kwargs: Any) -> tuple[Any, ...] | None:
        return ()

    def history_deals_get(self, **kwargs: Any) -> tuple[Any, ...] | None:
        return self.history_deals_get_result

    def order_check(self, request: dict[str, Any]) -> Any | None:
        return self.order_check_result

    def order_send(self, request: dict[str, Any]) -> Any | None:
        self.order_send_calls.append(request)
        return self.order_send_result


def make_terminal_info(
    *,
    connected: bool = True,
    trade_allowed: bool = True,
    community_connected: bool = False,
    company: str = "Test Broker Ltd",
    name: str = "MetaTrader 5",
    path: str = "C:/Program Files/MetaTrader 5/terminal64.exe",
) -> SimpleNamespace:
    return SimpleNamespace(
        connected=connected,
        trade_allowed=trade_allowed,
        community_connected=community_connected,
        company=company,
        name=name,
        path=path,
    )


def make_account_info(
    *,
    login: int = 12345678,
    server: str = "TestBroker-Demo",
    balance: float = 10_000.0,
    equity: float = 10_050.0,
    margin: float = 100.0,
    margin_free: float = 9_950.0,
    currency: str = "USD",
    leverage: int = 100,
    trade_mode: int = ACCOUNT_TRADE_MODE_DEMO,
) -> SimpleNamespace:
    return SimpleNamespace(
        login=login,
        server=server,
        balance=balance,
        equity=equity,
        margin=margin,
        margin_free=margin_free,
        currency=currency,
        leverage=leverage,
        trade_mode=trade_mode,
    )


def make_symbol_info(
    *,
    name: str = "EURUSD",
    description: str = "Euro vs US Dollar",
    digits: int = 5,
    point: float = 0.00001,
    volume_min: float = 0.01,
    volume_max: float = 100.0,
    volume_step: float = 0.01,
    trade_contract_size: float = 100_000.0,
    spread: int = 2,
    trade_mode: int = 4,
    visible: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=description,
        digits=digits,
        point=point,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
        trade_contract_size=trade_contract_size,
        spread=spread,
        trade_mode=trade_mode,
        visible=visible,
    )


_CANDLE_DTYPE = np.dtype(
    [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("tick_volume", "i8"),
        ("spread", "i4"),
        ("real_volume", "i8"),
    ]
)

_TICK_DTYPE = np.dtype(
    [
        ("time", "i8"),
        ("bid", "f8"),
        ("ask", "f8"),
        ("last", "f8"),
        ("volume", "f8"),
        ("time_msc", "i8"),
        ("flags", "i4"),
        ("volume_real", "f8"),
    ]
)


def make_order_send_result(
    *,
    retcode: int = TRADE_RETCODE_DONE,
    order: int = 1001,
    deal: int = 2001,
    position: int = 3001,
    price: float = 1.1000,
    comment: str = "Request executed",
) -> SimpleNamespace:
    return SimpleNamespace(
        retcode=retcode, order=order, deal=deal, position=position, price=price, comment=comment
    )


def make_position(
    *,
    ticket: int = 3001,
    symbol: str = "EURUSD",
    volume: float = 0.01,
    price_open: float = 1.1000,
    price_current: float = 1.1000,
    profit: float = 0.0,
    swap: float = 0.0,
    position_type: int = ORDER_TYPE_BUY,
    time: int = 1_700_000_000,
    magic: int = 0,
    comment: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        volume=volume,
        price_open=price_open,
        price_current=price_current,
        profit=profit,
        swap=swap,
        type=position_type,
        time=time,
        magic=magic,
        comment=comment,
    )


def make_history_deal(
    *,
    ticket: int = 4001,
    order: int = 1001,
    position_id: int = 3001,
    symbol: str = "EURUSD",
    volume: float = 0.01,
    price: float = 1.1050,
    profit: float = 5.0,
    deal_type: int = ORDER_TYPE_SELL,
    entry: int = 1,
    time: int = 1_700_000_300,
    magic: int = 0,
    comment: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        order=order,
        position_id=position_id,
        symbol=symbol,
        volume=volume,
        price=price,
        profit=profit,
        type=deal_type,
        entry=entry,
        time=time,
        magic=magic,
        comment=comment,
    )


def make_rates_array(
    rows: list[tuple[int, float, float, float, float, int, int, int]],
) -> np.ndarray:
    """Emula o retorno de `copy_rates_from_pos`/`copy_rates_range`: um
    array numpy estruturado, acessado por `row['campo']`."""
    return np.array(rows, dtype=_CANDLE_DTYPE)


def make_ticks_array(
    rows: list[tuple[int, float, float, float, float, int, int, float]],
) -> np.ndarray:
    """Emula o retorno de `copy_ticks_from`/`copy_ticks_range`."""
    return np.array(rows, dtype=_TICK_DTYPE)

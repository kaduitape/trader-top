"""Interface tipada do subconjunto do pacote `MetaTrader5` usado neste
projeto.

`MT5ClientProtocol` documenta exatamente quais funcoes o resto de `app.mt5`
chama. Nos testes, um cliente fake que implementa este protocolo substitui
o pacote real — nenhum teste depende de um terminal MetaTrader 5 instalado
nem envia uma ordem de verdade.

`order_check`/`order_send` foram adicionados na Fase 11 (executor em
conta demo) — a UNICA camada autorizada a chama-los e
`app.mt5.orders.send_market_order`, que exige coerencia entre o tipo da
conta conectada (`AccountSnapshot.is_demo`) e o modo configurado: sem
`allow_real_account` recusa conta real, e com ele recusa conta demo. `order_calc_margin`/`order_calc_profit` continuam fora do
escopo (sem consumidor concreto ainda).
"""

from __future__ import annotations

from typing import Any, Protocol


class MT5ClientProtocol(Protocol):
    def initialize(
        self,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        timeout: int | None = None,
    ) -> bool: ...

    def shutdown(self) -> None: ...

    def last_error(self) -> tuple[int, str]: ...

    def terminal_info(self) -> Any | None: ...

    def version(self) -> tuple[int, int, str] | None: ...

    def account_info(self) -> Any | None: ...

    def symbols_get(self, group: str | None = None) -> tuple[Any, ...] | None: ...

    def symbol_info(self, symbol: str) -> Any | None: ...

    def symbol_info_tick(self, symbol: str) -> Any | None: ...

    def symbol_select(self, symbol: str, enable: bool = True) -> bool: ...

    def copy_rates_from(
        self, symbol: str, timeframe: int, date_from: Any, count: int
    ) -> Any | None: ...

    def copy_rates_from_pos(
        self, symbol: str, timeframe: int, start_pos: int, count: int
    ) -> Any | None: ...

    def copy_rates_range(
        self, symbol: str, timeframe: int, date_from: Any, date_to: Any
    ) -> Any | None: ...

    def copy_ticks_from(
        self, symbol: str, date_from: Any, count: int, flags: int
    ) -> Any | None: ...

    def copy_ticks_range(
        self, symbol: str, date_from: Any, date_to: Any, flags: int
    ) -> Any | None: ...

    def market_book_add(self, symbol: str) -> bool: ...

    def market_book_get(self, symbol: str) -> tuple[Any, ...] | None: ...

    def market_book_release(self, symbol: str) -> bool: ...

    def positions_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def orders_get(
        self,
        symbol: str | None = None,
        group: str | None = None,
        ticket: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def history_orders_get(
        self,
        date_from: Any = None,
        date_to: Any = None,
        group: str | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def history_deals_get(
        self,
        date_from: Any = None,
        date_to: Any = None,
        group: str | None = None,
        ticket: int | None = None,
        position: int | None = None,
    ) -> tuple[Any, ...] | None: ...

    def order_check(self, request: dict[str, Any]) -> Any | None: ...

    def order_send(self, request: dict[str, Any]) -> Any | None: ...

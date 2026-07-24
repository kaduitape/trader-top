"""Coleta de candles e ticks — somente leitura.

Os timestamps retornados pelo MetaTrader5 sao o horario do servidor da
corretora (nao necessariamente UTC exato). Sao armazenados aqui como UTC
por convencao do projeto; a validacao/correcao de fuso horario fica para a
Fase 3 (qualidade de dados), que e explicitamente responsavel por detectar
"timezone incorreto" (ver docs/data-model.md).

Achado real na Fase 16 (operando contra um terminal MT5 real pela
primeira vez): candles (`time`) E o tick AO VIVO (`symbol_info_tick`,
`time`/`time_msc`) usam o horario do SERVIDOR da corretora, mas ticks
HISTORICOS (`copy_ticks_from`/`copy_ticks_range`, tambem `time_msc`) usam
UTC de verdade -- duas bases de tempo diferentes na mesma API, para a
mesma corretora. `fetch_server_time` existe para dar um "agora" na MESMA
base dos candles; nao usar `datetime.now(UTC)` para isso.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.mt5.client import MT5ClientProtocol

logger = logging.getLogger(__name__)


class Timeframe(enum.StrEnum):
    M1 = "M1"
    M2 = "M2"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"
    MN1 = "MN1"


_TIMEFRAME_ATTR_NAMES: dict[Timeframe, str] = {
    Timeframe.M1: "TIMEFRAME_M1",
    Timeframe.M2: "TIMEFRAME_M2",
    Timeframe.M5: "TIMEFRAME_M5",
    Timeframe.M15: "TIMEFRAME_M15",
    Timeframe.M30: "TIMEFRAME_M30",
    Timeframe.H1: "TIMEFRAME_H1",
    Timeframe.H4: "TIMEFRAME_H4",
    Timeframe.D1: "TIMEFRAME_D1",
    Timeframe.W1: "TIMEFRAME_W1",
    Timeframe.MN1: "TIMEFRAME_MN1",
}

# MN1 e nominal (30 dias / 2_592_000s): meses reais variam 28-31 dias. So
# afeta a tolerancia de deteccao de buraco em `check_candles` (Fase 3), que
# ja usa `delta_seconds <= timeframe_seconds * 1.5` — folga suficiente para
# nao gerar falso positivo em fevereiro (28 dias) nem em meses de 31 dias.
TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M2: 120,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1_800,
    Timeframe.H1: 3_600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
    Timeframe.W1: 604_800,
    Timeframe.MN1: 2_592_000,
}


def _resolve_timeframe(client: MT5ClientProtocol, timeframe: Timeframe) -> int:
    attr_name = _TIMEFRAME_ATTR_NAMES[timeframe]
    value = getattr(client, attr_name, None)
    if value is None:
        raise ValueError(
            f"Cliente MT5 nao possui a constante '{attr_name}' para o timeframe {timeframe}."
        )
    return int(value)


@dataclass(frozen=True, slots=True)
class RawCandle:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


@dataclass(frozen=True, slots=True)
class RawTick:
    timestamp: datetime
    bid: float
    ask: float
    last: float
    volume: float
    flags: int


def _row_to_candle(row: object) -> RawCandle:
    return RawCandle(
        open_time=datetime.fromtimestamp(int(row["time"]), tz=UTC),  # type: ignore[index]
        open=float(row["open"]),  # type: ignore[index]
        high=float(row["high"]),  # type: ignore[index]
        low=float(row["low"]),  # type: ignore[index]
        close=float(row["close"]),  # type: ignore[index]
        tick_volume=int(row["tick_volume"]),  # type: ignore[index]
        spread=int(row["spread"]),  # type: ignore[index]
        real_volume=int(row["real_volume"]),  # type: ignore[index]
    )


def _row_to_tick(row: object) -> RawTick:
    time_msc = int(row["time_msc"])  # type: ignore[index]
    return RawTick(
        timestamp=datetime.fromtimestamp(time_msc / 1000, tz=UTC),
        bid=float(row["bid"]),  # type: ignore[index]
        ask=float(row["ask"]),  # type: ignore[index]
        last=float(row["last"]),  # type: ignore[index]
        volume=float(row["volume"]),  # type: ignore[index]
        flags=int(row["flags"]),  # type: ignore[index]
    )


def fetch_server_time(client: MT5ClientProtocol, symbol: str) -> datetime | None:
    """Horario ATUAL do lado do servidor da corretora — para usar como
    "agora" ao comparar/buscar CANDLES (`copy_rates_from_pos`/
    `copy_rates_range`, campo `time`), nunca `datetime.now(UTC)`.

    Achado real operando ao vivo contra um terminal MT5 real (Fase 16):
    `symbol_info_tick(...).time`/`.time_msc` reportam o horario do
    SERVIDOR (aqui, 3h a frente do UTC real) -- a MESMA base de tempo dos
    candles. `copy_ticks_from`/`copy_ticks_range` (ticks HISTORICOS,
    `fetch_ticks_range` abaixo) reportam UTC de verdade -- uma base
    DIFERENTE, no mesmo terminal, para a mesma corretora. Sem usar esta
    funcao, o cursor de coleta incremental de candles (`date_from` =
    ultima candle conhecida + 1 barra, comparado contra `datetime.now(UTC)`
    real) fica permanentemente "no futuro" e para de avancar para sempre,
    silenciosamente."""
    tick = client.symbol_info_tick(symbol)
    if tick is None:
        return None
    time_msc = getattr(tick, "time_msc", None)
    if time_msc:
        return datetime.fromtimestamp(time_msc / 1000, tz=UTC)
    time_sec = getattr(tick, "time", None)
    if time_sec:
        return datetime.fromtimestamp(time_sec, tz=UTC)
    return None


def fetch_candles_from_pos(
    client: MT5ClientProtocol,
    symbol: str,
    timeframe: Timeframe,
    count: int,
    start_pos: int = 0,
) -> list[RawCandle]:
    """Busca as `count` candles mais recentes (a partir da posicao
    `start_pos`, onde 0 e a candle mais recente/em formacao)."""
    mt5_timeframe = _resolve_timeframe(client, timeframe)
    rows = client.copy_rates_from_pos(symbol, mt5_timeframe, start_pos, count)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_copy_rates_failed",
            extra={
                "symbol": symbol,
                "timeframe": timeframe.value,
                "mt5_error_code": code,
                "mt5_error_description": description,
            },
        )
        return []
    return [_row_to_candle(row) for row in rows]


def fetch_candles_range(
    client: MT5ClientProtocol,
    symbol: str,
    timeframe: Timeframe,
    date_from: datetime,
    date_to: datetime,
) -> list[RawCandle]:
    """Busca candles de `symbol` no intervalo [date_from, date_to] — usado
    para preenchimento incremental (coletar apenas o que falta desde a
    ultima candle conhecida), diferente de `fetch_candles_from_pos`, que
    sempre busca a partir da mais recente.

    Intervalo invertido/vazio (`date_from >= date_to`) retorna lista vazia
    sem chamar o MetaTrader: acontece toda vez que o polling incremental
    (ex.: `paper run`) roda mais rapido do que o timeframe fecha uma nova
    barra (comum em H1+ com polling de segundos) -- o terminal real
    responde "Call failed" para um intervalo invertido em vez de uma
    lista vazia, o que gerava um WARNING falso a cada iteracao sem barra
    nova (bug real, achado rodando `paper run` ao vivo, Fase 16)."""
    if date_from >= date_to:
        return []
    mt5_timeframe = _resolve_timeframe(client, timeframe)
    rows = client.copy_rates_range(symbol, mt5_timeframe, date_from, date_to)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_copy_rates_range_failed",
            extra={
                "symbol": symbol,
                "timeframe": timeframe.value,
                "mt5_error_code": code,
                "mt5_error_description": description,
            },
        )
        return []
    return [_row_to_candle(row) for row in rows]


def fetch_ticks_range(
    client: MT5ClientProtocol,
    symbol: str,
    date_from: datetime,
    date_to: datetime,
) -> list[RawTick]:
    """Busca todos os ticks de `symbol` no intervalo [date_from, date_to]."""
    flags = getattr(client, "COPY_TICKS_ALL", 0)
    rows = client.copy_ticks_range(symbol, date_from, date_to, flags)
    if rows is None:
        code, description = client.last_error()
        logger.warning(
            "mt5_copy_ticks_failed",
            extra={
                "symbol": symbol,
                "mt5_error_code": code,
                "mt5_error_description": description,
            },
        )
        return []
    return [_row_to_tick(row) for row in rows]

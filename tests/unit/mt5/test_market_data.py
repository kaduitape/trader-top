from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.mt5.market_data import (
    Timeframe,
    fetch_candles_from_pos,
    fetch_candles_range,
    fetch_server_time,
    fetch_ticks_range,
)
from tests.fixtures.fake_mt5_client import FakeMT5Client, make_rates_array, make_ticks_array


def test_fetch_candles_from_pos_converts_rows() -> None:
    client = FakeMT5Client()
    client.copy_rates_from_pos_result = make_rates_array(
        [
            (1_700_000_000, 1.1000, 1.1010, 1.0990, 1.1005, 120, 2, 0),
            (1_700_000_060, 1.1005, 1.1020, 1.1000, 1.1015, 130, 2, 0),
        ]
    )

    candles = fetch_candles_from_pos(client, "EURUSD", Timeframe.M1, count=2)

    assert len(candles) == 2
    assert candles[0].open_time == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert candles[0].open == pytest.approx(1.1000)
    assert candles[0].close == pytest.approx(1.1005)
    assert candles[0].tick_volume == 120
    assert candles[1].tick_volume == 130


def test_fetch_candles_from_pos_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.copy_rates_from_pos_result = None
    client.last_error_result = (-2, "invalid params")

    assert fetch_candles_from_pos(client, "EURUSD", Timeframe.M1, count=10) == []


def test_fetch_candles_raises_for_unresolvable_timeframe() -> None:
    client = FakeMT5Client()
    client.TIMEFRAME_M1 = None  # type: ignore[assignment]

    with pytest.raises(ValueError):
        fetch_candles_from_pos(client, "EURUSD", Timeframe.M1, count=10)


def test_fetch_server_time_prefers_time_msc() -> None:
    """`fetch_server_time` da o "agora" na MESMA base de tempo dos
    candles (horario do SERVIDOR da corretora) -- achado real operando
    contra a Tickmill (Fase 16): `symbol_info_tick.time`/`.time_msc` sao
    horario de servidor, diferente de `copy_ticks_from`/`copy_ticks_
    range` (ticks historicos), que sao UTC de verdade. Preferir
    `time_msc` (mais precisão) sobre `time` quando ambos disponíveis."""
    client = FakeMT5Client()
    client.symbol_info_tick_result = SimpleNamespace(time=1_700_000_000, time_msc=1_700_000_000_500)

    result = fetch_server_time(client, "EURUSD")

    assert result == datetime.fromtimestamp(1_700_000_000.5, tz=UTC)


def test_fetch_server_time_falls_back_to_time_when_no_time_msc() -> None:
    client = FakeMT5Client()
    client.symbol_info_tick_result = SimpleNamespace(time=1_700_000_000, time_msc=0)

    result = fetch_server_time(client, "EURUSD")

    assert result == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_fetch_server_time_returns_none_when_tick_unavailable() -> None:
    client = FakeMT5Client()
    client.symbol_info_tick_result = None

    assert fetch_server_time(client, "EURUSD") is None


def test_fetch_candles_range_skips_call_when_range_is_inverted() -> None:
    """Bug real, achado rodando `paper run` ao vivo contra um timeframe
    H1+ (Fase 16): quando o polling roda mais rapido do que uma nova
    barra fecha, `date_from` (cursor + 1 barra) fica DEPOIS de `date_to`
    (agora) -- o terminal real responde "Call failed" para esse
    intervalo invertido em vez de uma lista vazia, gerando um WARNING
    falso a cada iteracao sem barra nova."""
    client = FakeMT5Client()
    client.copy_rates_range_result = make_rates_array(
        [(1_700_000_000, 1.1, 1.1, 1.1, 1.1, 1, 0, 0)]
    )
    now = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    candles = fetch_candles_range(client, "EURUSD", Timeframe.H1, now, now - timedelta(seconds=1))

    assert candles == []
    assert client.copy_rates_range_calls == []


def test_fetch_ticks_range_converts_rows() -> None:
    client = FakeMT5Client()
    client.copy_ticks_range_result = make_ticks_array(
        [
            (1_700_000_000, 1.1000, 1.1002, 0.0, 0.0, 1_700_000_000_500, 6, 0.0),
        ]
    )

    ticks = fetch_ticks_range(
        client,
        "EURUSD",
        datetime.fromtimestamp(1_700_000_000, tz=UTC),
        datetime.fromtimestamp(1_700_000_060, tz=UTC),
    )

    assert len(ticks) == 1
    assert ticks[0].bid == pytest.approx(1.1000)
    assert ticks[0].ask == pytest.approx(1.1002)
    assert ticks[0].flags == 6
    assert ticks[0].timestamp == datetime.fromtimestamp(1_700_000_000.5, tz=UTC)


def test_fetch_ticks_range_returns_empty_when_none() -> None:
    client = FakeMT5Client()
    client.copy_ticks_range_result = None
    client.last_error_result = (-2, "invalid params")

    assert (
        fetch_ticks_range(
            client,
            "EURUSD",
            datetime.fromtimestamp(0, tz=UTC),
            datetime.fromtimestamp(1, tz=UTC),
        )
        == []
    )

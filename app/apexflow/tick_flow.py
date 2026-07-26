"""Tick Collector: buffer circular e metricas de fluxo de ticks.

Le o comportamento do mercado onde ele de fato acontece — entre as
candles. Uma candle M5 fechada esconde se aqueles 5 minutos tiveram 40
ticks preguicosos ou 400 ticks acelerando: e essa diferenca que separa um
rompimento com continuidade de um rompimento vazio.

Desempenho e requisito, nao detalhe: `TickBuffer` e um `deque` com
`maxlen` (insercao e descarte O(1), memoria constante) e todas as metricas
saem de UMA passada sobre a janela. Nenhuma consulta a banco acontece
aqui — quem chama traz os ticks ja em memoria.

Precisao honesta: quando a janela nao tem ticks suficientes para uma
metrica, ela vem `None` e o motivo fica em `warnings`. Nenhum numero e
estimado para preencher lacuna — um feature vector com `None` e melhor que
um com um numero inventado.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


class TickLike(Protocol):
    """Aceita tanto `app.mt5.market_data.RawTick` quanto o modelo
    persistido `app.database.models.tick.Tick` — os dois expoem os mesmos
    campos, e o motor nunca precisa saber de onde o tick veio."""

    timestamp: datetime
    bid: object
    ask: object


MIN_TICKS_FOR_FLOW = 10
"""Abaixo disso qualquer taxa/aceleracao e ruido amostral, nao leitura."""

MIN_TICKS_PER_HALF = 5
"""Cada metade da janela precisa disso para uma comparacao honesta."""


@dataclass(slots=True)
class TickBuffer:
    """Janela deslizante de ticks com descarte automatico.

    `maxlen` limita memoria e custo: o buffer nunca cresce, e o tick mais
    antigo sai sozinho quando um novo entra.
    """

    maxlen: int = 2_000
    _ticks: deque = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._ticks = deque(maxlen=self.maxlen)

    def push(self, tick: TickLike) -> None:
        self._ticks.append(tick)

    def extend(self, ticks: Iterable[TickLike]) -> None:
        self._ticks.extend(ticks)

    def clear(self) -> None:
        self._ticks.clear()

    def snapshot(self) -> tuple[TickLike, ...]:
        """Copia imutavel para calculo — o buffer continua recebendo ticks
        enquanto as metricas sao computadas."""
        return tuple(self._ticks)

    def __len__(self) -> int:
        return len(self._ticks)

    @property
    def is_empty(self) -> bool:
        return not self._ticks


class TickDirection:
    UP = 1
    DOWN = -1
    FLAT = 0


@dataclass(frozen=True, slots=True)
class TickFlowMetrics:
    """Fotografia do fluxo na janela analisada.

    Campos `None` significam "nao ha dado suficiente para afirmar", nunca
    zero — a diferenca importa para o feature vector e para os vetos.
    """

    tick_count: int
    window_seconds: float
    ticks_per_second: float | None
    tick_acceleration: float | None
    """Razao entre a taxa da metade recente e a da metade anterior. >1
    acelerando, <1 desacelerando."""

    mean_interval_ms: float | None
    max_interval_ms: float | None
    uptick_ratio: float | None
    """Fracao de ticks que subiram (0-1). 0.5 = equilibrio."""

    direction_bias: int
    """`TickDirection`: para onde o fluxo empurra, se e que empurra."""

    price_velocity_points: float | None
    """Deslocamento liquido em pontos por segundo (com sinal)."""

    price_path_points: float | None
    """Distancia PERCORRIDA em pontos (soma dos passos, sem sinal). Muito
    maior que o deslocamento liquido = mercado agitado sem sair do lugar."""

    efficiency: float | None
    """Deslocamento liquido / caminho percorrido (0-1). Perto de 1 =
    movimento direcional limpo; perto de 0 = vaivem."""

    spread_now_points: float | None
    spread_mean_points: float | None
    spread_max_points: float | None
    spread_trend: float | None
    """Spread medio recente / spread medio anterior. >1 alargando."""

    latency_seconds: float | None
    """Idade do ultimo tick em relacao a `now` — atraso do feed."""

    warnings: tuple[str, ...] = ()

    @property
    def is_reliable(self) -> bool:
        return self.tick_count >= MIN_TICKS_FOR_FLOW and self.ticks_per_second is not None


def _price(value: object) -> float:
    return float(value)  # type: ignore[arg-type]


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _empty(
    *, tick_count: int, window_seconds: float, warnings: tuple[str, ...]
) -> TickFlowMetrics:
    return TickFlowMetrics(
        tick_count=tick_count,
        window_seconds=window_seconds,
        ticks_per_second=None,
        tick_acceleration=None,
        mean_interval_ms=None,
        max_interval_ms=None,
        uptick_ratio=None,
        direction_bias=TickDirection.FLAT,
        price_velocity_points=None,
        price_path_points=None,
        efficiency=None,
        spread_now_points=None,
        spread_mean_points=None,
        spread_max_points=None,
        spread_trend=None,
        latency_seconds=None,
        warnings=warnings,
    )


def compute_tick_flow(
    ticks: Sequence[TickLike],
    *,
    point: float,
    now: datetime | None = None,
    uptick_bias_threshold: float = 0.58,
) -> TickFlowMetrics:
    """Calcula todas as metricas de fluxo em uma unica passada.

    `point` converte preco em PONTOS do simbolo — a unica unidade em que
    EURUSD e XAUUSD sao comparaveis, e a que o resto do motor usa.
    """
    count = len(ticks)
    if count == 0:
        return _empty(
            tick_count=0,
            window_seconds=0.0,
            warnings=("Nenhum tick na janela.",),
        )

    resolved_now = _as_utc(now or datetime.now(UTC))
    first_time = _as_utc(ticks[0].timestamp)
    last_time = _as_utc(ticks[-1].timestamp)
    window_seconds = max(0.0, (last_time - first_time).total_seconds())
    latency = max(0.0, (resolved_now - last_time).total_seconds())

    if count < MIN_TICKS_FOR_FLOW:
        return _empty(
            tick_count=count,
            window_seconds=window_seconds,
            warnings=(
                f"Apenas {count} tick(s) na janela (minimo {MIN_TICKS_FOR_FLOW}) — "
                "fluxo nao pode ser medido com honestidade.",
            ),
        )

    safe_point = point if point > 0 else 1.0

    upticks = 0
    downticks = 0
    path_points = 0.0
    intervals: list[float] = []
    spreads: list[float] = []

    previous_mid = (_price(ticks[0].bid) + _price(ticks[0].ask)) / 2
    previous_time = first_time
    spreads.append((_price(ticks[0].ask) - _price(ticks[0].bid)) / safe_point)

    for tick in ticks[1:]:
        bid = _price(tick.bid)
        ask = _price(tick.ask)
        mid = (bid + ask) / 2
        step = mid - previous_mid
        if step > 0:
            upticks += 1
        elif step < 0:
            downticks += 1
        path_points += abs(step) / safe_point

        current_time = _as_utc(tick.timestamp)
        intervals.append(max(0.0, (current_time - previous_time).total_seconds() * 1_000))

        spreads.append((ask - bid) / safe_point)
        previous_mid = mid
        previous_time = current_time

    first_mid = (_price(ticks[0].bid) + _price(ticks[0].ask)) / 2
    last_mid = (_price(ticks[-1].bid) + _price(ticks[-1].ask)) / 2
    displacement_points = (last_mid - first_mid) / safe_point

    warnings: list[str] = []
    if window_seconds <= 0:
        warnings.append(
            "Todos os ticks carimbados no mesmo instante — taxa por segundo "
            "indisponivel."
        )
        ticks_per_second = None
        velocity = None
    else:
        ticks_per_second = count / window_seconds
        velocity = displacement_points / window_seconds

    directional = upticks + downticks
    uptick_ratio = upticks / directional if directional else None
    if uptick_ratio is None:
        direction_bias = TickDirection.FLAT
    elif uptick_ratio >= uptick_bias_threshold:
        direction_bias = TickDirection.UP
    elif uptick_ratio <= 1 - uptick_bias_threshold:
        direction_bias = TickDirection.DOWN
    else:
        direction_bias = TickDirection.FLAT

    efficiency = (
        min(1.0, abs(displacement_points) / path_points) if path_points > 0 else None
    )

    half = count // 2
    acceleration = None
    spread_trend = None
    if half >= MIN_TICKS_PER_HALF:
        mid_time = _as_utc(ticks[half].timestamp)
        older_seconds = (mid_time - first_time).total_seconds()
        recent_seconds = (last_time - mid_time).total_seconds()
        if older_seconds > 0 and recent_seconds > 0:
            older_rate = half / older_seconds
            recent_rate = (count - half) / recent_seconds
            acceleration = recent_rate / older_rate if older_rate > 0 else None
        older_spread = sum(spreads[:half]) / half
        recent_spread = sum(spreads[half:]) / (count - half)
        spread_trend = recent_spread / older_spread if older_spread > 0 else None
    else:
        warnings.append(
            "Janela curta demais para comparar as duas metades — aceleracao e "
            "tendencia de spread indisponiveis."
        )

    return TickFlowMetrics(
        tick_count=count,
        window_seconds=window_seconds,
        ticks_per_second=ticks_per_second,
        tick_acceleration=acceleration,
        mean_interval_ms=sum(intervals) / len(intervals) if intervals else None,
        max_interval_ms=max(intervals) if intervals else None,
        uptick_ratio=uptick_ratio,
        direction_bias=direction_bias,
        price_velocity_points=velocity,
        price_path_points=path_points,
        efficiency=efficiency,
        spread_now_points=spreads[-1],
        spread_mean_points=sum(spreads) / len(spreads),
        spread_max_points=max(spreads),
        spread_trend=spread_trend,
        latency_seconds=latency,
        warnings=tuple(warnings),
    )

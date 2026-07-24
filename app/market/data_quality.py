"""Verificacoes de qualidade de dados de mercado (candles e ticks).

Cobre os itens exigidos pelo prompt mestre (secao 8) que sao viaveis de
checar de forma determinística a partir dos dados ja coletados: timestamps
duplicados, buracos, candles com OHLC inconsistente, preco/volume
invalidos, ticks fora de ordem, spread absurdo, atraso do feed e timestamps
no futuro (indicio de timezone incorreto).

Deliberadamente fora do escopo desta fase: divergencia entre candles e
ticks (exige alinhar janelas de coleta que ainda nao sao garantidas
sobrepostas nesta fase) e Hidden-Markov/estatisticas de regime (Fase 4).

As funcoes aqui sao puras (nao acessam banco nem MetaTrader) para serem
testáveis com dados sinteticos e reutilizaveis tanto antes de persistir
(sobre `RawCandle`/`RawTick`) quanto sobre dados ja no banco
(`Candle`/`Tick`, cujos campos numericos sao `Decimal`).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


class Severity(enum.StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    check: str
    severity: Severity
    message: str


class CandleLike(Protocol):
    """Somente leitura (properties, nao atributos simples): assim tanto
    `RawCandle.open: float` quanto `Candle.open: Decimal` satisfazem o
    protocolo covariantemente. Atributos simples exigiriam tipo identico
    (invariante), o que quebraria com `RawCandle`/`Candle` coexistindo."""

    @property
    def open_time(self) -> datetime: ...
    @property
    def open(self) -> float | Decimal: ...
    @property
    def high(self) -> float | Decimal: ...
    @property
    def low(self) -> float | Decimal: ...
    @property
    def close(self) -> float | Decimal: ...
    @property
    def tick_volume(self) -> int: ...


class TickLike(Protocol):
    @property
    def timestamp(self) -> datetime: ...
    @property
    def bid(self) -> float | Decimal: ...
    @property
    def ask(self) -> float | Decimal: ...
    @property
    def volume(self) -> float | Decimal: ...


# Fechamento normal de fim de semana no forex e ~48h (sexta ~21-22h UTC ate
# domingo ~21-22h UTC, dependendo da corretora) -- 40h fica com margem
# segura ABAIXO disso, para que o fechamento semanal de toda semana seja
# classificado como INFO (esperado), nao WARNING. Bug real, achado
# coletando ~7 meses de candles H1 reais (Fase 16): o valor antigo (55h)
# ficava ACIMA do fechamento normal de 48h, entao TODO fim de semana
# (44 ocorrencias no dataset real) virava WARNING, derrubando a nota de
# qualidade para 0/100 mesmo com os dados perfeitamente saudaveis -- nunca
# apareceu nos testes porque nenhum dataset sintetico anterior cruzava um
# fim de semana de verdade.
_WEEKEND_GAP_TOLERANCE_SECONDS = 40 * 60 * 60


def _f(value: float | Decimal) -> float:
    return float(value)


def _as_naive(value: datetime) -> datetime:
    """Normaliza para naive (assumindo UTC, convencao do projeto) antes de
    comparar/subtrair datetimes. Necessario porque o SQLite (e, na
    pratica, tambem o driver MySQL usado aqui) devolve `DateTime(timezone=
    True)` como naive na leitura -- sem isso, `quality check` (ticks
    relidos do banco, naive) comparados contra `datetime.now(UTC)` (aware)
    levantam `TypeError`. Bug real, achado rodando `quality check` contra
    dados reais persistidos (Fase 16) -- `collect ticks` nunca expos isso
    porque roda `check_ticks` sobre ticks recem-buscados do MetaTrader
    (aware nos dois lados)."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def check_candles(
    candles: Sequence[CandleLike], *, timeframe_seconds: int
) -> list[DataQualityIssue]:
    """Roda todas as checagens de candle: timestamps duplicados, buracos e
    consistencia de OHLC/volume. `candles` deve estar ordenada por
    `open_time` crescente (nao ordenamos aqui para nao mascarar um possivel
    problema de ordenacao na origem)."""
    issues: list[DataQualityIssue] = []
    issues.extend(_check_duplicate_open_times(candles))
    issues.extend(_check_candle_gaps(candles, timeframe_seconds))
    issues.extend(_check_ohlc_consistency(candles))
    return issues


def _check_duplicate_open_times(candles: Sequence[CandleLike]) -> list[DataQualityIssue]:
    seen: set[datetime] = set()
    duplicates = 0
    for candle in candles:
        if candle.open_time in seen:
            duplicates += 1
        seen.add(candle.open_time)

    if duplicates == 0:
        return []
    return [
        DataQualityIssue(
            check="duplicate_open_time",
            severity=Severity.WARNING,
            message=f"{duplicates} timestamp(s) de candle duplicado(s) no lote.",
        )
    ]


def _check_candle_gaps(
    candles: Sequence[CandleLike], timeframe_seconds: int
) -> list[DataQualityIssue]:
    if timeframe_seconds <= 0 or len(candles) < 2:
        return []

    issues: list[DataQualityIssue] = []
    for previous, current in zip(candles, candles[1:], strict=False):
        delta_seconds = (current.open_time - previous.open_time).total_seconds()
        if delta_seconds <= timeframe_seconds * 1.5:
            continue

        missing_bars = round(delta_seconds / timeframe_seconds) - 1
        severity = (
            Severity.INFO if delta_seconds > _WEEKEND_GAP_TOLERANCE_SECONDS else Severity.WARNING
        )
        issues.append(
            DataQualityIssue(
                check="candle_gap",
                severity=severity,
                message=(
                    f"gap de {missing_bars} candle(s) entre {previous.open_time.isoformat()} "
                    f"e {current.open_time.isoformat()}."
                ),
            )
        )
    return issues


def _check_ohlc_consistency(candles: Sequence[CandleLike]) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    for candle in candles:
        open_, high, low, close = (
            _f(candle.open),
            _f(candle.high),
            _f(candle.low),
            _f(candle.close),
        )

        if open_ <= 0 or high <= 0 or low <= 0 or close <= 0:
            issues.append(
                DataQualityIssue(
                    check="candle_invalid_price",
                    severity=Severity.CRITICAL,
                    message=f"preco <= 0 na candle de {candle.open_time.isoformat()}.",
                )
            )
            continue

        if high < low:
            issues.append(
                DataQualityIssue(
                    check="candle_high_below_low",
                    severity=Severity.CRITICAL,
                    message=f"high < low na candle de {candle.open_time.isoformat()}.",
                )
            )
            continue

        if not (low <= open_ <= high) or not (low <= close <= high):
            issues.append(
                DataQualityIssue(
                    check="candle_ohlc_out_of_range",
                    severity=Severity.CRITICAL,
                    message=(
                        f"open/close fora do intervalo [low, high] na candle de "
                        f"{candle.open_time.isoformat()}."
                    ),
                )
            )

        if candle.tick_volume < 0:
            issues.append(
                DataQualityIssue(
                    check="candle_invalid_volume",
                    severity=Severity.CRITICAL,
                    message=f"tick_volume negativo na candle de {candle.open_time.isoformat()}.",
                )
            )

    return issues


def check_ticks(
    ticks: Sequence[TickLike],
    *,
    point: float,
    max_spread_points: float,
    now: datetime,
    max_feed_delay_seconds: int,
    future_tolerance_seconds: int = 300,
) -> list[DataQualityIssue]:
    """Roda todas as checagens de tick: ordem, preco/volume invalido,
    spread absurdo, atraso do feed e timestamp no futuro."""
    issues: list[DataQualityIssue] = []
    issues.extend(_check_tick_order(ticks))
    issues.extend(
        _check_tick_prices_and_volume(ticks, point=point, max_spread_points=max_spread_points)
    )
    issues.extend(_check_future_timestamps(ticks, now, future_tolerance_seconds))
    issues.extend(_check_feed_delay(ticks, now, max_feed_delay_seconds))
    return issues


def _check_tick_order(ticks: Sequence[TickLike]) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    for previous, current in zip(ticks, ticks[1:], strict=False):
        if current.timestamp < previous.timestamp:
            issues.append(
                DataQualityIssue(
                    check="tick_out_of_order",
                    severity=Severity.CRITICAL,
                    message=(
                        f"tick em {current.timestamp.isoformat()} vem antes do tick anterior "
                        f"({previous.timestamp.isoformat()})."
                    ),
                )
            )
    return issues


def _check_tick_prices_and_volume(
    ticks: Sequence[TickLike], *, point: float, max_spread_points: float
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    for tick in ticks:
        bid, ask, volume = _f(tick.bid), _f(tick.ask), _f(tick.volume)

        if bid <= 0 or ask <= 0:
            issues.append(
                DataQualityIssue(
                    check="tick_invalid_price",
                    severity=Severity.CRITICAL,
                    message=f"bid/ask <= 0 no tick de {tick.timestamp.isoformat()}.",
                )
            )
            continue

        if ask < bid:
            issues.append(
                DataQualityIssue(
                    check="tick_negative_spread",
                    severity=Severity.CRITICAL,
                    message=f"ask < bid no tick de {tick.timestamp.isoformat()}.",
                )
            )
        elif point > 0:
            spread_points = (ask - bid) / point
            if spread_points > max_spread_points:
                issues.append(
                    DataQualityIssue(
                        check="tick_spread_too_wide",
                        severity=Severity.WARNING,
                        message=(
                            f"spread de {spread_points:.1f} pontos no tick de "
                            f"{tick.timestamp.isoformat()} excede o limite de {max_spread_points}."
                        ),
                    )
                )

        if volume < 0:
            issues.append(
                DataQualityIssue(
                    check="tick_invalid_volume",
                    severity=Severity.CRITICAL,
                    message=f"volume negativo no tick de {tick.timestamp.isoformat()}.",
                )
            )

    return issues


def _check_future_timestamps(
    ticks: Sequence[TickLike], now: datetime, tolerance_seconds: int
) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    now = _as_naive(now)
    for tick in ticks:
        if (_as_naive(tick.timestamp) - now).total_seconds() > tolerance_seconds:
            issues.append(
                DataQualityIssue(
                    check="tick_timestamp_in_future",
                    severity=Severity.CRITICAL,
                    message=(
                        f"tick com timestamp no futuro ({tick.timestamp.isoformat()}) — "
                        "possivel timezone incorreto na origem."
                    ),
                )
            )
    return issues


def _check_feed_delay(
    ticks: Sequence[TickLike], now: datetime, max_delay_seconds: int
) -> list[DataQualityIssue]:
    if not ticks:
        return []
    last_timestamp = _as_naive(max(tick.timestamp for tick in ticks))
    delay_seconds = (_as_naive(now) - last_timestamp).total_seconds()
    if delay_seconds <= max_delay_seconds:
        return []
    return [
        DataQualityIssue(
            check="feed_delay",
            severity=Severity.WARNING,
            message=f"ultimo tick tem {delay_seconds:.0f}s de atraso em relacao a agora.",
        )
    ]


def compute_score(issues: Sequence[DataQualityIssue]) -> int:
    """Nota de 0 a 100. CRITICAL pesa mais que WARNING, que pesa mais que
    INFO — nunca escondida atras de um unico numero sem as ocorrencias
    detalhadas (ver `DataQualityIssue`), conforme exigido pelo prompt
    mestre (nunca esconder metricas individuais atras de um score)."""
    penalty = sum(
        {Severity.CRITICAL: 15, Severity.WARNING: 5, Severity.INFO: 1}[issue.severity]
        for issue in issues
    )
    return max(0, 100 - penalty)


def is_acceptable(issues: Sequence[DataQualityIssue], *, min_score: int) -> bool:
    """Gate de qualidade: reprovado se houver qualquer ocorrencia CRITICAL
    ou se a nota ficar abaixo de `min_score`. Usado (em fases futuras) para
    proibir treinamento/operacao quando os dados nao sao confiaveis."""
    if any(issue.severity == Severity.CRITICAL for issue in issues):
        return False
    return compute_score(issues) >= min_score

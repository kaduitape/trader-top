"""Correlacao entre instrumentos e controle de exposicao.

Abrir EURUSD, GBPUSD e AUDUSD ao mesmo tempo nao e diversificacao: e a
MESMA aposta contra o dolar, feita tres vezes, com tres vezes o risco. O
sistema tinha um limite de posicoes simultaneas, mas ele contava POSICOES
sem saber que aquelas tres eram uma so — e um scanner que varre o mercado
inteiro multiplica exatamente esse erro, porque tende a achar o mesmo sinal
em varios pares da mesma moeda ao mesmo tempo.

Aqui a correlacao e MEDIDA, nao presumida: vem dos retornos dos candles que
ja estao no banco. Nada de tabela fixa de "pares correlacionados" — a
correlacao muda com o regime, e uma tabela escrita a mao envelhece sem
avisar.

Duas decisoes que valem explicar:

- Usa **retornos logaritmicos**, nao precos. Precos de dois ativos em
  tendencia sao quase sempre correlacionados por construcao; o que importa
  para risco e se eles se movem JUNTOS no dia a dia.
- Sem amostra suficiente, devolve `None` em vez de zero. Zero significaria
  "medi e sao independentes", e nao e isso que aconteceu.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository

MIN_SAMPLES = 60
"""Abaixo disso a correlacao e ruido com aparencia de numero."""

DEFAULT_MAX_CORRELATION = 0.70
"""|r| acima disso trata os instrumentos como a mesma aposta."""


@dataclass(frozen=True, slots=True)
class CorrelationResult:
    symbol_a: str
    symbol_b: str
    coefficient: float | None
    samples: int

    @property
    def measured(self) -> bool:
        return self.coefficient is not None

    def is_same_bet(self, threshold: float = DEFAULT_MAX_CORRELATION) -> bool:
        """Correlacao NEGATIVA forte tambem conta.

        Comprar EURUSD e vender USDCHF sao a mesma aposta contra o dolar,
        ainda que os pares andem em sentidos opostos. Por isso o modulo.
        """
        return self.coefficient is not None and abs(self.coefficient) >= threshold


def log_returns(closes: list[float]) -> list[float]:
    retornos: list[float] = []
    for anterior, atual in zip(closes, closes[1:], strict=False):
        if anterior > 0 and atual > 0:
            retornos.append(math.log(atual / anterior))
    return retornos


def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Correlacao de Pearson, ou None quando nao da para medir."""
    n = min(len(xs), len(ys))
    if n < MIN_SAMPLES:
        return None
    xs, ys = xs[-n:], ys[-n:]

    media_x = sum(xs) / n
    media_y = sum(ys) / n
    cov = sum((x - media_x) * (y - media_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - media_x) ** 2 for x in xs)
    var_y = sum((y - media_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        # Serie constante: correlacao indefinida. Devolver 0 diria
        # "independentes", o que e uma afirmacao que ninguem mediu.
        return None
    return cov / math.sqrt(var_x * var_y)


def correlate(
    session: Session,
    *,
    symbol_a: str,
    symbol_b: str,
    timeframe: str = "M15",
    bars: int = 300,
    as_of: datetime | None = None,
) -> CorrelationResult:
    """Correlacao entre dois instrumentos, a partir do banco.

    Alinha as series pelo horario de abertura: comparar a i-esima barra de
    cada um daria resultado errado sempre que um dos simbolos tiver uma
    lacuna de coleta — e lacuna acontece.
    """
    repo_symbols = SymbolRepository(session)
    repo_candles = CandleRepository(session)

    linha_a = repo_symbols.get_by_name(symbol_a)
    linha_b = repo_symbols.get_by_name(symbol_b)
    if linha_a is None or linha_b is None:
        return CorrelationResult(symbol_a, symbol_b, None, 0)

    candles_a = repo_candles.get_recent(linha_a.id, timeframe, bars, as_of=as_of)
    candles_b = repo_candles.get_recent(linha_b.id, timeframe, bars, as_of=as_of)

    por_horario_a = {candle.open_time: float(candle.close) for candle in candles_a}
    por_horario_b = {candle.open_time: float(candle.close) for candle in candles_b}
    horarios = sorted(set(por_horario_a) & set(por_horario_b))
    if len(horarios) < MIN_SAMPLES + 1:
        return CorrelationResult(symbol_a, symbol_b, None, len(horarios))

    retornos_a = log_returns([por_horario_a[h] for h in horarios])
    retornos_b = log_returns([por_horario_b[h] for h in horarios])
    coeficiente = pearson(retornos_a, retornos_b)
    return CorrelationResult(
        symbol_a, symbol_b, coeficiente, min(len(retornos_a), len(retornos_b))
    )


@dataclass(frozen=True, slots=True)
class ExposureVerdict:
    allowed: bool
    reason: str
    conflicting_symbol: str | None = None
    coefficient: float | None = None


def check_exposure(
    session: Session,
    *,
    candidate: str,
    open_symbols: list[str],
    threshold: float = DEFAULT_MAX_CORRELATION,
    timeframe: str = "M15",
    as_of: datetime | None = None,
) -> ExposureVerdict:
    """Recusa uma entrada que repete uma aposta ja aberta.

    Nao mede correlacao quando nao ha posicao aberta — a consulta custa
    banco e nao mudaria nada.
    """
    if not open_symbols:
        return ExposureVerdict(allowed=True, reason="Nenhuma posicao aberta.")

    if candidate in open_symbols:
        return ExposureVerdict(
            allowed=False,
            reason=f"Ja existe posicao aberta em {candidate}.",
            conflicting_symbol=candidate,
            coefficient=1.0,
        )

    for aberto in open_symbols:
        resultado = correlate(
            session,
            symbol_a=candidate,
            symbol_b=aberto,
            timeframe=timeframe,
            as_of=as_of,
        )
        if resultado.is_same_bet(threshold):
            sentido = "mesma direcao" if (resultado.coefficient or 0) > 0 else "espelhada"
            return ExposureVerdict(
                allowed=False,
                reason=(
                    f"{candidate} e {aberto} se movem juntos ({sentido}, "
                    f"correlacao {resultado.coefficient:+.2f}) — seria a mesma "
                    "aposta duas vezes, com o dobro do risco."
                ),
                conflicting_symbol=aberto,
                coefficient=resultado.coefficient,
            )

    return ExposureVerdict(
        allowed=True, reason="Nenhuma correlacao relevante com o que ja esta aberto."
    )

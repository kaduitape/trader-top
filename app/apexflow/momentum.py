"""Momentum Engine: forca, aceleracao, exaustao e persistencia.

Momentum aqui NAO e "o RSI subiu". E a leitura combinada de tres coisas
que so juntas contam a historia:

- **preco** — quanto o mercado andou, em pontos de ATR (unidade comparavel
  entre pares);
- **fluxo** — os ticks estao acelerando ou perdendo forca;
- **consistencia** — o movimento e limpo (eficiencia alta) ou vaivem.

A distincao que mais importa e entre CONTINUIDADE e EXAUSTAO: os dois
aparecem como "movimento forte", mas um pede entrada a favor e o outro
pede ficar de fora. Aqui a exaustao e caracterizada por movimento amplo
com fluxo desacelerando e eficiencia caindo — o preco ainda anda, mas
ninguem mais esta empurrando.

Modulo puro sobre a matriz de features ja calculada
(`app.market.features.build_candle_features`) mais as metricas de tick.
Nenhuma formula de indicador e recalculada aqui.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import pandas as pd

from app.apexflow.tick_flow import TickDirection, TickFlowMetrics


class MomentumState(enum.StrEnum):
    ACCELERATING = "ACCELERATING"
    STEADY = "STEADY"
    DECELERATING = "DECELERATING"
    EXHAUSTED = "EXHAUSTED"
    FLAT = "FLAT"
    UNKNOWN = "UNKNOWN"


MOMENTUM_STATE_LABELS: dict[MomentumState, str] = {
    MomentumState.ACCELERATING: "Acelerando",
    MomentumState.STEADY: "Constante",
    MomentumState.DECELERATING: "Perdendo forca",
    MomentumState.EXHAUSTED: "Exaustao",
    MomentumState.FLAT: "Sem movimento",
    MomentumState.UNKNOWN: "Sem dados",
}

STRONG_MOVE_ATR = 1.2
"""Deslocamento acima de 1.2 ATR na janela ja e movimento amplo."""

FLAT_MOVE_ATR = 0.25
ACCELERATION_UP = 1.25
ACCELERATION_DOWN = 0.80
LOW_EFFICIENCY = 0.35
PERSISTENCE_BARS = 5


@dataclass(frozen=True, slots=True)
class MomentumReading:
    state: MomentumState
    direction: int
    """`TickDirection`: sentido do impulso dominante."""

    strength_atr: float | None
    """Deslocamento das ultimas barras em multiplos de ATR."""

    impulse_points: float | None
    acceleration: float | None
    efficiency: float | None
    persistence: float | None
    """Fracao das ultimas `PERSISTENCE_BARS` barras que fecharam no mesmo
    sentido (0-1) — mede continuidade, nao tamanho."""

    speed_change: float | None
    direction_change: bool
    reasons: tuple[str, ...]

    @property
    def label(self) -> str:
        return MOMENTUM_STATE_LABELS[self.state]

    @property
    def favours_continuation(self) -> bool:
        return self.state in (MomentumState.ACCELERATING, MomentumState.STEADY)


def _last(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    return None if pd.isna(value) else float(value)


def read_momentum(
    features: pd.DataFrame,
    flow: TickFlowMetrics,
    *,
    lookback_bars: int = 5,
) -> MomentumReading:
    """Le o momentum das ultimas `lookback_bars` barras somado ao fluxo.

    Devolve `UNKNOWN` (nunca um palpite) quando faltam barras ou ATR.
    """
    required = {"close", "atr_14"}
    if features.empty or not required <= set(features.columns):
        return MomentumReading(
            state=MomentumState.UNKNOWN,
            direction=TickDirection.FLAT,
            strength_atr=None,
            impulse_points=None,
            acceleration=flow.tick_acceleration,
            efficiency=flow.efficiency,
            persistence=None,
            speed_change=None,
            direction_change=False,
            reasons=("Matriz de features sem close/ATR — momentum indisponivel.",),
        )

    window = features.tail(lookback_bars + 1)
    if len(window) < 2:
        return MomentumReading(
            state=MomentumState.UNKNOWN,
            direction=TickDirection.FLAT,
            strength_atr=None,
            impulse_points=None,
            acceleration=flow.tick_acceleration,
            efficiency=flow.efficiency,
            persistence=None,
            speed_change=None,
            direction_change=False,
            reasons=(
                f"Apenas {len(window)} barra(s) disponivel(is) — momentum exige "
                "pelo menos duas.",
            ),
        )

    atr = _last(window["atr_14"])
    closes = window["close"].astype(float)
    impulse = float(closes.iloc[-1] - closes.iloc[0])
    strength_atr = abs(impulse) / atr if atr and atr > 0 else None

    steps = closes.diff().dropna()
    ups = int((steps > 0).sum())
    downs = int((steps < 0).sum())
    total = ups + downs
    persistence = max(ups, downs) / total if total else None

    if impulse > 0:
        direction = TickDirection.UP
    elif impulse < 0:
        direction = TickDirection.DOWN
    else:
        direction = TickDirection.FLAT

    recent_step = float(steps.iloc[-1]) if not steps.empty else 0.0
    previous_step = float(steps.iloc[-2]) if len(steps) >= 2 else 0.0
    speed_change = (
        abs(recent_step) / abs(previous_step) if abs(previous_step) > 0 else None
    )
    direction_change = recent_step * previous_step < 0

    reasons: list[str] = []
    acceleration = flow.tick_acceleration
    efficiency = flow.efficiency

    if strength_atr is None:
        state = MomentumState.UNKNOWN
        reasons.append("ATR indisponivel — a forca do movimento nao pode ser escalada.")
    elif strength_atr < FLAT_MOVE_ATR:
        state = MomentumState.FLAT
        reasons.append(
            f"Deslocamento de apenas {strength_atr:.2f} ATR nas ultimas "
            f"{lookback_bars} barras — mercado parado."
        )
    elif (
        strength_atr >= STRONG_MOVE_ATR
        and acceleration is not None
        and acceleration < ACCELERATION_DOWN
        and (efficiency is None or efficiency < LOW_EFFICIENCY)
    ):
        state = MomentumState.EXHAUSTED
        reasons.append(
            f"Movimento amplo ({strength_atr:.2f} ATR) com fluxo desacelerando "
            f"({acceleration:.2f}x) e trajeto ineficiente — sinal de exaustao, "
            "nao de continuidade."
        )
    elif acceleration is not None and acceleration >= ACCELERATION_UP:
        state = MomentumState.ACCELERATING
        reasons.append(
            f"Fluxo de ticks {acceleration:.2f}x mais rapido na segunda metade "
            f"da janela, com {strength_atr:.2f} ATR de deslocamento."
        )
    elif acceleration is not None and acceleration < ACCELERATION_DOWN:
        state = MomentumState.DECELERATING
        reasons.append(
            f"Fluxo perdendo forca ({acceleration:.2f}x) — o movimento existe, "
            "mas nao esta sendo alimentado."
        )
    else:
        state = MomentumState.STEADY
        reasons.append(
            f"Movimento de {strength_atr:.2f} ATR com fluxo estavel."
        )

    if persistence is not None:
        reasons.append(
            f"{persistence * 100:.0f}% das ultimas barras fecharam no mesmo sentido."
        )
    if direction_change:
        reasons.append("A ultima barra inverteu o sentido da anterior.")
    if efficiency is not None and efficiency < LOW_EFFICIENCY:
        reasons.append(
            f"Eficiencia do trajeto baixa ({efficiency:.2f}): muito vaivem para "
            "pouco deslocamento liquido."
        )

    return MomentumReading(
        state=state,
        direction=direction,
        strength_atr=strength_atr,
        impulse_points=impulse,
        acceleration=acceleration,
        efficiency=efficiency,
        persistence=persistence,
        speed_change=speed_change,
        direction_change=direction_change,
        reasons=tuple(reasons),
    )

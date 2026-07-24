"""Estrategia E do prompt mestre (secao 11): momentum.

"Confirmacao do timeframe superior" e "velocidade de ticks" ficam fora do
escopo desta fase — a primeira exige juntar duas series de timeframes
diferentes (Estrategia I, multi-timeframe); a segunda exige o modulo de
microestrutura de tick (Estrategia H). Ambas sao citadas explicitamente no
prompt mestre como parte desta estrategia, mas nenhuma tem infraestrutura
pronta ainda — ver `docs/features.md`.

"Evitar entrar quando o movimento estiver excessivamente estendido" e
implementado literalmente via um teto no z-score: nao compra/vende se o
preco ja estiver a mais de `max_zscore_extension` desvios-padrao da media.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.risk_helpers import atr_stop_and_target, is_nan


@dataclass(frozen=True, slots=True)
class MomentumContinuationConfig:
    min_streak: int = 3
    volume_expansion_threshold: float = 1.2
    max_zscore_extension: float = 2.5
    atr_multiplier_stop: float = 1.5
    risk_reward_ratio: float = 1.5
    validity_bars: int = 1


class MomentumContinuationStrategy(Strategy):
    """Compra/vende na continuacao de um movimento com aceleracao de preco
    (ROC crescente), volume em expansao e uma sequencia de candles na
    mesma direcao — desde que o movimento ainda nao esteja excessivamente
    estendido (z-score dentro do limite)."""

    name = "momentum_continuation"

    def __init__(
        self, config: MomentumContinuationConfig, *, point: float, bar_seconds: int
    ) -> None:
        self._config = config
        self._point = point
        self._bar_seconds = bar_seconds

    def generate_signal(self, state: MarketState) -> Signal | None:
        previous = state.previous
        if previous is None:
            return None

        current = state.current
        cfg = self._config
        roc_now, roc_prev = current["roc_10"], previous["roc_10"]
        relative_volume = current["relative_volume_20"]
        zscore_now = current["zscore_20"]
        streak = current["candle_streak"]
        atr = current["atr_14"]

        if any(is_nan(v) for v in (roc_now, roc_prev, relative_volume, zscore_now, streak, atr)):
            return None
        if abs(zscore_now) > cfg.max_zscore_extension:
            return None
        if relative_volume < cfg.volume_expansion_threshold:
            return None

        direction: SignalDirection | None = None
        reason = ""

        if roc_now > 0 and roc_now > roc_prev and streak >= cfg.min_streak:
            direction = SignalDirection.LONG
            reason = (
                f"momentum de alta acelerando (ROC {roc_prev:.3f} -> {roc_now:.3f}), "
                f"sequencia de {int(streak)} candle(s) de alta, volume relativo {relative_volume:.2f}"
            )
        elif roc_now < 0 and roc_now < roc_prev and streak <= -cfg.min_streak:
            direction = SignalDirection.SHORT
            reason = (
                f"momentum de baixa acelerando (ROC {roc_prev:.3f} -> {roc_now:.3f}), "
                f"sequencia de {int(-streak)} candle(s) de baixa, volume relativo {relative_volume:.2f}"
            )

        if direction is None:
            return None

        reference_price = float(current["close"])
        stop_loss, take_profit = atr_stop_and_target(
            reference_price,
            direction,
            float(atr),
            atr_multiplier_stop=cfg.atr_multiplier_stop,
            risk_reward_ratio=cfg.risk_reward_ratio,
        )
        generated_at = current["open_time"]

        return Signal(
            symbol=state.symbol,
            strategy_name=self.name,
            direction=direction,
            generated_at=generated_at,
            reference_price=reference_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            valid_until=generated_at + timedelta(seconds=self._bar_seconds * cfg.validity_bars),
            reason=reason,
            regime_required="nenhum filtro de regime — apenas limite de extensao via z-score",
            confidence=0.5,
            features_used={
                "roc_10": float(roc_now),
                "relative_volume_20": float(relative_volume),
                "zscore_20": float(zscore_now),
                "candle_streak": float(streak),
                "atr_14": float(atr),
            },
        )

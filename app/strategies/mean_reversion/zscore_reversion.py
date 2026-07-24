"""Estrategia D do prompt mestre (secao 11): retorno a media.

"Proibir em tendencia forte" e aplicado literalmente: so opera quando o
regime classifica o mercado como `SIDEWAYS`. O alvo e a propria media
(banda media de Bollinger, equivalente a SMA-20) — nao um multiplo fixo de
risco/retorno como nas outras estrategias, porque a tese aqui e
especificamente "o preco volta para a media", nao um R multiple generico.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.market.regimes import Trend
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.risk_helpers import is_nan


@dataclass(frozen=True, slots=True)
class ZScoreMeanReversionConfig:
    entry_zscore_threshold: float = 2.0
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    atr_multiplier_stop: float = 1.0
    validity_bars: int = 1


class ZScoreMeanReversionStrategy(Strategy):
    """Compra quando o preco esta estatisticamente esticado para baixo
    (zscore muito negativo), com sinais de exaustao (corpo do candle
    encolhendo, RSI sobrevendido comecando a subir) e mercado lateral;
    vende no cenario espelhado. Nunca opera em tendencia forte."""

    name = "zscore_mean_reversion"

    def __init__(
        self, config: ZScoreMeanReversionConfig, *, point: float, bar_seconds: int
    ) -> None:
        self._config = config
        self._point = point
        self._bar_seconds = bar_seconds

    def generate_signal(self, state: MarketState) -> Signal | None:
        previous = state.previous
        if previous is None or state.regime is None:
            return None

        regime = state.regime
        if regime.trend != Trend.SIDEWAYS or not regime.spread_adequate:
            return None

        current = state.current
        cfg = self._config
        zscore_now = current["zscore_20"]
        rsi_now, rsi_prev = current["rsi_14"], previous["rsi_14"]
        body_now, body_prev = current["candle_body"], previous["candle_body"]
        atr = current["atr_14"]
        mean_reference = current["bollinger_middle"]

        if any(
            is_nan(v)
            for v in (zscore_now, rsi_now, rsi_prev, body_now, body_prev, atr, mean_reference)
        ):
            return None

        is_decelerating = body_now < body_prev
        direction: SignalDirection | None = None
        reason = ""

        if (
            zscore_now <= -cfg.entry_zscore_threshold
            and rsi_now <= cfg.rsi_oversold
            and rsi_now > rsi_prev
            and is_decelerating
        ):
            direction = SignalDirection.LONG
            reason = (
                f"desvio estatistico (zscore={zscore_now:.2f}), RSI sobrevendido "
                f"recuperando ({rsi_prev:.1f} -> {rsi_now:.1f}) e candle desacelerando"
            )
        elif (
            zscore_now >= cfg.entry_zscore_threshold
            and rsi_now >= cfg.rsi_overbought
            and rsi_now < rsi_prev
            and is_decelerating
        ):
            direction = SignalDirection.SHORT
            reason = (
                f"desvio estatistico (zscore={zscore_now:.2f}), RSI sobrecomprado "
                f"recuperando ({rsi_prev:.1f} -> {rsi_now:.1f}) e candle desacelerando"
            )

        if direction is None:
            return None

        reference_price = float(current["close"])
        stop_distance = float(atr) * cfg.atr_multiplier_stop
        if direction == SignalDirection.LONG:
            stop_loss = reference_price - stop_distance
        else:
            stop_loss = reference_price + stop_distance
        take_profit = float(mean_reference)
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
            regime_required="lateral (SIDEWAYS) + spread adequado; proibida em tendencia forte",
            confidence=0.5,
            features_used={
                "zscore_20": float(zscore_now),
                "rsi_14": float(rsi_now),
                "candle_body": float(body_now),
                "atr_14": float(atr),
            },
        )

"""Estrategia C do prompt mestre (secao 11): rompimento de consolidacao.

Variante implementada nesta fase: **entrada apos fechamento** (a mais
conservadora das quatro variantes citadas no prompt mestre — imediata,
apos fechamento, reteste, ordem stop simulada). As demais variantes ficam
para quando os testes de robustez (Fase 9) compararem qual delas realmente
importa; implementar todas agora violaria a diretriz de nao construir
"todas as variacoes de uma vez".

Compressao de volatilidade e medida pela largura das Bandas de Bollinger
em relacao ao minimo recente — nao pelo campo `volatility` do regime (que
usa volatilidade REALIZADA, nao a largura das bandas; sao proxies
diferentes e a largura de banda e mais direta para "faixa estreita").

Confirmacao por tick (prompt mestre) fica fora do escopo — depende do
modulo de microestrutura (Estrategia H), ainda nao implementado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from app.strategies.base import MarketState, Signal, SignalDirection, Strategy
from app.strategies.risk_helpers import atr_stop_and_target, is_nan


@dataclass(frozen=True, slots=True)
class RangeBreakoutConfig:
    range_window: int = 20
    compression_window: int = 50
    compression_ratio: float = 1.10
    min_breakout_atr_multiple: float = 0.1
    volume_expansion_threshold: float = 1.3
    atr_multiplier_stop: float = 1.5
    risk_reward_ratio: float = 2.0
    validity_bars: int = 1


class RangeBreakoutStrategy(Strategy):
    """Compra/vende quando o fechamento rompe a máxima/mínima das ultimas
    `range_window` barras (excluindo a barra atual), desde que a
    volatilidade estivesse comprimida (largura de banda proxima do minimo
    recente) e o volume esteja em expansao."""

    name = "range_breakout"

    def __init__(self, config: RangeBreakoutConfig, *, point: float, bar_seconds: int) -> None:
        self._config = config
        self._point = point
        self._bar_seconds = bar_seconds

    def generate_signal(self, state: MarketState) -> Signal | None:
        cfg = self._config
        features = state.features
        required_bars = cfg.range_window + cfg.compression_window + 1
        if len(features) < required_bars or state.regime is None:
            return None

        current = state.current
        close, high, low = current["close"], current["high"], current["low"]
        atr = current["atr_14"]
        relative_volume = current["relative_volume_20"]
        bollinger_upper = current["bollinger_upper"]
        bollinger_lower = current["bollinger_lower"]
        bollinger_middle = current["bollinger_middle"]

        if any(
            is_nan(v)
            for v in (
                close,
                high,
                low,
                atr,
                relative_volume,
                bollinger_upper,
                bollinger_lower,
                bollinger_middle,
            )
        ):
            return None
        if bollinger_middle == 0 or not state.regime.spread_adequate:
            return None

        # Janela de N barras ANTERIORES a atual — a propria barra de
        # rompimento nunca entra na definicao do range que ela rompeu.
        window = features.iloc[-(cfg.range_window + 1) : -1]
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())

        band_width_series = (features["bollinger_upper"] - features["bollinger_lower"]) / features[
            "bollinger_middle"
        ]
        recent_band_widths = band_width_series.iloc[-(cfg.compression_window + 1) : -1].dropna()
        if recent_band_widths.empty:
            return None

        min_recent_band_width = float(recent_band_widths.min())
        current_band_width = (bollinger_upper - bollinger_lower) / bollinger_middle
        is_compressed = current_band_width <= min_recent_band_width * cfg.compression_ratio

        if not is_compressed or relative_volume < cfg.volume_expansion_threshold:
            return None

        min_distance = cfg.min_breakout_atr_multiple * atr
        direction: SignalDirection | None = None
        reason = ""

        if close > range_high + min_distance:
            direction = SignalDirection.LONG
            reason = (
                f"rompimento acima de {range_high:.5f} (fechamento {close:.5f}) apos "
                f"compressao de volatilidade, volume relativo {relative_volume:.2f}"
            )
        elif close < range_low - min_distance:
            direction = SignalDirection.SHORT
            reason = (
                f"rompimento abaixo de {range_low:.5f} (fechamento {close:.5f}) apos "
                f"compressao de volatilidade, volume relativo {relative_volume:.2f}"
            )

        if direction is None:
            return None

        reference_price = float(close)
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
            regime_required="spread adequado (sem filtro de tendencia — o rompimento pode iniciar uma)",
            confidence=0.5,
            features_used={
                "range_high": range_high,
                "range_low": range_low,
                "relative_volume_20": float(relative_volume),
                "atr_14": float(atr),
            },
        )

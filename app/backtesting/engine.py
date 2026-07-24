"""Motor de backtest por candle (Fase 5).

Regras inegociaveis aplicadas aqui, por exigencia do prompt mestre:

- **Nunca escolhe o resultado favoravel.** Quando o stop-loss e o
  take-profit caem dentro do intervalo `[low, high]` da mesma candle, e
  impossivel saber pela OHLC qual foi atingido primeiro — o motor sempre
  assume o stop-loss (o pior cenario), nunca o alvo.
- **Sem dados futuros.** Um sinal gerado na barra `t` so e executado na
  abertura da barra `t + entry_delay_bars` (padrao 1) — nunca no fechamento
  da propria barra `t`, que e quando o sinal foi gerado.
- **Custos sempre presentes.** Spread (do proprio candle, ou fixo),
  slippage e comissao sao aplicados a toda entrada e saida (ver
  `app.backtesting.costs`) — nunca um backtest sem custos.

Simplificacoes explicitas desta fase (documentadas, nao escondidas):
- Uma unica posicao aberta por vez (sem pilha de posicoes concorrentes).
- Volume fixo por trade (dimensionamento de risco real e Fase 17).
- PnL assume conta na moeda de cotacao do simbolo (sem conversao cambial).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.backtesting.costs import CostModel, apply_entry_cost, apply_exit_cost, commission_cost
from app.market.features import CandleFeatureLike, build_candle_features
from app.market.regimes import MarketRegime, classify_regime_series, regime_from_row
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    strategy_name: str
    signal_id: str
    direction: SignalDirection
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    stop_loss: float
    take_profit: float
    volume: float
    gross_pnl: float
    commission: float
    spread_and_slippage_cost: float
    net_pnl: float
    exit_reason: str
    bars_held: int
    mae: float
    mfe: float
    regime_at_entry: MarketRegime | None


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    volume: float = 0.01
    entry_delay_bars: int = 1
    cost_model: CostModel = field(default_factory=CostModel)


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    timeframe: str
    strategy_name: str
    initial_balance: float
    trades: list[Trade]
    equity_curve: pd.Series


@dataclass(frozen=True, slots=True)
class _OpenPosition:
    direction: SignalDirection
    signal_id: str
    reference_price: float
    entry_price: float
    entry_time: datetime
    entry_index: int
    stop_loss: float
    take_profit: float
    regime_at_entry: MarketRegime | None


@dataclass(frozen=True, slots=True)
class _PendingSignal:
    signal: Signal
    execute_at_index: int
    regime_at_signal: MarketRegime | None


def _check_stop_and_target(
    direction: SignalDirection, stop_loss: float, take_profit: float, low: float, high: float
) -> tuple[bool, bool]:
    if direction == SignalDirection.LONG:
        return low <= stop_loss, high >= take_profit
    return high >= stop_loss, low <= take_profit


def _signed(direction: SignalDirection) -> int:
    return 1 if direction == SignalDirection.LONG else -1


class CandleBacktestEngine:
    """Roda uma unica estrategia contra uma serie de candles de um simbolo
    e timeframe. Determinístico: mesma entrada sempre produz o mesmo
    `BacktestResult` (nenhuma aleatoriedade, nenhum estado global)."""

    def __init__(
        self,
        strategy: Strategy,
        config: BacktestConfig,
        *,
        point: float,
        contract_size: float,
        initial_balance: float = 10_000.0,
    ) -> None:
        self._strategy = strategy
        self._config = config
        self._point = point
        self._contract_size = contract_size
        self._initial_balance = initial_balance

    def run(
        self, candles: Sequence[CandleFeatureLike], *, symbol: str, timeframe: str
    ) -> BacktestResult:
        n = len(candles)
        if n == 0:
            return BacktestResult(
                symbol=symbol,
                timeframe=timeframe,
                strategy_name=self._strategy.name,
                initial_balance=self._initial_balance,
                trades=[],
                equity_curve=pd.Series(dtype=float),
            )

        features = build_candle_features(candles, point=self._point)
        regimes = classify_regime_series(features)

        balance = self._initial_balance
        trades: list[Trade] = []
        equity_values: list[float] = []
        equity_index: list[datetime] = []

        open_position: _OpenPosition | None = None
        pending_signal: _PendingSignal | None = None
        running_mae = 0.0
        running_mfe = 0.0

        volume = self._config.volume
        contract_size = self._contract_size
        cost_model = self._config.cost_model

        for i in range(n):
            candle = candles[i]
            low, high, close = float(candle.low), float(candle.high), float(candle.close)

            if open_position is not None:
                if open_position.direction == SignalDirection.LONG:
                    adverse = open_position.entry_price - low
                    favorable = high - open_position.entry_price
                else:
                    adverse = high - open_position.entry_price
                    favorable = open_position.entry_price - low
                running_mae = max(running_mae, adverse)
                running_mfe = max(running_mfe, favorable)

                stop_hit, target_hit = _check_stop_and_target(
                    open_position.direction,
                    open_position.stop_loss,
                    open_position.take_profit,
                    low,
                    high,
                )
                is_last_bar = i == n - 1

                if stop_hit or target_hit or is_last_bar:
                    if stop_hit:
                        # Conservador por construcao: se AMBOS foram atingidos na
                        # mesma candle, o stop (pior cenario) sempre vence.
                        exit_reason = "stop_loss"
                        raw_exit_price = open_position.stop_loss
                    elif target_hit:
                        exit_reason = "take_profit"
                        raw_exit_price = open_position.take_profit
                    else:
                        exit_reason = "end_of_data"
                        raw_exit_price = close

                    exit_price = apply_exit_cost(
                        raw_exit_price,
                        open_position.direction,
                        model=cost_model,
                        candle_spread_points=candle.spread,
                        point=self._point,
                    )
                    sign = _signed(open_position.direction)
                    gross_pnl = (
                        (exit_price - open_position.entry_price) * sign * volume * contract_size
                    )
                    no_cost_gross_pnl = (
                        (raw_exit_price - open_position.reference_price)
                        * sign
                        * volume
                        * contract_size
                    )
                    spread_and_slippage_cost = no_cost_gross_pnl - gross_pnl
                    commission = commission_cost(cost_model, volume)
                    net_pnl = gross_pnl - commission
                    balance += net_pnl

                    trades.append(
                        Trade(
                            symbol=symbol,
                            strategy_name=self._strategy.name,
                            signal_id=open_position.signal_id,
                            direction=open_position.direction,
                            entry_time=open_position.entry_time,
                            entry_price=open_position.entry_price,
                            exit_time=candle.open_time,
                            exit_price=exit_price,
                            stop_loss=open_position.stop_loss,
                            take_profit=open_position.take_profit,
                            volume=volume,
                            gross_pnl=gross_pnl,
                            commission=commission,
                            spread_and_slippage_cost=spread_and_slippage_cost,
                            net_pnl=net_pnl,
                            exit_reason=exit_reason,
                            bars_held=i - open_position.entry_index,
                            mae=running_mae,
                            mfe=running_mfe,
                            regime_at_entry=open_position.regime_at_entry,
                        )
                    )
                    open_position = None
                    running_mae = 0.0
                    running_mfe = 0.0

            if (
                pending_signal is not None
                and open_position is None
                and pending_signal.execute_at_index == i
            ):
                signal = pending_signal.signal
                # Executa ao preco de abertura desta barra (a barra em que o
                # sinal se torna executavel), nunca ao preco de referencia
                # que o sinal tinha quando foi gerado — senao o atraso de
                # entrada (`entry_delay_bars`) so mudaria o timestamp, sem
                # nenhum efeito real de realismo de execucao.
                execution_open_price = float(candle.open)
                entry_price = apply_entry_cost(
                    execution_open_price,
                    signal.direction,
                    model=cost_model,
                    candle_spread_points=candle.spread,
                    point=self._point,
                )
                open_position = _OpenPosition(
                    direction=signal.direction,
                    signal_id=signal.signal_id,
                    reference_price=execution_open_price,
                    entry_price=entry_price,
                    entry_time=candle.open_time,
                    entry_index=i,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    regime_at_entry=pending_signal.regime_at_signal,
                )
                pending_signal = None

            if open_position is None and pending_signal is None:
                current_regime = regime_from_row(regimes.iloc[i])
                state = MarketState(
                    symbol=symbol,
                    timeframe=timeframe,
                    features=features.iloc[: i + 1],
                    regime=current_regime,
                )
                new_signal = self._strategy.generate_signal(state)
                if new_signal is not None:
                    execute_at = i + self._config.entry_delay_bars
                    if execute_at < n:
                        pending_signal = _PendingSignal(
                            signal=new_signal,
                            execute_at_index=execute_at,
                            regime_at_signal=current_regime,
                        )

            if open_position is not None:
                sign = _signed(open_position.direction)
                unrealized = (close - open_position.entry_price) * sign * volume * contract_size
                equity_values.append(balance + unrealized)
            else:
                equity_values.append(balance)
            equity_index.append(candle.open_time)

        equity_curve = pd.Series(
            equity_values, index=pd.Index(equity_index, name="open_time"), name="equity"
        )
        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            strategy_name=self._strategy.name,
            initial_balance=self._initial_balance,
            trades=trades,
            equity_curve=equity_curve,
        )

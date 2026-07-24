"""Motor de backtest por tick (Fase 7).

Reusa a mesma geracao de sinal por candle (`Strategy`/`MarketState`/
features) do motor por candle (Fase 5/6) — a diferenca esta em COMO a
entrada e a saida sao executadas: contra a sequencia real de ticks
(bid/ask), com latencia, spread variavel, slippage e possibilidade de
rejeicao, em vez de aproximar pela OHLC da candle seguinte.

Isso resolve uma limitacao explicita do motor por candle: quando stop e
alvo caem na mesma candle, aquele motor precisa assumir o pior caso (nunca
sabe qual foi atingido primeiro). Aqui, com ticks reais em ordem
cronologica verdadeira, essa ambiguidade deixa de existir — o motor
verifica qual nivel foi cruzado primeiro, tick a tick.

Funcionalidades adicionais exigidas pelo prompt mestre para este motor,
implementadas: trailing stop, fechamento por tempo, rejeicao de ordem
(spread excessivo), aviso de liquidez insuficiente (gap entre ticks).

Deliberadamente fora do escopo desta fase: execucao parcial (exigiria
profundidade de livro de ofertas, que a Fase 2 ja apontou como nao
garantida por todas as corretoras) e horario de mercado/calendario de
sessao (nenhum calendario de feriados/sessao implementado ainda).
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.backtesting.engine import Trade
from app.backtesting.fills import (
    FillResult,
    TickCostModel,
    TickLike,
    simulate_entry_fill,
    simulate_exit_fill,
)
from app.market.features import CandleFeatureLike, build_candle_features
from app.market.regimes import MarketRegime, classify_regime_series, regime_from_row
from app.strategies.base import MarketState, Signal, SignalDirection, Strategy


@dataclass(frozen=True, slots=True)
class TickBacktestConfig:
    volume: float = 0.01
    entry_delay_bars: int = 1
    cost_model: TickCostModel = field(default_factory=TickCostModel)
    max_holding_seconds: float | None = None
    trailing_stop_points: float | None = None


@dataclass(frozen=True, slots=True)
class TickTrade:
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
    entry_fill: FillResult
    exit_fill: FillResult
    liquidity_warning: bool

    def as_trade(self) -> Trade:
        """Converte para o `Trade` do motor por candle — permite
        reaproveitar `app.backtesting.metrics.compute_metrics` sem
        duplicar formulas de metricas."""
        return Trade(
            symbol=self.symbol,
            strategy_name=self.strategy_name,
            signal_id=self.signal_id,
            direction=self.direction,
            entry_time=self.entry_time,
            entry_price=self.entry_price,
            exit_time=self.exit_time,
            exit_price=self.exit_price,
            stop_loss=self.stop_loss,
            take_profit=self.take_profit,
            volume=self.volume,
            gross_pnl=self.gross_pnl,
            commission=self.commission,
            spread_and_slippage_cost=self.spread_and_slippage_cost,
            net_pnl=self.net_pnl,
            exit_reason=self.exit_reason,
            bars_held=self.bars_held,
            mae=self.mae,
            mfe=self.mfe,
            regime_at_entry=self.regime_at_entry,
        )


@dataclass(frozen=True, slots=True)
class TickRejection:
    """Registro de auditoria de uma entrada rejeitada — nunca escondida."""

    signal_id: str
    symbol: str
    strategy_name: str
    direction: SignalDirection
    fill: FillResult


@dataclass(frozen=True, slots=True)
class TickBacktestResult:
    symbol: str
    timeframe: str
    strategy_name: str
    initial_balance: float
    trades: list[TickTrade]
    rejections: list[TickRejection]
    equity_curve: pd.Series


def _signed(direction: SignalDirection) -> int:
    return 1 if direction == SignalDirection.LONG else -1


def _bar_index_at_or_after(open_times: list[datetime], target_time: datetime) -> int:
    return bisect.bisect_left(open_times, target_time)


class _ExitOutcome:
    __slots__ = ("reason", "trigger_time", "mae", "mfe", "liquidity_warning")

    def __init__(
        self, reason: str, trigger_time: datetime, mae: float, mfe: float, liquidity_warning: bool
    ) -> None:
        self.reason = reason
        self.trigger_time = trigger_time
        self.mae = mae
        self.mfe = mfe
        self.liquidity_warning = liquidity_warning


def _scan_ticks_for_exit(
    ticks: list[TickLike],
    start_index: int,
    direction: SignalDirection,
    *,
    entry_price: float,
    entry_time: datetime,
    stop_loss: float,
    take_profit: float,
    config: TickBacktestConfig,
    point: float,
) -> _ExitOutcome:
    trailing_stop = stop_loss
    best_price = entry_price
    mae = 0.0
    mfe = 0.0
    liquidity_warning = False
    previous_timestamp = entry_time
    trailing_active = config.trailing_stop_points is not None
    trailing_distance = (config.trailing_stop_points or 0.0) * point

    index = start_index
    while index < len(ticks):
        tick = ticks[index]
        timestamp = tick.timestamp
        bid = float(tick.bid)
        ask = float(tick.ask)

        gap_seconds = (timestamp - previous_timestamp).total_seconds()
        if gap_seconds > config.cost_model.max_tick_gap_seconds:
            liquidity_warning = True
        previous_timestamp = timestamp

        if direction == SignalDirection.LONG:
            mae = max(mae, entry_price - bid)
            mfe = max(mfe, bid - entry_price)
        else:
            mae = max(mae, ask - entry_price)
            mfe = max(mfe, entry_price - ask)

        if trailing_active:
            if direction == SignalDirection.LONG:
                best_price = max(best_price, bid)
                candidate = best_price - trailing_distance
                trailing_stop = max(trailing_stop, candidate)
            else:
                best_price = min(best_price, ask)
                candidate = best_price + trailing_distance
                trailing_stop = min(trailing_stop, candidate)

        if direction == SignalDirection.LONG:
            stop_hit = bid <= trailing_stop
            target_hit = bid >= take_profit
        else:
            stop_hit = ask >= trailing_stop
            target_hit = ask <= take_profit

        time_exit = (
            config.max_holding_seconds is not None
            and (timestamp - entry_time).total_seconds() >= config.max_holding_seconds
        )

        if stop_hit:
            reason = "trailing_stop" if trailing_stop != stop_loss else "stop_loss"
            return _ExitOutcome(reason, timestamp, mae, mfe, liquidity_warning)
        if target_hit:
            return _ExitOutcome("take_profit", timestamp, mae, mfe, liquidity_warning)
        if time_exit:
            return _ExitOutcome("time_exit", timestamp, mae, mfe, liquidity_warning)

        index += 1

    last_time = ticks[-1].timestamp if ticks else entry_time
    return _ExitOutcome("end_of_data", last_time, mae, mfe, liquidity_warning)


class TickBacktestEngine:
    """Roda uma estrategia (sinais gerados por candle) contra a sequencia
    real de ticks para simular fills realistas. Determinístico: mesma
    entrada sempre produz o mesmo `TickBacktestResult`."""

    def __init__(
        self,
        strategy: Strategy,
        config: TickBacktestConfig,
        *,
        point: float,
        contract_size: float,
        bar_seconds: int,
        initial_balance: float = 10_000.0,
    ) -> None:
        self._strategy = strategy
        self._config = config
        self._point = point
        self._contract_size = contract_size
        self._bar_seconds = bar_seconds
        self._initial_balance = initial_balance

    def run(
        self,
        candles: Sequence[CandleFeatureLike],
        ticks: Sequence[TickLike],
        *,
        symbol: str,
        timeframe: str,
    ) -> TickBacktestResult:
        n = len(candles)
        ticks_list = list(ticks)

        if n == 0:
            return TickBacktestResult(
                symbol=symbol,
                timeframe=timeframe,
                strategy_name=self._strategy.name,
                initial_balance=self._initial_balance,
                trades=[],
                rejections=[],
                equity_curve=pd.Series(dtype=float),
            )

        features = build_candle_features(candles, point=self._point)
        regimes = classify_regime_series(features)
        open_times = [c.open_time for c in candles]
        tick_timestamps = [t.timestamp for t in ticks_list]

        balance = self._initial_balance
        trades: list[TickTrade] = []
        rejections: list[TickRejection] = []
        equity_values: list[float] = []
        equity_index: list[datetime] = []

        volume = self._config.volume
        contract_size = self._contract_size
        cost_model = self._config.cost_model

        i = 0
        while i < n:
            current_regime = regime_from_row(regimes.iloc[i])
            state = MarketState(
                symbol=symbol,
                timeframe=timeframe,
                features=features.iloc[: i + 1],
                regime=current_regime,
            )
            signal: Signal | None = self._strategy.generate_signal(state)

            if signal is None:
                equity_values.append(balance)
                equity_index.append(candles[i].open_time)
                i += 1
                continue

            earliest_execution_time = candles[i].open_time + pd.Timedelta(
                seconds=self._bar_seconds * self._config.entry_delay_bars
            )
            entry_fill = simulate_entry_fill(
                ticks_list,
                signal.direction,
                signal_time=earliest_execution_time,
                cost_model=cost_model,
                point=self._point,
            )

            if not entry_fill.filled:
                rejections.append(
                    TickRejection(
                        signal_id=signal.signal_id,
                        symbol=symbol,
                        strategy_name=self._strategy.name,
                        direction=signal.direction,
                        fill=entry_fill,
                    )
                )
                equity_values.append(balance)
                equity_index.append(candles[i].open_time)
                i += 1
                continue

            assert entry_fill.fill_time is not None and entry_fill.fill_price is not None
            entry_tick_index = bisect.bisect_left(tick_timestamps, entry_fill.fill_time)

            outcome = _scan_ticks_for_exit(
                ticks_list,
                entry_tick_index,
                signal.direction,
                entry_price=entry_fill.fill_price,
                entry_time=entry_fill.fill_time,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                config=self._config,
                point=self._point,
            )

            exit_fill = simulate_exit_fill(
                ticks_list,
                signal.direction,
                trigger_time=outcome.trigger_time,
                cost_model=cost_model,
                point=self._point,
            )
            assert exit_fill.fill_time is not None and exit_fill.fill_price is not None

            sign = _signed(signal.direction)
            gross_pnl = (
                (exit_fill.fill_price - entry_fill.fill_price) * sign * volume * contract_size
            )
            commission = cost_model.commission_per_lot * volume

            entry_spread_cost = (
                (entry_fill.spread_points or 0.0) / 2 * self._point * volume * contract_size
            )
            exit_spread_cost = (
                (exit_fill.spread_points or 0.0) / 2 * self._point * volume * contract_size
            )
            slippage_cost = cost_model.slippage_points * self._point * volume * contract_size * 2
            spread_and_slippage_cost = entry_spread_cost + exit_spread_cost + slippage_cost

            net_pnl = gross_pnl - commission
            balance += net_pnl

            entry_bar = _bar_index_at_or_after(open_times, entry_fill.fill_time)
            exit_bar = _bar_index_at_or_after(open_times, outcome.trigger_time)

            trades.append(
                TickTrade(
                    symbol=symbol,
                    strategy_name=self._strategy.name,
                    signal_id=signal.signal_id,
                    direction=signal.direction,
                    entry_time=entry_fill.fill_time,
                    entry_price=entry_fill.fill_price,
                    exit_time=exit_fill.fill_time,
                    exit_price=exit_fill.fill_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    volume=volume,
                    gross_pnl=gross_pnl,
                    commission=commission,
                    spread_and_slippage_cost=spread_and_slippage_cost,
                    net_pnl=net_pnl,
                    exit_reason=outcome.reason,
                    bars_held=max(0, exit_bar - entry_bar),
                    mae=outcome.mae,
                    mfe=outcome.mfe,
                    regime_at_entry=current_regime,
                    entry_fill=entry_fill,
                    exit_fill=exit_fill,
                    liquidity_warning=outcome.liquidity_warning,
                )
            )

            equity_values.append(balance)
            equity_index.append(exit_fill.fill_time)

            next_index = _bar_index_at_or_after(open_times, outcome.trigger_time)
            i = next_index if next_index > i else i + 1

        equity_curve = pd.Series(
            equity_values, index=pd.Index(equity_index, name="time"), name="equity"
        )
        return TickBacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            strategy_name=self._strategy.name,
            initial_balance=self._initial_balance,
            trades=trades,
            rejections=rejections,
            equity_curve=equity_curve,
        )

"""Motor de paper trading incremental (Fase 10).

Diferença deliberada em relação ao backtester (Fase 5/7): ali, a entrada
só pode ocorrer na abertura da PRÓXIMA barra, para nunca usar dados que
ainda não existiam no momento do sinal (evitar look-ahead bias) — uma
preocupação real ao REPROCESSAR HISTÓRICO. Aqui, em paper trading ao
vivo, o sinal só é detectado depois que a barra já fechou (o "agora"
real já é posterior a esse fechamento) — não há look-ahead a evitar
quando o tempo só anda para frente. Por isso a execução acontece no
fechamento da própria barra do sinal, uma aproximação razoável do preço
de execução ao vivo, sem esperar artificialmente pela barra seguinte.

O motor nunca reprocessa o histórico inteiro a cada chamada: mantém um
cursor persistido (`system_settings`, chave por símbolo/timeframe/
estratégia) com o horário da última barra já avaliada. Na primeíssima
chamada (sem cursor ainda), só a barra mais recente é considerada nova —
paper trading começa "a partir de agora", não retroativamente."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.backtesting.costs import CostModel, apply_entry_cost, apply_exit_cost, commission_cost
from app.database.repositories.paper_trade_repository import PaperTradeRepository
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.market.features import CandleFeatureLike, build_candle_features
from app.market.regimes import classify_regime_series, regime_from_row
from app.strategies.base import MarketState, SignalDirection, Strategy


@dataclass(frozen=True, slots=True)
class PaperTradeOpened:
    trade_id: int
    direction: SignalDirection
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float


@dataclass(frozen=True, slots=True)
class PaperTradeClosed:
    trade_id: int
    exit_time: datetime
    exit_price: float
    exit_reason: str
    net_pnl: float


PaperTradingEvent = PaperTradeOpened | PaperTradeClosed


@dataclass(frozen=True, slots=True)
class PaperStepResult:
    processed_bars: int
    events: list[PaperTradingEvent]


def _cursor_key(symbol: str, timeframe: str, strategy_name: str) -> str:
    return f"paper_cursor:{symbol}:{timeframe}:{strategy_name}"


def _as_naive(value: datetime) -> datetime:
    """Normaliza para naive (assumindo UTC, convenção do projeto) antes de
    comparar/subtrair datetimes. Necessário porque o SQLite (e, na
    prática, também o driver MySQL usado aqui) devolve `DateTime(timezone=
    True)` como naive na leitura — sem isso, comparar uma candle recém-
    buscada do MetaTrader (tz-aware) com uma posição já persistida (naive
    após o round-trip pelo banco) levanta `TypeError`."""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


class PaperTradingEngine:
    def __init__(
        self,
        session: Session,
        strategy: Strategy,
        *,
        symbol: str,
        symbol_id: int,
        timeframe: str,
        bar_seconds: int,
        point: float,
        contract_size: float,
        volume: float,
        cost_model: CostModel | None = None,
        model_version: str = "rule-based",
    ) -> None:
        self._session = session
        self._strategy = strategy
        self._symbol = symbol
        self._symbol_id = symbol_id
        self._timeframe = timeframe
        self._bar_seconds = bar_seconds
        self._point = point
        self._contract_size = contract_size
        self._volume = volume
        self._cost_model = cost_model or CostModel()
        self._model_version = model_version
        self._trade_repo = PaperTradeRepository(session)
        self._settings_repo = SystemSettingRepository(session)

    def step(self, candles: Sequence[CandleFeatureLike]) -> PaperStepResult:
        n = len(candles)
        if n == 0:
            return PaperStepResult(processed_bars=0, events=[])

        cursor_key = _cursor_key(self._symbol, self._timeframe, self._strategy.name)
        cursor_value = self._settings_repo.get(cursor_key)
        last_time = _as_naive(datetime.fromisoformat(cursor_value)) if cursor_value else None

        if last_time is None:
            start_index = n - 1
        else:
            start_index = 0
            while start_index < n and _as_naive(candles[start_index].open_time) <= last_time:
                start_index += 1

        if start_index >= n:
            return PaperStepResult(processed_bars=0, events=[])

        features = build_candle_features(candles, point=self._point)
        regimes = classify_regime_series(features)

        events: list[PaperTradingEvent] = []
        open_trade = self._trade_repo.get_open(
            self._symbol_id, self._timeframe, self._strategy.name
        )

        for i in range(start_index, n):
            candle = candles[i]
            low, high = float(candle.low), float(candle.high)

            if open_trade is not None:
                direction = SignalDirection(open_trade.direction)
                stop_loss = float(open_trade.stop_loss)
                take_profit = float(open_trade.take_profit)
                if direction == SignalDirection.LONG:
                    stop_hit, target_hit = low <= stop_loss, high >= take_profit
                else:
                    stop_hit, target_hit = high >= stop_loss, low <= take_profit

                if stop_hit or target_hit:
                    # Conservador por construcao: se ambas cabem na mesma
                    # candle, assume-se o stop (pior caso) — mesma regra
                    # das Fases 5/6/7/8.
                    exit_reason = "stop_loss" if stop_hit else "take_profit"
                    raw_exit_price = stop_loss if stop_hit else take_profit
                    exit_price = apply_exit_cost(
                        raw_exit_price,
                        direction,
                        model=self._cost_model,
                        candle_spread_points=candle.spread,
                        point=self._point,
                    )
                    sign = 1.0 if direction == SignalDirection.LONG else -1.0
                    gross_pnl = (
                        (exit_price - float(open_trade.entry_price))
                        * sign
                        * self._volume
                        * self._contract_size
                    )
                    net_pnl = gross_pnl - commission_cost(self._cost_model, self._volume)
                    elapsed_seconds = (
                        _as_naive(candle.open_time) - _as_naive(open_trade.entry_time)
                    ).total_seconds()
                    bars_held = max(0, round(elapsed_seconds / self._bar_seconds))

                    self._trade_repo.close_position(
                        open_trade,
                        exit_time=candle.open_time,
                        exit_price=Decimal(str(exit_price)),
                        exit_reason=exit_reason,
                        net_pnl=Decimal(str(round(net_pnl, 2))),
                        bars_held=bars_held,
                    )
                    events.append(
                        PaperTradeClosed(
                            trade_id=open_trade.id,
                            exit_time=candle.open_time,
                            exit_price=exit_price,
                            exit_reason=exit_reason,
                            net_pnl=net_pnl,
                        )
                    )
                    open_trade = None

            if open_trade is None:
                current_regime = regime_from_row(regimes.iloc[i])
                state = MarketState(
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    features=features.iloc[: i + 1],
                    regime=current_regime,
                )
                signal = self._strategy.generate_signal(state)
                if signal is not None:
                    entry_price = apply_entry_cost(
                        float(candle.close),
                        signal.direction,
                        model=self._cost_model,
                        candle_spread_points=candle.spread,
                        point=self._point,
                    )
                    open_trade = self._trade_repo.open_position(
                        symbol_id=self._symbol_id,
                        timeframe=self._timeframe,
                        strategy_name=self._strategy.name,
                        model_version=self._model_version,
                        signal_id=signal.signal_id,
                        direction=signal.direction.value,
                        entry_time=candle.open_time,
                        entry_price=Decimal(str(entry_price)),
                        stop_loss=Decimal(str(signal.stop_loss)),
                        take_profit=Decimal(str(signal.take_profit)),
                        volume=Decimal(str(self._volume)),
                    )
                    events.append(
                        PaperTradeOpened(
                            trade_id=open_trade.id,
                            direction=signal.direction,
                            entry_time=candle.open_time,
                            entry_price=entry_price,
                            stop_loss=signal.stop_loss,
                            take_profit=signal.take_profit,
                        )
                    )

        self._settings_repo.set(cursor_key, candles[-1].open_time.isoformat())
        return PaperStepResult(processed_bars=n - start_index, events=events)

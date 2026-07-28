"""Motor de execução em conta demo (Fase 11, estendido na Fase 13).

Liga estratégia (Fase 5/6) → motor de risco (`app.risk`, poder de veto) →
envio de ordem real em conta demo (`app.mt5.orders.send_market_order`,
com stop-loss/take-profit já anexados ao pedido) → persistência
(`app.database.models.live_trade`) → reconciliação contra o estado real
reportado pelo MetaTrader 5.

Mesmo desenho incremental e persistido do `PaperTradingEngine` (Fase 10):
um cursor (`system_settings`) evita reprocessar o histórico inteiro a
cada chamada; na primeiríssima chamada, só a barra mais recente conta
como nova. Diferença central: quem fecha a posição é o BROKER (via
stop-loss/take-profit anexados ao pedido), nunca este processo — a
reconciliação apenas detecta que isso já aconteceu, nunca envia uma
ordem de fechamento por conta própria (essa lógica pertenceria a um
`CLOSE_PENDING` explícito, fora do escopo desta fase).

Toda avaliação de sinal gera uma linha em `live_trades`, mesmo quando
rejeitada pelo risco ou pelo broker — nenhum sinal é descartado
silenciosamente.

`clock` (Fase 13) é o horário de parede real usado para checar a saúde
do feed (`app.risk.feed_health`) — injetável para testes determinísticos
(o padrão é `datetime.now(UTC)`); nunca inferido a partir do horário das
próprias candles, que não tem relação com "agora" de verdade."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.database.models.live_trade import LiveTrade
from app.database.repositories.live_trade_repository import LiveTradeRepository
from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.execution.order_state import OrderState
from app.market.features import CandleFeatureLike, build_candle_features
from app.market.regimes import classify_regime_series, regime_from_row
from app.mt5.account import AccountSnapshot
from app.mt5.client import MT5ClientProtocol
from app.mt5.orders import fetch_history_deals, send_market_order
from app.mt5.positions import fetch_open_positions
from app.mt5.symbol_mapper import SymbolSpecification
from app.risk.circuit_breaker import DailyStats
from app.risk.config import RiskLimits
from app.risk.engine import evaluate_signal
from app.strategies.base import MarketState, SignalDirection, Strategy

_DEAL_ENTRY_OUT_DEFAULT = 1
"""Valor documentado e estável de `DEAL_ENTRY_OUT` na API MetaTrader5 —
identifica um deal que FECHA uma posição (em vez de abrir/aumentar)."""


@dataclass(frozen=True, slots=True)
class SignalRejected:
    trade_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class OrderRejectedByBroker:
    trade_id: int
    reason: str


@dataclass(frozen=True, slots=True)
class PositionOpened:
    trade_id: int
    direction: SignalDirection
    entry_price: float
    volume: float
    mt5_position_ticket: int


@dataclass(frozen=True, slots=True)
class PositionClosed:
    trade_id: int
    exit_price: float
    net_pnl: float


@dataclass(frozen=True, slots=True)
class PositionReconciling:
    trade_id: int
    """O broker não reporta mais esta posição como aberta, mas nenhum deal
    de fechamento correspondente foi encontrado no histórico consultado —
    nunca se inventa um preço/resultado de saída nesse caso."""


DemoExecutionEvent = (
    SignalRejected | OrderRejectedByBroker | PositionOpened | PositionClosed | PositionReconciling
)


@dataclass(frozen=True, slots=True)
class DemoStepResult:
    processed_bars: int
    events: list[DemoExecutionEvent]


def _cursor_key(symbol: str, timeframe: str, strategy_name: str) -> str:
    return f"demo_cursor:{symbol}:{timeframe}:{strategy_name}"


def _as_naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


class DemoExecutionEngine:
    def __init__(
        self,
        session: Session,
        client: MT5ClientProtocol,
        strategy: Strategy,
        *,
        symbol: str,
        symbol_id: int,
        timeframe: str,
        point: float,
        account: AccountSnapshot,
        symbol_spec: SymbolSpecification,
        risk_limits: RiskLimits,
        magic: int = 0,
        model_version: str = "rule-based",
        clock: Callable[[], datetime] | None = None,
        scope_across_timeframes: bool = False,
        allow_real_account: bool = False,
    ) -> None:
        self._session = session
        self._client = client
        self._strategy = strategy
        self._symbol = symbol
        self._symbol_id = symbol_id
        self._timeframe = timeframe
        self._risk_timeframe: str | None = None if scope_across_timeframes else timeframe
        """Escopo dos limites de risco e da busca por posição aberta.

        `None` (piloto automático) abrange TODOS os timeframes desta
        estratégia neste símbolo. É obrigatório quando quem chama pode
        trocar de timeframe entre ciclos: contar por timeframe faria a
        troca de M5 para M15 zerar os contadores do dia e esconder a
        posição já aberta, contornando os limites sem intenção."""
        self._point = point
        self._account = account
        self._symbol_spec = symbol_spec
        self._risk_limits = risk_limits
        self._magic = magic
        self._allow_real_account = allow_real_account
        """Reflete o modo configurado (DEMO/REAL). `app.mt5.orders` recusa
        quando o tipo da conta conectada diverge deste valor."""

        self._model_version = model_version
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trade_repo = LiveTradeRepository(session)
        self._settings_repo = SystemSettingRepository(session)

    def _compute_daily_stats(self, now: datetime) -> DailyStats:
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        trades_today = self._trade_repo.count_entries_since(
            self._symbol_id, self._risk_timeframe, self._strategy.name, since=start_of_day
        )
        daily_pnl = self._trade_repo.sum_net_pnl_since(
            self._symbol_id, self._risk_timeframe, self._strategy.name, since=start_of_day
        )
        consecutive_losses = 0
        for trade in self._trade_repo.get_recent_closed(
            self._symbol_id, self._risk_timeframe, self._strategy.name, limit=20
        ):
            if trade.net_pnl is not None and float(trade.net_pnl) < 0:
                consecutive_losses += 1
            else:
                break
        last_entry = self._trade_repo.get_last_entry_time(
            self._symbol_id, self._risk_timeframe, self._strategy.name
        )
        active = self._trade_repo.get_active_position(
            self._symbol_id, self._risk_timeframe, self._strategy.name
        )
        return DailyStats(
            trades_today=trades_today,
            consecutive_losses=consecutive_losses,
            daily_pnl=daily_pnl,
            open_positions_count=1 if active is not None else 0,
            last_trade_time=_as_naive(last_entry) if last_entry is not None else None,
        )

    def _reconcile(self, trade: LiveTrade) -> DemoExecutionEvent | None:
        """Retorna `None` quando a posição continua aberta de verdade —
        estado normal, não um evento a reportar. Só retorna um evento
        quando algo de fato mudou (fechada) ou quando a divergência não
        pôde ser explicada (`PositionReconciling`, revisão manual)."""
        mt5_position_ticket = trade.mt5_position_ticket
        positions = fetch_open_positions(self._client, self._symbol)
        if any(p.ticket == mt5_position_ticket for p in positions):
            return None

        # O broker não reporta mais esta posição -- procura o deal que a
        # fechou numa janela ampla o suficiente para cobrir qualquer atraso
        # entre o fechamento real e este poll.
        now = datetime.now(UTC)
        deals = fetch_history_deals(self._client, now - timedelta(days=7), now)
        deal_entry_out = getattr(self._client, "DEAL_ENTRY_OUT", _DEAL_ENTRY_OUT_DEFAULT)
        closing_deal = next(
            (
                d
                for d in deals
                if d.position_id == mt5_position_ticket and d.entry == deal_entry_out
            ),
            None,
        )
        if closing_deal is None:
            self._trade_repo.mark_reconciling(trade)
            return PositionReconciling(trade_id=trade.id)

        self._trade_repo.close_position(
            trade,
            exit_time=closing_deal.executed_at,
            exit_price=Decimal(str(closing_deal.price)),
            net_pnl=Decimal(str(round(closing_deal.profit, 2))),
        )
        return PositionClosed(
            trade_id=trade.id, exit_price=closing_deal.price, net_pnl=closing_deal.profit
        )

    def step(self, candles: Sequence[CandleFeatureLike]) -> DemoStepResult:
        n = len(candles)
        if n == 0:
            return DemoStepResult(processed_bars=0, events=[])

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
            return DemoStepResult(processed_bars=0, events=[])

        events: list[DemoExecutionEvent] = []

        active_trade = self._trade_repo.get_active_position(
            self._symbol_id, self._risk_timeframe, self._strategy.name
        )
        if active_trade is not None:
            if active_trade.mt5_position_ticket is None:
                # Nunca deveria acontecer (so fica ativo apos ticket
                # atribuido), mas nunca assume -- marca para revisao manual.
                self._trade_repo.mark_reconciling(active_trade)
                events.append(PositionReconciling(trade_id=active_trade.id))
            else:
                event = self._reconcile(active_trade)
                if event is not None:
                    events.append(event)
                # `None` (ainda aberta) ou `PositionReconciling` (ambigua):
                # continua ocupado, nao avalia novo sinal nesta chamada.
                # So um `PositionClosed` libera a vaga.
                if isinstance(event, PositionClosed):
                    active_trade = None

        if active_trade is None:
            features = build_candle_features(candles, point=self._point)
            regimes = classify_regime_series(features)

            for i in range(start_index, n):
                candle = candles[i]
                current_regime = regime_from_row(regimes.iloc[i])
                state = MarketState(
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    features=features.iloc[: i + 1],
                    regime=current_regime,
                )
                signal = self._strategy.generate_signal(state)
                if signal is None:
                    continue

                signal_time = candle.open_time
                stats = self._compute_daily_stats(_as_naive(signal_time))
                decision = evaluate_signal(
                    signal,
                    stats=stats,
                    limits=self._risk_limits,
                    account=self._account,
                    symbol_spec=self._symbol_spec,
                    current_spread_points=float(candle.spread),
                    feed_last_update_time=_as_naive(candles[-1].open_time),
                    now=_as_naive(self._clock()),
                )

                if not decision.approved:
                    trade = self._trade_repo.create(
                        symbol_id=self._symbol_id,
                        timeframe=self._timeframe,
                        strategy_name=self._strategy.name,
                        model_version=self._model_version,
                        signal_id=signal.signal_id,
                        direction=signal.direction.value,
                        order_state=OrderState.RISK_REJECTED,
                        signal_time=signal_time,
                        rejection_reason=decision.reason,
                    )
                    events.append(SignalRejected(trade_id=trade.id, reason=decision.reason))
                    continue

                assert decision.computed_volume is not None
                result = send_market_order(
                    self._client,
                    account=self._account,
                    allow_real_account=self._allow_real_account,
                    symbol=self._symbol,
                    direction=signal.direction,
                    volume=decision.computed_volume,
                    price=float(candle.close),
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    magic=self._magic,
                    # MetaTrader 5/brokers costumam limitar o comentario a
                    # 31 caracteres. Truncar preserva o identificador sem
                    # transformar uma ordem valida em rejeicao tecnica.
                    comment=f"{self._strategy.name}:{signal.signal_id[:16]}"[:31],
                )

                if not result.success:
                    trade = self._trade_repo.create(
                        symbol_id=self._symbol_id,
                        timeframe=self._timeframe,
                        strategy_name=self._strategy.name,
                        model_version=self._model_version,
                        signal_id=signal.signal_id,
                        direction=signal.direction.value,
                        order_state=OrderState.REJECTED,
                        signal_time=signal_time,
                        rejection_reason=result.comment,
                        volume=Decimal(str(decision.computed_volume)),
                        stop_loss=Decimal(str(signal.stop_loss)),
                        take_profit=Decimal(str(signal.take_profit)),
                    )
                    events.append(OrderRejectedByBroker(trade_id=trade.id, reason=result.comment))
                    continue

                assert result.price is not None
                trade = self._trade_repo.create(
                    symbol_id=self._symbol_id,
                    timeframe=self._timeframe,
                    strategy_name=self._strategy.name,
                    model_version=self._model_version,
                    signal_id=signal.signal_id,
                    direction=signal.direction.value,
                    order_state=OrderState.POSITION_OPEN,
                    signal_time=signal_time,
                    mt5_order_ticket=result.order_ticket,
                    mt5_position_ticket=result.position_ticket,
                    entry_time=candle.open_time,
                    entry_price=Decimal(str(result.price)),
                    stop_loss=Decimal(str(signal.stop_loss)),
                    take_profit=Decimal(str(signal.take_profit)),
                    volume=Decimal(str(decision.computed_volume)),
                )
                events.append(
                    PositionOpened(
                        trade_id=trade.id,
                        direction=signal.direction,
                        entry_price=result.price,
                        volume=decision.computed_volume,
                        mt5_position_ticket=result.position_ticket or 0,
                    )
                )
                break

        self._settings_repo.set(cursor_key, candles[-1].open_time.isoformat())
        return DemoStepResult(processed_bars=n - start_index, events=events)

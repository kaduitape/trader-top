"""Repositorio do Learning Engine (`apexflow_decisions`).

Alem do CRUD, oferece as agregacoes que respondem "o motor esta
melhorando?" — win rate, profit factor, expectancia e taxa de abstencao —
calculadas SOBRE O HISTORICO REAL, nunca estimadas.

Todas as metricas devolvem `None` (nao zero) quando nao ha amostra
suficiente: um profit factor "0.0" com duas operacoes seria uma mentira
mais perigosa que a ausencia do numero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.models.apexflow_decision import ApexFlowDecisionRecord

MIN_TRADES_FOR_STATISTICS = 5
"""Abaixo disso qualquer estatistica de desempenho e anedota."""


@dataclass(frozen=True, slots=True)
class PerformanceSummary:
    total_decisions: int
    entries: int
    abstentions: int
    closed_trades: int
    wins: int
    losses: int
    win_rate: float | None
    profit_factor: float | None
    expectancy: float | None
    net_pnl: float
    abstention_rate: float | None

    @property
    def has_statistics(self) -> bool:
        return self.closed_trades >= MIN_TRADES_FOR_STATISTICS


class ApexFlowDecisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        symbol_id: int,
        timeframe: str,
        decided_at: datetime,
        action: str,
        probability_buy: float,
        probability_sell: float,
        probability_abstain: float,
        confidence: float,
        min_confidence: float,
        model_version: str,
        feature_version: str,
        completeness: float,
        context_state: str,
        session_rating: str = "",
        volume_level: str = "",
        spread_points: float | None = None,
        atr_points: float | None = None,
        ticks_per_second: float | None = None,
        mtf_alignment: float | None = None,
        vetoes: str | None = None,
        reasons: str | None = None,
        feature_vector: str | None = None,
        live_trade_id: int | None = None,
    ) -> ApexFlowDecisionRecord:
        def decimal_or_none(value: float | None) -> Decimal | None:
            return None if value is None else Decimal(str(round(value, 4)))

        record = ApexFlowDecisionRecord(
            symbol_id=symbol_id,
            timeframe=timeframe,
            decided_at=decided_at,
            action=action,
            probability_buy=Decimal(str(round(probability_buy, 4))),
            probability_sell=Decimal(str(round(probability_sell, 4))),
            probability_abstain=Decimal(str(round(probability_abstain, 4))),
            confidence=Decimal(str(round(confidence, 4))),
            min_confidence=Decimal(str(round(min_confidence, 4))),
            model_version=model_version[:50],
            feature_version=feature_version[:50],
            completeness=Decimal(str(round(completeness, 4))),
            context_state=context_state[:30],
            session_rating=session_rating[:16],
            volume_level=volume_level[:16],
            spread_points=decimal_or_none(spread_points),
            atr_points=decimal_or_none(atr_points),
            ticks_per_second=decimal_or_none(ticks_per_second),
            mtf_alignment=decimal_or_none(mtf_alignment),
            vetoes=vetoes,
            reasons=reasons,
            feature_vector=feature_vector,
            live_trade_id=live_trade_id,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def attach_result(
        self,
        record: ApexFlowDecisionRecord,
        *,
        net_pnl: float,
        r_multiple: float | None = None,
        max_drawdown: float | None = None,
        closed_at: datetime | None = None,
    ) -> None:
        record.result_net_pnl = Decimal(str(round(net_pnl, 2)))
        record.result_r_multiple = (
            None if r_multiple is None else Decimal(str(round(r_multiple, 4)))
        )
        record.result_max_drawdown = (
            None if max_drawdown is None else Decimal(str(round(max_drawdown, 2)))
        )
        record.closed_at = closed_at or datetime.now(tz=None)
        self._session.flush()

    def attach_live_trade(
        self, record: ApexFlowDecisionRecord, live_trade_id: int
    ) -> None:
        """Liga a decisao a operacao que ela originou.

        Feito DEPOIS do envio da ordem, porque so ai existe um `live_trade_id`
        — a decisao e gravada antes, para que uma falha no envio nao apague o
        registro de que o motor decidiu operar.
        """
        record.live_trade_id = live_trade_id
        self._session.flush()

    def get_by_live_trade(self, live_trade_id: int) -> ApexFlowDecisionRecord | None:
        stmt = select(ApexFlowDecisionRecord).where(
            ApexFlowDecisionRecord.live_trade_id == live_trade_id
        )
        return self._session.execute(stmt).scalars().first()

    def list_recent(
        self, *, symbol_id: int | None = None, limit: int = 50
    ) -> list[ApexFlowDecisionRecord]:
        stmt = select(ApexFlowDecisionRecord)
        if symbol_id is not None:
            stmt = stmt.where(ApexFlowDecisionRecord.symbol_id == symbol_id)
        stmt = stmt.order_by(ApexFlowDecisionRecord.decided_at.desc()).limit(limit)
        return list(self._session.execute(stmt).scalars().all())

    def count_since(self, *, since: datetime, symbol_id: int | None = None) -> int:
        stmt = select(func.count()).where(ApexFlowDecisionRecord.decided_at >= since)
        if symbol_id is not None:
            stmt = stmt.where(ApexFlowDecisionRecord.symbol_id == symbol_id)
        return int(self._session.execute(stmt).scalar_one())

    def performance(
        self, *, symbol_id: int | None = None, since: datetime | None = None
    ) -> PerformanceSummary:
        """Agrega o desempenho real das decisoes registradas."""
        stmt = select(ApexFlowDecisionRecord)
        if symbol_id is not None:
            stmt = stmt.where(ApexFlowDecisionRecord.symbol_id == symbol_id)
        if since is not None:
            stmt = stmt.where(ApexFlowDecisionRecord.decided_at >= since)
        records = list(self._session.execute(stmt).scalars().all())

        total = len(records)
        entries = sum(1 for record in records if record.action in ("BUY", "SELL"))
        abstentions = total - entries
        closed = [record for record in records if record.result_net_pnl is not None]

        gains = [float(r.result_net_pnl) for r in closed if float(r.result_net_pnl) > 0]  # type: ignore[arg-type]
        drawdowns = [float(r.result_net_pnl) for r in closed if float(r.result_net_pnl) < 0]  # type: ignore[arg-type]
        net = sum(float(r.result_net_pnl) for r in closed)  # type: ignore[arg-type]

        enough = len(closed) >= MIN_TRADES_FOR_STATISTICS
        win_rate = len(gains) / len(closed) if enough and closed else None
        gross_loss = abs(sum(drawdowns))
        profit_factor = (
            (sum(gains) / gross_loss) if enough and gross_loss > 0 else None
        )
        expectancy = (net / len(closed)) if enough and closed else None

        return PerformanceSummary(
            total_decisions=total,
            entries=entries,
            abstentions=abstentions,
            closed_trades=len(closed),
            wins=len(gains),
            losses=len(drawdowns),
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            net_pnl=net,
            abstention_rate=(abstentions / total) if total else None,
        )

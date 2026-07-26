"""Learning Engine: grava toda decisao para reavaliacao futura.

Ponte fina entre o motor (puro, sem banco) e o repositorio. Existe
separada de `decision.py` justamente para que o motor continue testavel
sem banco nenhum — decidir e registrar sao responsabilidades diferentes.

Grava **todas** as decisoes, inclusive NAO OPERAR. Registrar so as
entradas produziria um historico enviesado: sem as abstencoes e impossivel
descobrir depois se o robo ficou de fora de boas oportunidades ou se
acertou ao se abster.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.apexflow.context import MarketContext
from app.apexflow.decision import ApexFlowDecision
from app.apexflow.features import FeatureVector
from app.database.models.apexflow_decision import ApexFlowDecisionRecord
from app.database.models.live_trade import LiveTrade
from app.database.repositories.apexflow_decision_repository import (
    ApexFlowDecisionRepository,
)
from app.market.sessions import SymbolSessionState
from app.market.volume_profile import VolumeReading

MAX_REASONS_STORED = 12


def record_decision(
    session: Session,
    decision: ApexFlowDecision,
    vector: FeatureVector,
    *,
    symbol_id: int,
    timeframe: str,
    context: MarketContext,
    session_state: SymbolSessionState | None = None,
    volume: VolumeReading | None = None,
    live_trade_id: int | None = None,
) -> ApexFlowDecisionRecord:
    """Persiste uma decisao com o feature vector completo.

    O vetor vai como JSON junto de `feature_version`: e o que permite
    reavaliar um modelo novo contra exatamente os sensores que o motor
    tinha naquele instante, sem depender de reconstruir dados de mercado
    que ja mudaram.
    """
    repository = ApexFlowDecisionRepository(session)
    return repository.create(
        symbol_id=symbol_id,
        timeframe=timeframe,
        decided_at=decision.generated_at,
        action=decision.action.value,
        probability_buy=decision.probability_buy,
        probability_sell=decision.probability_sell,
        probability_abstain=decision.probability_abstain,
        confidence=decision.confidence,
        min_confidence=decision.min_confidence,
        model_version=decision.model_version,
        feature_version=decision.feature_version,
        completeness=decision.completeness,
        context_state=context.state.value,
        session_rating=session_state.rating.value if session_state is not None else "",
        volume_level=volume.level.value if volume is not None else "",
        spread_points=vector.values.get("spread_points"),
        atr_points=vector.values.get("volatility_atr_points"),
        ticks_per_second=vector.values.get("flow_ticks_per_second"),
        mtf_alignment=vector.values.get("mtf_alignment"),
        vetoes=json.dumps(list(decision.vetoes), ensure_ascii=True) if decision.vetoes else None,
        reasons=json.dumps(
            list(decision.reasons[:MAX_REASONS_STORED]), ensure_ascii=True
        ),
        feature_vector=json.dumps(vector.as_dict(), ensure_ascii=True),
        live_trade_id=live_trade_id,
    )


def r_multiple_of(trade: LiveTrade) -> float | None:
    """R realizado da operacao fechada, ou `None` quando nao ha como saber.

    Usa `initial_stop_loss` (migration 0010), o stop com que a operacao
    NASCEU — nunca o `stop_loss` atual, que o trailing pode ter movido e que
    produziria um R inflado. Operacoes anteriores a essa coluna, ou sem
    preco de saida, devolvem `None` em vez de um numero inventado.
    """
    if (
        trade.entry_price is None
        or trade.exit_price is None
        or trade.initial_stop_loss is None
    ):
        return None
    entry = float(trade.entry_price)
    risk = abs(entry - float(trade.initial_stop_loss))
    if risk <= 0:
        return None
    moved = float(trade.exit_price) - entry
    if str(trade.direction).upper() == "SHORT":
        moved = -moved
    return moved / risk


def record_trade_result(session: Session, trade: LiveTrade) -> bool:
    """Anexa o resultado da operacao fechada `trade` a decisao que a gerou.

    Ponto unico de entrada usado pelo ciclo do piloto quando a reconciliacao
    detecta um fechamento. Devolve `False` quando nao existe decisao ligada
    (operacao aberta por outro caminho) — caso legitimo, nunca excecao.
    """
    if trade.net_pnl is None:
        return False
    return record_outcome(
        session,
        live_trade_id=trade.id,
        net_pnl=float(trade.net_pnl),
        r_multiple=r_multiple_of(trade),
        closed_at=trade.exit_time,
    )


def record_outcome(
    session: Session,
    *,
    live_trade_id: int,
    net_pnl: float,
    r_multiple: float | None = None,
    max_drawdown: float | None = None,
    closed_at: datetime | None = None,
) -> bool:
    """Anexa o resultado a decisao que originou a operacao.

    Devolve `False` quando nao existe decisao ligada aquele trade — caso
    legitimo (operacoes abertas por outro caminho que nao o ApexFlow), que
    nunca deve virar excecao nem criar um registro orfao.
    """
    repository = ApexFlowDecisionRepository(session)
    record = repository.get_by_live_trade(live_trade_id)
    if record is None:
        return False
    repository.attach_result(
        record,
        net_pnl=net_pnl,
        r_multiple=r_multiple,
        max_drawdown=max_drawdown,
        closed_at=closed_at,
    )
    return True


def load_feature_vector(record: ApexFlowDecisionRecord) -> dict[str, float | None]:
    """Rele o vetor gravado. Um registro sem vetor devolve `{}` — nunca um
    dicionario preenchido com zeros que passaria por dado real."""
    if not record.feature_vector:
        return {}
    try:
        data = json.loads(record.feature_vector)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}

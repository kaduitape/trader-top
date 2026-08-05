"""Endpoint de analise (Fase 18.9) — somente consultivo. NAO importa nada
de `app.execution`/`app.paper_trading`/`app.risk`: garante por construcao
que este endpoint nunca gera ordem nem alimenta a pipeline de execucao."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.api.schemas.analysis import AnalysisReportOut, to_schema
from app.core.config import Settings, get_settings
from app.database.models.user import User
from app.database.session import get_db
from app.market.multi_timeframe import SymbolNotFoundError
from app.mt5.market_data import Timeframe
from app.news.call_log import ORIGIN_PANEL, calls_from
from app.services.analysis_service import analyze_symbol

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


@router.get("/{symbol}", response_model=AnalysisReportOut)
def get_analysis(
    symbol: str,
    timeframe: str | None = None,
    threshold: float | None = None,
    enforce_gates: bool = True,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),
) -> AnalysisReportOut:
    resolved_timeframe = Timeframe(timeframe or settings.analysis_default_timeframe)
    resolved_threshold = threshold if threshold is not None else settings.analysis_default_threshold

    try:
        with calls_from(ORIGIN_PANEL):
            report = analyze_symbol(
                db,
                symbol=symbol,
                primary_timeframe=resolved_timeframe,
                enforce_gates=enforce_gates,
                threshold=resolved_threshold,
                now=datetime.now(UTC),
            )
    except SymbolNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NotImplementedError as exc:
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)) from exc

    return to_schema(report)

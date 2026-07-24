"""Testes do endpoint de analise (Fase 18.9). `client` (fixture de
conftest.py) e um `TestClient` real da aplicacao FastAPI."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from app.core.security import hash_password
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.database.repositories.user_repository import UserRepository
from app.mt5.market_data import RawCandle
from app.mt5.symbol_mapper import SymbolSpecification

_EXPECTED_FACTOR_NAMES = {
    "structure",
    "price_action",
    "liquidity",
    "volume",
    "news",
    "fundamentals",
    "correlation",
}


def _create_user(db_session, username: str, password: str):
    repo = UserRepository(db_session)
    role = repo.get_or_create_role("ADMIN")
    user = repo.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password(password),
        roles=[role],
    )
    db_session.commit()
    return user


def _token(client, username: str, password: str) -> str:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    return response.json()["access_token"]


def _seed_uptrend_candles(db_session, symbol_name: str, timeframe: str, n: int = 260) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=symbol_name,
            description="Test symbol",
            digits=5,
            point=0.00001,
            volume_min=0.01,
            volume_max=100.0,
            volume_step=0.01,
            trade_contract_size=100_000.0,
            spread=2,
            trade_mode=4,
            visible=True,
        )
    )
    start = datetime(2026, 6, 1, tzinfo=UTC) - timedelta(minutes=n)
    candles = []
    price = 100.0
    for i in range(n):
        price = 100.0 + (i / n) * 60.0 + 5 * math.sin(i * 0.6)
        open_ = price - 0.05
        close = price
        candles.append(
            RawCandle(
                open_time=start + timedelta(minutes=i),
                open=open_,
                high=max(open_, close) + 0.05,
                low=min(open_, close) - 0.05,
                close=close,
                tick_volume=100 + i,
                spread=2,
                real_volume=0,
            )
        )
    CandleRepository(db_session).bulk_upsert(symbol.id, timeframe, candles)
    db_session.commit()


def test_analysis_requires_authentication(client) -> None:
    response = client.get("/api/analysis/EURUSD")
    assert response.status_code == 401


def test_analysis_returns_404_for_unknown_symbol(client, db_session) -> None:
    _create_user(db_session, "analysis_api_unknown_user", "correct-password")
    token = _token(client, "analysis_api_unknown_user", "correct-password")

    response = client.get(
        "/api/analysis/ANALYSIS_API_DOES_NOT_EXIST",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_analysis_returns_200_with_all_seven_factors(client, db_session) -> None:
    _create_user(db_session, "analysis_api_ok_user", "correct-password")
    token = _token(client, "analysis_api_ok_user", "correct-password")
    _seed_uptrend_candles(db_session, "ANALYSIS_API_SYM", "M15")

    response = client.get(
        "/api/analysis/ANALYSIS_API_SYM",
        params={"timeframe": "M15"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "ANALYSIS_API_SYM"
    assert body["recommendation"] in ("ENTER", "DO_NOT_ENTER")

    factor_names = {factor["name"] for factor in body["score"]["factors"]}
    assert factor_names == _EXPECTED_FACTOR_NAMES
    assert len(body["score"]["factors"]) == 7


def test_analysis_respects_custom_threshold(client, db_session) -> None:
    _create_user(db_session, "analysis_api_threshold_user", "correct-password")
    token = _token(client, "analysis_api_threshold_user", "correct-password")
    _seed_uptrend_candles(db_session, "ANALYSIS_API_THRESHOLD", "M15")

    response = client.get(
        "/api/analysis/ANALYSIS_API_THRESHOLD",
        params={"timeframe": "M15", "threshold": 1.0},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["recommendation"] == "ENTER"
    assert response.json()["trade_levels"] is not None

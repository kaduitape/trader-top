from __future__ import annotations

from dataclasses import replace

from app.execution.automation_settings import (
    TradingAutomationConfig,
    load_trading_automation_config,
    save_trading_automation_config,
)


def test_trading_automation_config_round_trip(db_session) -> None:
    expected = replace(
        TradingAutomationConfig(),
        enabled=True,
        symbol="EURUSD",
        timeframe="M5",
        analysis_threshold=82.0,
        risk_per_trade_pct=0.4,
        max_trades_per_day=4,
    )

    save_trading_automation_config(db_session, expected)
    db_session.commit()

    assert load_trading_automation_config(db_session) == expected


def test_trading_automation_config_defaults_are_safe() -> None:
    config = TradingAutomationConfig()

    assert config.enabled is False
    assert config.risk_per_trade_pct <= 1.0
    assert config.max_simultaneous_positions == 1

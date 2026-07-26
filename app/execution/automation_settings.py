"""Configuracao persistente da automacao de operacoes em conta demo.

O dashboard e o worker Windows compartilham estes valores pelo banco.  A
configuracao nunca libera conta real: esse bloqueio continua sendo imposto
independentemente pelo worker, pelo motor de risco e por ``send_market_order``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.market.multi_timeframe import ANALYSIS_TIMEFRAMES

TRADING_AUTOMATION_CONFIG_KEY = "trading_automation_config"

_TIMEFRAME_CODES = frozenset(timeframe.value for timeframe in ANALYSIS_TIMEFRAMES)


@dataclass(frozen=True, slots=True)
class TradingAutomationConfig:
    enabled: bool = False
    autopilot: bool = True
    """Piloto automatico (`app.execution.autopilot`): o robo escolhe sozinho
    o operacional, o timeframe de execucao, o score minimo e o
    multiplicador de risco a partir da sessao e do volume do par.

    Ligado por padrao — e o modo que o operador pediu ("escolho a moeda, o
    robo decide o resto"). Desligado, o comportamento anterior permanece
    intacto: `timeframe`/`analysis_threshold` fixos, definidos a mao.

    O piloto so pode tornar a operacao MAIS restritiva que a configuracao
    (score minimo maior, risco menor) — nunca mais frouxa; ver
    `app.execution.playbook`."""

    symbol: str = "XAUUSD"
    timeframe: str = "M15"
    analysis_threshold: float = 90.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 3
    max_simultaneous_positions: int = 1
    max_trades_per_day: int = 10
    min_seconds_between_trades: int = 60
    max_spread_points: float = 30.0


def _read_json(repository: SystemSettingRepository) -> dict[str, Any]:
    raw = repository.get(TRADING_AUTOMATION_CONFIG_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded_float(
    value: object, *, default: float, minimum: float, maximum: float
) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def load_trading_automation_config(session: Session) -> TradingAutomationConfig:
    data = _read_json(SystemSettingRepository(session))
    defaults = TradingAutomationConfig()
    timeframe = str(data.get("timeframe", defaults.timeframe)).strip().upper()
    if timeframe not in _TIMEFRAME_CODES:
        timeframe = defaults.timeframe

    symbol = str(data.get("symbol", defaults.symbol)).strip().upper()
    if not symbol:
        symbol = defaults.symbol

    return TradingAutomationConfig(
        enabled=bool(data.get("enabled", defaults.enabled)),
        autopilot=bool(data.get("autopilot", defaults.autopilot)),
        symbol=symbol[:32],
        timeframe=timeframe,
        analysis_threshold=_bounded_float(
            data.get("analysis_threshold"),
            default=defaults.analysis_threshold,
            minimum=50.0,
            maximum=100.0,
        ),
        risk_per_trade_pct=_bounded_float(
            data.get("risk_per_trade_pct"),
            default=defaults.risk_per_trade_pct,
            minimum=0.1,
            maximum=1.0,
        ),
        max_daily_loss_pct=_bounded_float(
            data.get("max_daily_loss_pct"),
            default=defaults.max_daily_loss_pct,
            minimum=0.5,
            maximum=5.0,
        ),
        max_consecutive_losses=_bounded_int(
            data.get("max_consecutive_losses"),
            default=defaults.max_consecutive_losses,
            minimum=1,
            maximum=10,
        ),
        max_simultaneous_positions=_bounded_int(
            data.get("max_simultaneous_positions"),
            default=defaults.max_simultaneous_positions,
            minimum=1,
            maximum=3,
        ),
        max_trades_per_day=_bounded_int(
            data.get("max_trades_per_day"),
            default=defaults.max_trades_per_day,
            minimum=1,
            maximum=50,
        ),
        min_seconds_between_trades=_bounded_int(
            data.get("min_seconds_between_trades"),
            default=defaults.min_seconds_between_trades,
            minimum=60,
            maximum=86_400,
        ),
        max_spread_points=_bounded_float(
            data.get("max_spread_points"),
            default=defaults.max_spread_points,
            minimum=1.0,
            maximum=500.0,
        ),
    )


def save_trading_automation_config(
    session: Session, config: TradingAutomationConfig
) -> None:
    SystemSettingRepository(session).set(
        TRADING_AUTOMATION_CONFIG_KEY,
        json.dumps(asdict(config), ensure_ascii=True, separators=(",", ":")),
        description="Limites e estado da automacao de operacoes em conta demo.",
    )

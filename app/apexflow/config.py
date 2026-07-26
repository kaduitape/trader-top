"""Configuracao persistente do ApexFlow AI.

Compartilhada entre o dashboard e o worker pelo mesmo canal chave-valor
dos demais modulos. Nenhum parametro aqui pode afrouxar os limites de
risco globais (`app.risk.config.RiskLimits`) — eles continuam com poder de
veto independente e sao aplicados depois.

Todos os limites tem faixa validada na leitura: um valor fora da faixa
volta ao padrao em vez de virar uma configuracao perigosa silenciosa.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

APEXFLOW_CONFIG_KEY = "apexflow_config"


@dataclass(frozen=True, slots=True)
class ApexFlowConfig:
    enabled: bool = False

    min_confidence: float = 0.80
    """Probabilidade minima para executar (padrao 80%, como pedido). Abaixo
    disso a resposta e NAO OPERAR — sempre."""

    min_atr_points: float = 20.0
    """Piso de volatilidade: abaixo dele nenhum alvo paga o custo."""

    max_spread_points: float = 30.0
    max_spread_to_target: float = 0.20
    max_spread_widening: float = 1.6

    tick_window_seconds: int = 120
    """Janela de fluxo analisada a cada decisao."""

    tick_buffer_size: int = 2_000

    min_mtf_alignment: float = 0.25
    """Alinhamento multi-timeframe minimo (valor absoluto) a favor da
    direcao proposta."""

    min_feature_completeness: float = 0.70
    """Fracao minima do feature vector preenchida. Abaixo disso o motor se
    abstem: decidir com metade dos sensores cegos e adivinhar."""

    risk_reward_min: float = 1.5
    trailing_start_r: float = 1.0
    """Multiplos de R a partir dos quais o trailing stop comeca a seguir."""

    trailing_step_r: float = 0.5
    break_even_r: float = 0.8
    """Multiplos de R para mover o stop ao ponto de entrada."""

    daily_profit_target_pct: float = 3.0
    """Limite diario de LUCRO: alcancado, o robo para de operar no dia."""

    max_drawdown_pct: float = 5.0
    model_version: str = ""
    """Vazio = scorecard deterministico. Preenchido = versao aprovada no
    registro de modelos (`app.ml.registry`)."""


def _read_json(repository: SystemSettingRepository) -> dict[str, Any]:
    raw = repository.get(APEXFLOW_CONFIG_KEY)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bounded_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    if parsed < minimum or parsed > maximum:
        return default
    return parsed


def load_apexflow_config(session: Session) -> ApexFlowConfig:
    data = _read_json(SystemSettingRepository(session))
    defaults = ApexFlowConfig()
    return ApexFlowConfig(
        enabled=bool(data.get("enabled", defaults.enabled)),
        min_confidence=_bounded_float(
            data.get("min_confidence"), default=defaults.min_confidence,
            minimum=0.50, maximum=0.99,
        ),
        min_atr_points=_bounded_float(
            data.get("min_atr_points"), default=defaults.min_atr_points,
            minimum=1.0, maximum=10_000.0,
        ),
        max_spread_points=_bounded_float(
            data.get("max_spread_points"), default=defaults.max_spread_points,
            minimum=1.0, maximum=500.0,
        ),
        max_spread_to_target=_bounded_float(
            data.get("max_spread_to_target"), default=defaults.max_spread_to_target,
            minimum=0.01, maximum=0.50,
        ),
        max_spread_widening=_bounded_float(
            data.get("max_spread_widening"), default=defaults.max_spread_widening,
            minimum=1.05, maximum=5.0,
        ),
        tick_window_seconds=_bounded_int(
            data.get("tick_window_seconds"), default=defaults.tick_window_seconds,
            minimum=10, maximum=3_600,
        ),
        tick_buffer_size=_bounded_int(
            data.get("tick_buffer_size"), default=defaults.tick_buffer_size,
            minimum=100, maximum=100_000,
        ),
        min_mtf_alignment=_bounded_float(
            data.get("min_mtf_alignment"), default=defaults.min_mtf_alignment,
            minimum=0.0, maximum=1.0,
        ),
        min_feature_completeness=_bounded_float(
            data.get("min_feature_completeness"),
            default=defaults.min_feature_completeness,
            minimum=0.30, maximum=1.0,
        ),
        risk_reward_min=_bounded_float(
            data.get("risk_reward_min"), default=defaults.risk_reward_min,
            minimum=1.0, maximum=10.0,
        ),
        trailing_start_r=_bounded_float(
            data.get("trailing_start_r"), default=defaults.trailing_start_r,
            minimum=0.2, maximum=5.0,
        ),
        trailing_step_r=_bounded_float(
            data.get("trailing_step_r"), default=defaults.trailing_step_r,
            minimum=0.1, maximum=3.0,
        ),
        break_even_r=_bounded_float(
            data.get("break_even_r"), default=defaults.break_even_r,
            minimum=0.2, maximum=3.0,
        ),
        daily_profit_target_pct=_bounded_float(
            data.get("daily_profit_target_pct"),
            default=defaults.daily_profit_target_pct,
            minimum=0.5, maximum=20.0,
        ),
        max_drawdown_pct=_bounded_float(
            data.get("max_drawdown_pct"), default=defaults.max_drawdown_pct,
            minimum=1.0, maximum=30.0,
        ),
        model_version=str(data.get("model_version", defaults.model_version))[:64],
    )


def save_apexflow_config(session: Session, config: ApexFlowConfig) -> None:
    SystemSettingRepository(session).set(
        APEXFLOW_CONFIG_KEY,
        json.dumps(asdict(config), ensure_ascii=True, separators=(",", ":")),
        description="Parametros do motor de decisao ApexFlow AI.",
    )

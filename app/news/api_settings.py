"""Ajustes da API paga que o operador muda sem editar arquivo nenhum.

Chave e URL base ja moravam em `system_settings` (ver `app/news/factory.py`).
Teto diario e validade do cache continuavam so no `.env` — ou seja, mexer
neles exigia acesso ao servidor. Sao justamente os dois numeros que decidem
quanto a API custa por dia; deixa-los fora do alcance de quem paga a conta
era a parte errada de estar no arquivo.

O `.env` continua sendo o padrao: o que estiver gravado aqui apenas
sobrepoe. Instalacao nova funciona sem configurar nada, e quem ja tem o
`.env` ajustado nao ve mudanca ate mexer no painel.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.database.repositories.system_setting_repository import SystemSettingRepository

BUDGET_LIMIT_SETTING = "marketpulse_daily_budget"
CACHE_TTL_SETTING = "marketpulse_cache_ttl_seconds"

# Faixas aceitas. O teto pode ser 0 (sem limite) porque o modulo de
# orcamento ja da esse significado a zero — mas a tela avisa o que isso
# custa, em vez de deixar o operador descobrir na fatura.
BUDGET_MIN, BUDGET_MAX = 0, 5_000
TTL_MIN, TTL_MAX = 0, 86_400


@dataclass(frozen=True, slots=True)
class ApiRuntimeSettings:
    daily_budget: int
    cache_ttl_seconds: float
    budget_source: str
    """"dashboard" quando veio do painel, "env" quando veio do arquivo."""

    ttl_source: str


def _read_int(session: Session, key: str) -> int | None:
    raw = SystemSettingRepository(session).get(key)
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        # Valor corrompido nao pode virar "sem teto" por acidente: cai para
        # o padrao do .env, que e um numero que alguem escolheu de proposito.
        return None


def load_api_settings(session: Session, settings: Settings) -> ApiRuntimeSettings:
    budget = _read_int(session, BUDGET_LIMIT_SETTING)
    ttl = _read_int(session, CACHE_TTL_SETTING)
    return ApiRuntimeSettings(
        daily_budget=budget if budget is not None else settings.news_daily_call_budget,
        cache_ttl_seconds=(
            float(ttl) if ttl is not None else settings.news_cache_ttl_seconds
        ),
        budget_source="dashboard" if budget is not None else "env",
        ttl_source="dashboard" if ttl is not None else "env",
    )


def validate_api_settings(
    *, daily_budget: int | None, cache_ttl_seconds: int | None
) -> str | None:
    """Primeira faixa violada, em texto pronto para a tela — ou None."""
    if daily_budget is not None and not BUDGET_MIN <= daily_budget <= BUDGET_MAX:
        return f"o teto diario deve ficar entre {BUDGET_MIN} e {BUDGET_MAX} chamadas."
    if cache_ttl_seconds is not None and not TTL_MIN <= cache_ttl_seconds <= TTL_MAX:
        return f"a validade do cache deve ficar entre {TTL_MIN} e {TTL_MAX} segundos."
    return None


def save_api_settings(
    session: Session, *, daily_budget: int | None = None, cache_ttl_seconds: int | None = None
) -> list[str]:
    """Grava o que foi informado e devolve o que mudou, para o log."""
    repo = SystemSettingRepository(session)
    changes: list[str] = []

    if daily_budget is not None:
        repo.set(
            BUDGET_LIMIT_SETTING,
            str(daily_budget),
            description="Teto diario de chamadas a MarketPulse (definido no painel).",
        )
        changes.append(f"teto diario = {daily_budget}")

    if cache_ttl_seconds is not None:
        repo.set(
            CACHE_TTL_SETTING,
            str(cache_ttl_seconds),
            description="Validade do cache de avaliacoes, em segundos (painel).",
        )
        changes.append(f"validade do cache = {cache_ttl_seconds}s")

    return changes

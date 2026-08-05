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
from app.news.store import DEFAULT_REFRESH_HOURS, DEFAULT_RETRY_AFTER_MINUTES

BUDGET_LIMIT_SETTING = "marketpulse_daily_budget"
CACHE_TTL_SETTING = "marketpulse_cache_ttl_seconds"
REFRESH_HOURS_SETTING = "marketpulse_refresh_hours"
RETRY_MINUTES_SETTING = "marketpulse_retry_after_minutes"

# Faixas aceitas. O teto pode ser 0 (sem limite) porque o modulo de
# orcamento ja da esse significado a zero — mas a tela avisa o que isso
# custa, em vez de deixar o operador descobrir na fatura.
BUDGET_MIN, BUDGET_MAX = 0, 5_000
TTL_MIN, TTL_MAX = 0, 86_400
# Uma resposta boa pode valer ate uma semana; uma falha nunca deve
# segurar mais que um dia, senao a API voltar nao adianta nada.
REFRESH_MIN_HOURS, REFRESH_MAX_HOURS = 1, 168
RETRY_MIN_MINUTES, RETRY_MAX_MINUTES = 5, 1_440


@dataclass(frozen=True, slots=True)
class ApiRuntimeSettings:
    daily_budget: int
    cache_ttl_seconds: float
    refresh_hours: int
    """Por quantas horas uma resposta BOA e servida do banco."""

    retry_after_minutes: int
    """Por quantos minutos uma FALHA segura novas tentativas. Era zero na
    pratica — e foi por isso que um endpoint quebrado consumiu a cota."""

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
    refresh = _read_int(session, REFRESH_HOURS_SETTING)
    retry = _read_int(session, RETRY_MINUTES_SETTING)
    return ApiRuntimeSettings(
        daily_budget=budget if budget is not None else settings.news_daily_call_budget,
        cache_ttl_seconds=(
            float(ttl) if ttl is not None else settings.news_cache_ttl_seconds
        ),
        refresh_hours=refresh if refresh is not None else DEFAULT_REFRESH_HOURS,
        retry_after_minutes=(
            retry if retry is not None else DEFAULT_RETRY_AFTER_MINUTES
        ),
        budget_source="dashboard" if budget is not None else "env",
        ttl_source="dashboard" if ttl is not None else "env",
    )


def validate_api_settings(
    *,
    daily_budget: int | None,
    cache_ttl_seconds: int | None,
    refresh_hours: int | None = None,
    retry_after_minutes: int | None = None,
) -> str | None:
    """Primeira faixa violada, em texto pronto para a tela — ou None."""
    if daily_budget is not None and not BUDGET_MIN <= daily_budget <= BUDGET_MAX:
        return f"o teto diario deve ficar entre {BUDGET_MIN} e {BUDGET_MAX} chamadas."
    if cache_ttl_seconds is not None and not TTL_MIN <= cache_ttl_seconds <= TTL_MAX:
        return f"a validade do cache deve ficar entre {TTL_MIN} e {TTL_MAX} segundos."
    if refresh_hours is not None and not (
        REFRESH_MIN_HOURS <= refresh_hours <= REFRESH_MAX_HOURS
    ):
        return (
            f"a validade dos dados deve ficar entre {REFRESH_MIN_HOURS} e "
            f"{REFRESH_MAX_HOURS} horas."
        )
    if retry_after_minutes is not None and not (
        RETRY_MIN_MINUTES <= retry_after_minutes <= RETRY_MAX_MINUTES
    ):
        return (
            f"a espera apos falha deve ficar entre {RETRY_MIN_MINUTES} e "
            f"{RETRY_MAX_MINUTES} minutos."
        )
    return None


def save_api_settings(
    session: Session,
    *,
    daily_budget: int | None = None,
    cache_ttl_seconds: int | None = None,
    refresh_hours: int | None = None,
    retry_after_minutes: int | None = None,
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

    if refresh_hours is not None:
        repo.set(
            REFRESH_HOURS_SETTING,
            str(refresh_hours),
            description="Por quantas horas uma resposta boa e servida do banco.",
        )
        changes.append(f"validade dos dados = {refresh_hours}h")

    if retry_after_minutes is not None:
        repo.set(
            RETRY_MINUTES_SETTING,
            str(retry_after_minutes),
            description="Espera antes de tentar de novo apos falha da API.",
        )
        changes.append(f"espera apos falha = {retry_after_minutes} min")

    return changes

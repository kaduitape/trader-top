"""Modo observacao do radar: liga/desliga pelo painel, roda no worker.

O diario do scanner (`app/market/scan_journal.py`) so tem valor com amostra
acumulada ao longo de semanas. Ate aqui, acumular amostra dependia de alguem
lembrar de rodar `scanner run --record` de tempos em tempos — o que na
pratica significa que a amostra nunca existiria, e a pergunta que o diario
existe para responder ("as escolhas do radar foram melhores que operar um
par fixo?") continuaria sem resposta.

Este modulo transforma isso em configuracao: o painel grava a intencao, o
worker do MetaTrader — que ja roda em laco — executa. Mesmo desenho que
`app/mt5/sync_settings.py` usa para a sincronizacao: a web nunca executa
trabalho longo, so registra o que deve acontecer.

O intervalo e proprio, e nao o do worker, porque as duas coisas tem ritmos
diferentes: sincronizar candles a cada 15s e util, gravar uma escolha do
radar a cada 15s so encheria o diario de linhas quase identicas.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

SCAN_OBSERVATION_SETTING = "scanner_observation"

INTERVAL_MIN_MINUTES = 5
INTERVAL_MAX_MINUTES = 720
DEFAULT_INTERVAL_MINUTES = 30


@dataclass(frozen=True, slots=True)
class ObservationConfig:
    enabled: bool = False
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES
    last_recorded_at: str = ""
    """ISO-8601 do ultimo registro gravado. Vazio = nunca gravou."""

    @property
    def last_recorded(self) -> datetime | None:
        if not self.last_recorded_at:
            return None
        try:
            momento = datetime.fromisoformat(self.last_recorded_at)
        except ValueError:
            return None
        return momento if momento.tzinfo else momento.replace(tzinfo=UTC)

    def next_due_at(self) -> datetime | None:
        anterior = self.last_recorded
        if anterior is None:
            return None
        return anterior + timedelta(minutes=self.interval_minutes)


def clamp_interval(minutes: int) -> int:
    return max(INTERVAL_MIN_MINUTES, min(INTERVAL_MAX_MINUTES, minutes))


def load_observation_config(session: Session) -> ObservationConfig:
    raw = SystemSettingRepository(session).get(SCAN_OBSERVATION_SETTING)
    if not raw:
        return ObservationConfig()
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return ObservationConfig()
    if not isinstance(data, dict):
        return ObservationConfig()

    try:
        intervalo = int(data.get("interval_minutes", DEFAULT_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        intervalo = DEFAULT_INTERVAL_MINUTES

    return ObservationConfig(
        enabled=bool(data.get("enabled", False)),
        interval_minutes=clamp_interval(intervalo),
        last_recorded_at=str(data.get("last_recorded_at", "")),
    )


def save_observation_config(session: Session, config: ObservationConfig) -> None:
    SystemSettingRepository(session).set(
        SCAN_OBSERVATION_SETTING,
        json.dumps(
            {
                "enabled": config.enabled,
                "interval_minutes": clamp_interval(config.interval_minutes),
                "last_recorded_at": config.last_recorded_at,
            }
        ),
        description="Modo observacao do radar: liga/desliga e intervalo (painel).",
    )


def is_due(config: ObservationConfig, *, now: datetime) -> bool:
    """Chegou a hora de gravar mais uma amostra?

    Ligar o modo observacao grava a primeira amostra imediatamente, sem
    esperar um intervalo inteiro: quem acabou de ligar quer ver algo
    acontecer, nao uma tela vazia por meia hora.
    """
    if not config.enabled:
        return False
    proximo = config.next_due_at()
    return proximo is None or now >= proximo


def mark_recorded(
    session: Session, config: ObservationConfig, *, now: datetime
) -> ObservationConfig:
    atualizado = replace(config, last_recorded_at=now.astimezone(UTC).isoformat())
    save_observation_config(session, atualizado)
    return atualizado

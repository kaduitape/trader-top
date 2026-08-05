"""Registro de quando a API paga foi realmente consultada.

O contador de orcamento (`app/news/budget.py`) responde *quanto* foi gasto
hoje. Ele nao responde a pergunta que apareceu primeiro e que e mais util:
**quando, por causa de que, e a mando de quem**. Foi exatamente essa duvida
que levou a descobrir que abrir a tela de analise gastava cota com o robo
desligado.

Por isso cada entrada guarda a ORIGEM. Sem ela o registro viraria uma lista
de horarios sem explicacao, e a pergunta "se esta parado, por que consome?"
continuaria sem resposta.

So chamada de verdade entra aqui. Acerto de cache nunca chega neste modulo —
ele fica atras do cache de proposito, para que a lista signifique
"requisicoes que a corretora da API cobrou", e nao "vezes que o sistema quis
saber de noticias".

Fila circular em `system_settings`, mesmo desenho de `scan_journal`: o
objetivo e diagnostico recente ("o que gastou minha cota hoje"), nao
contabilidade permanente. Uma tabela dedicada exigiria migracao e nao
responderia melhor essa pergunta.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository

CALL_LOG_SETTING = "marketpulse_call_log"
MAX_ENTRIES = 200

ORIGIN_PANEL = "painel"
ORIGIN_ROBOT = "robo"
ORIGIN_CLI = "linha de comando"
ORIGIN_UNKNOWN = "sistema"

# Quem disparou a analise fica em contexto, e nao em parametro, porque o
# caminho entre o clique e a chamada HTTP passa por camadas que nao tem nada
# a ver com origem (analise, pontuacao, cache, orcamento). Enfiar o assunto
# em todas elas so para o log saber quem pediu seria pior que o problema.
_ORIGIN: ContextVar[str] = ContextVar("marketpulse_origin", default=ORIGIN_UNKNOWN)


def current_origin() -> str:
    return _ORIGIN.get()


@contextmanager
def calls_from(origin: str) -> Iterator[None]:
    """Marca a origem das chamadas feitas dentro do bloco."""
    token = _ORIGIN.set(origin)
    try:
        yield
    finally:
        _ORIGIN.reset(token)


@dataclass(frozen=True, slots=True)
class ApiCall:
    at: str
    kind: str
    """"noticias" ou "fundamentos" — cada analise dispara uma de cada."""

    symbol: str
    outcome: str
    origin: str
    duration_ms: int

    @property
    def failed(self) -> bool:
        return self.outcome not in ("OK",)

    @property
    def moment(self) -> datetime | None:
        try:
            momento = datetime.fromisoformat(self.at)
        except ValueError:
            return None
        return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def _serialize(call: ApiCall) -> dict:
    return {
        "at": call.at,
        "kind": call.kind,
        "symbol": call.symbol,
        "outcome": call.outcome,
        "origin": call.origin,
        "ms": call.duration_ms,
    }


def load_calls(session: Session) -> list[ApiCall]:
    """Do mais antigo para o mais novo, como foi gravado."""
    raw = SystemSettingRepository(session).get(CALL_LOG_SETTING)
    if not raw:
        return []
    try:
        registros = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(registros, list):
        return []

    chamadas: list[ApiCall] = []
    for item in registros:
        if not isinstance(item, dict):
            continue
        chamadas.append(
            ApiCall(
                at=str(item.get("at", "")),
                kind=str(item.get("kind", "")),
                symbol=str(item.get("symbol", "")),
                outcome=str(item.get("outcome", "")),
                origin=str(item.get("origin", ORIGIN_UNKNOWN)),
                duration_ms=int(item.get("ms", 0) or 0),
            )
        )
    return chamadas


def record_api_call(
    session: Session,
    *,
    kind: str,
    symbol: str,
    outcome: str,
    duration_ms: int,
    origin: str | None = None,
    now: datetime | None = None,
) -> ApiCall:
    momento = (now or datetime.now(UTC)).astimezone(UTC)
    chamada = ApiCall(
        at=momento.isoformat(),
        kind=kind,
        symbol=symbol,
        outcome=outcome,
        origin=origin or current_origin(),
        duration_ms=max(0, int(duration_ms)),
    )
    historico = [_serialize(item) for item in load_calls(session)]
    historico.append(_serialize(chamada))
    SystemSettingRepository(session).set(
        CALL_LOG_SETTING,
        json.dumps(historico[-MAX_ENTRIES:]),
        description="Ultimas chamadas reais a MarketPulse (fila circular).",
    )
    return chamada


@dataclass(frozen=True, slots=True)
class CallLogSummary:
    total: int
    failures: int
    by_origin: tuple[tuple[str, int], ...]
    last_at: str | None
    average_ms: int | None


def summarize_calls(session: Session) -> CallLogSummary:
    chamadas = load_calls(session)
    if not chamadas:
        return CallLogSummary(
            total=0, failures=0, by_origin=(), last_at=None, average_ms=None
        )

    por_origem: dict[str, int] = {}
    for item in chamadas:
        por_origem[item.origin] = por_origem.get(item.origin, 0) + 1

    return CallLogSummary(
        total=len(chamadas),
        failures=sum(1 for item in chamadas if item.failed),
        by_origin=tuple(sorted(por_origem.items(), key=lambda par: -par[1])),
        last_at=chamadas[-1].at,
        average_ms=round(sum(item.duration_ms for item in chamadas) / len(chamadas)),
    )

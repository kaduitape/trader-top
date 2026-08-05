"""Armazenamento das avaliacoes da API paga, no banco e por dia.

Existiam duas protecoes, e as duas deixaram passar:

- O cache (`app/news/cache.py`) e de MEMORIA e por PROCESSO. O servidor web
  e o worker Windows tem cada um o seu, e todo reinicio comeca vazio. Com
  TTL de 10 minutos e um ciclo a cada 15 segundos, o mesmo par podia ser
  consultado seis vezes por hora, em cada processo.
- E, o pior: **o cache so guarda sucesso**. Enquanto a API respondia erro,
  nada era guardado, entao cada ciclo tentava de novo. Um endpoint quebrado
  virava uma tentativa a cada 15 segundos ate a cota acabar — que foi
  exatamente o que aconteceu.

Este modulo troca as duas premissas. Guarda no BANCO (compartilhado entre
os processos, sobrevive a reinicio) e guarda TAMBEM as falhas, com um prazo
proprio de nova tentativa. Uma resposta boa vale um dia; uma falha segura
novas tentativas por uma hora. O gasto por par passa a ser previsivel:
duas chamadas por dia, mais uma tentativa por hora enquanto algo estiver
quebrado.

Fica em `system_settings` como um mapa por (namespace, tipo, ativo), e nao
em tabela propria, porque o volume e pequeno e conhecido — uma dezena de
pares vezes dois tipos. Se um dia virar historico para analise, migrar
para tabela e o caminho.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.provider import (
    FundamentalsAssessment,
    NewsAssessment,
    NewsItem,
    ProviderStatus,
)

STORE_SETTING = "marketpulse_daily_store"

KIND_NEWS = "noticias"
KIND_FUNDAMENTALS = "fundamentos"

DEFAULT_REFRESH_HOURS = 24
DEFAULT_RETRY_AFTER_MINUTES = 60


@dataclass(frozen=True, slots=True)
class StoredEntry:
    fetched_at: datetime
    status: str
    message: str
    score: float
    items: tuple[dict, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == ProviderStatus.OK.value


def _key(namespace: str, kind: str, symbol: str) -> str:
    return f"{namespace}|{kind}|{symbol.upper()}"


def _load_raw(session: Session) -> dict:
    raw = SystemSettingRepository(session).get(STORE_SETTING)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_moment(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        momento = datetime.fromisoformat(value)
    except ValueError:
        return None
    return momento if momento.tzinfo else momento.replace(tzinfo=UTC)


def load_entry(
    session: Session, *, namespace: str, kind: str, symbol: str
) -> StoredEntry | None:
    bruto = _load_raw(session).get(_key(namespace, kind, symbol))
    if not isinstance(bruto, dict):
        return None
    momento = _parse_moment(bruto.get("at"))
    if momento is None:
        return None
    itens = bruto.get("items")
    return StoredEntry(
        fetched_at=momento,
        status=str(bruto.get("status", "")),
        message=str(bruto.get("message", "")),
        score=float(bruto.get("score", 50.0)),
        items=tuple(item for item in itens if isinstance(item, dict))
        if isinstance(itens, list)
        else (),
    )


def save_entry(
    session: Session, *, namespace: str, kind: str, symbol: str, entry: StoredEntry
) -> None:
    dados = _load_raw(session)
    dados[_key(namespace, kind, symbol)] = {
        "at": entry.fetched_at.astimezone(UTC).isoformat(),
        "status": entry.status,
        "message": entry.message,
        "score": round(entry.score, 4),
        "items": list(entry.items),
    }
    SystemSettingRepository(session).set(
        STORE_SETTING,
        json.dumps(dados),
        description="Avaliacoes MarketPulse guardadas por dia (economia de cota).",
    )


def clear_store(session: Session) -> None:
    SystemSettingRepository(session).set(
        STORE_SETTING, "", description="Armazenamento MarketPulse limpo."
    )


def list_entries(session: Session) -> list[tuple[str, str, StoredEntry]]:
    """(tipo, ativo, registro), do mais recente para o mais antigo."""
    resultado: list[tuple[str, str, StoredEntry]] = []
    for chave, bruto in _load_raw(session).items():
        if not isinstance(bruto, dict):
            continue
        partes = chave.split("|")
        if len(partes) != 3:
            continue
        momento = _parse_moment(bruto.get("at"))
        if momento is None:
            continue
        resultado.append(
            (
                partes[1],
                partes[2],
                StoredEntry(
                    fetched_at=momento,
                    status=str(bruto.get("status", "")),
                    message=str(bruto.get("message", "")),
                    score=float(bruto.get("score", 50.0)),
                ),
            )
        )
    return sorted(resultado, key=lambda item: item[2].fetched_at, reverse=True)


def is_fresh(
    entry: StoredEntry,
    *,
    now: datetime,
    refresh_hours: int,
    retry_after_minutes: int,
) -> bool:
    """Ainda vale servir o que esta guardado?

    Sucesso e falha tem prazos diferentes de proposito. Servir um sucesso
    por 24h economiza cota sem custo real: a leitura de noticias do dia nao
    muda de minuto em minuto. Ja uma falha nao pode valer 24h — se a API
    voltar as 10h, esperar ate amanha seria absurdo. Mas tambem nao pode
    valer zero, que era o comportamento antigo e foi o que consumiu a cota.
    """
    idade = now - entry.fetched_at
    if idade.total_seconds() < 0:
        # Relogio andou para tras (ajuste de horario, restauracao de
        # backup). Tratar como velho e a escolha segura: no maximo gasta
        # uma chamada, contra guardar um registro do futuro para sempre.
        return False
    if entry.ok:
        return idade < timedelta(hours=refresh_hours)
    return idade < timedelta(minutes=retry_after_minutes)


def _age_label(entry: StoredEntry, now: datetime) -> str:
    minutos = max(0, int((now - entry.fetched_at).total_seconds() // 60))
    if minutos < 60:
        return f"{minutos} min"
    if minutos < 1440:
        return f"{minutos // 60} h"
    return f"{minutos // 1440} d"


def _news_to_entry(assessment: NewsAssessment, now: datetime) -> StoredEntry:
    return StoredEntry(
        fetched_at=now,
        status=str(assessment.status),
        message=assessment.message,
        score=assessment.score_contribution,
        items=tuple(
            {
                "headline": item.headline,
                "published_at": item.published_at.astimezone(UTC).isoformat(),
                "impact": item.impact,
                "currency": item.currency,
                "sentiment": item.sentiment,
            }
            for item in assessment.items
        ),
    )


def _entry_to_news(entry: StoredEntry, now: datetime) -> NewsAssessment:
    itens: list[NewsItem] = []
    for bruto in entry.items:
        momento = _parse_moment(bruto.get("published_at"))
        if momento is None:
            continue
        impacto = str(bruto.get("impact", "MEDIUM")).upper()
        itens.append(
            NewsItem(
                headline=str(bruto.get("headline", "")),
                published_at=momento,
                impact=impacto if impacto in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM",  # type: ignore[arg-type]
                currency=bruto.get("currency"),
                sentiment=bruto.get("sentiment"),
            )
        )
    return NewsAssessment(
        status=ProviderStatus(entry.status) if entry.status else ProviderStatus.ERROR,
        score_contribution=entry.score,
        items=itens,
        message=f"{entry.message} [guardado ha {_age_label(entry, now)}]",
    )


class StoredAssessmentProvider:
    """Serve do banco enquanto o registro vale; so entao chama o interno.

    Vale para os dois tipos de avaliacao — a unica diferenca e como o
    resultado vira registro e volta, o que os dois pares de funcoes abaixo
    resolvem.
    """

    def __init__(
        self,
        inner,
        *,
        namespace: str,
        kind: str,
        refresh_hours: int = DEFAULT_REFRESH_HOURS,
        retry_after_minutes: int = DEFAULT_RETRY_AFTER_MINUTES,
    ) -> None:
        self._inner = inner
        self._namespace = namespace
        self._kind = kind
        self._refresh_hours = refresh_hours
        self._retry_after_minutes = retry_after_minutes

    def _session(self):
        from app.database.session import get_session_factory

        return get_session_factory()()

    def fetch_assessment(self, symbol: str, *, now: datetime):
        session = self._session()
        try:
            guardado = load_entry(
                session, namespace=self._namespace, kind=self._kind, symbol=symbol
            )
        finally:
            session.close()

        if guardado is not None and is_fresh(
            guardado,
            now=now,
            refresh_hours=self._refresh_hours,
            retry_after_minutes=self._retry_after_minutes,
        ):
            return self._revive(guardado, now)

        assessment = self._inner.fetch_assessment(symbol, now=now)
        self._store(symbol, assessment, now)
        return assessment

    def _store(self, symbol: str, assessment, now: datetime) -> None:
        """Guarda sucesso E falha.

        Guardar a falha e o ponto do modulo: sem isso, um endpoint quebrado
        e tentado a cada ciclo. Sessao propria com commit proprio porque o
        chamador pode ser uma requisicao de leitura que nunca faz commit.
        """
        if assessment.status == ProviderStatus.SKIPPED:
            # Consulta que nao aconteceu nao descreve a API — guardar isso
            # bloquearia a proxima tentativa legitima.
            return

        session = self._session()
        try:
            save_entry(
                session,
                namespace=self._namespace,
                kind=self._kind,
                symbol=symbol,
                entry=self._to_entry(assessment, now),
            )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _to_entry(self, assessment, now: datetime) -> StoredEntry:
        if isinstance(assessment, NewsAssessment):
            return _news_to_entry(assessment, now)
        return StoredEntry(
            fetched_at=now,
            status=str(assessment.status),
            message=assessment.message,
            score=assessment.score_contribution,
        )

    def _revive(self, entry: StoredEntry, now: datetime):
        if self._kind == KIND_NEWS:
            return _entry_to_news(entry, now)
        return FundamentalsAssessment(
            status=ProviderStatus(entry.status) if entry.status else ProviderStatus.ERROR,
            score_contribution=entry.score,
            message=f"{entry.message} [guardado ha {_age_label(entry, now)}]",
        )


__all__ = [
    "DEFAULT_REFRESH_HOURS",
    "DEFAULT_RETRY_AFTER_MINUTES",
    "KIND_FUNDAMENTALS",
    "KIND_NEWS",
    "STORE_SETTING",
    "StoredAssessmentProvider",
    "StoredEntry",
    "clear_store",
    "is_fresh",
    "list_entries",
    "load_entry",
    "save_entry",
]
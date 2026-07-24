"""Interface plugavel de noticias/fundamentos (Fase 18.6).

Sem uma chave/documentacao real da API "AIsa" citada na especificacao do
usuario, este modulo define apenas o CONTRATO (Protocol) que um provedor
real implementaria — nunca um cliente HTTP adivinhado. O provedor usado
quando nada esta configurado (`UnconfiguredNewsProvider`/
`UnconfiguredFundamentalsProvider`, em `app.news.unconfigured`) reporta
`NOT_CONFIGURED` honestamente e contribui neutro (`score_contribution=50.0`)
no score composto (Fase 18.7) — nunca inventa noticia/sentimento, mesmo
principio ja aplicado ao conector MT5 (falha com mensagem clara em vez de
fabricar uma resposta)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol


class ProviderStatus(enum.StrEnum):
    OK = "OK"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class NewsItem:
    headline: str
    published_at: datetime
    impact: Literal["LOW", "MEDIUM", "HIGH"]
    currency: str | None
    sentiment: float | None


@dataclass(frozen=True, slots=True)
class NewsAssessment:
    status: ProviderStatus
    score_contribution: float
    items: list[NewsItem] = field(default_factory=list)
    message: str = ""


@dataclass(frozen=True, slots=True)
class FundamentalsAssessment:
    status: ProviderStatus
    score_contribution: float
    message: str = ""


class NewsProvider(Protocol):
    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment: ...


class FundamentalsProvider(Protocol):
    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment: ...

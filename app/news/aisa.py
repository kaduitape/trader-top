"""Cliente defensivo do MarketPulse/AIsa para noticias e fundamentos.

Campos ausentes nunca sao tratados como zero: sem evidencia utilizavel, o
fator permanece neutro e a lacuna fica explicita no relatorio.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx

from app.news.provider import FundamentalsAssessment, NewsAssessment, NewsItem, ProviderStatus

_DEFAULT_BASE_URL = "https://api.aisa.one"
_RECORD_KEYS = ("data", "results", "items", "news", "financial_metrics", "metrics")

# O que cada codigo significa para QUEM CONFIGUROU a chave. "HTTP 403" nao
# diz a ninguem o que fazer; "chave aceita, mas sem permissao para este
# endpoint" diz.
_HTTP_HINTS = {
    400: "a API recusou os parametros enviados.",
    401: "chave nao aceita — confira se colou a chave inteira, sem espacos.",
    403: (
        "chave valida, mas sem permissao para este endpoint — normalmente "
        "significa que a skill/plano correspondente nao esta habilitado na "
        "sua conta AIsa."
    ),
    404: "este endpoint nao existe nesta URL base.",
    422: "a API entendeu o pedido mas recusou os valores (ticker invalido?).",
    429: "limite de requisicoes da AIsa atingido.",
    500: "erro do lado da AIsa.",
    502: "erro do lado da AIsa (gateway).",
    503: "a AIsa esta indisponivel no momento.",
}


def describe_failure(exc: Exception) -> str:
    """Transforma a excecao no que aconteceu de fato.

    Existia so `type(exc).__name__` aqui — o relatorio dizia
    "HTTPStatusError" e pronto. Isso torna impossivel distinguir chave
    errada de endpoint errado de cota estourada, que sao tres problemas com
    tres solucoes diferentes. O custo de guardar o codigo e um trecho da
    resposta e baixo; o custo de nao guardar e nao conseguir consertar.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        codigo = exc.response.status_code
        corpo = " ".join((exc.response.text or "").split())[:200]
        dica = _HTTP_HINTS.get(codigo, "")
        partes = [f"HTTP {codigo}"]
        if dica:
            partes.append(dica)
        partes.append(f"Resposta: {corpo or '(vazia)'}")
        return " — ".join(partes)
    if isinstance(exc, httpx.TimeoutException):
        return "tempo esgotado esperando a API responder."
    if isinstance(exc, httpx.ConnectError):
        return (
            "nao foi possivel conectar em api.aisa.one — DNS, rede ou "
            "proxy bloqueando a saida."
        )
    if isinstance(exc, httpx.HTTPError):
        return f"falha de rede ({type(exc).__name__}): {exc}"[:220]
    return f"resposta ilegivel ({type(exc).__name__}): {exc}"[:220]


def _clip(value: float) -> float:
    return max(0.0, min(100.0, value))


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in _RECORD_KEYS:
        nested = payload.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
        if isinstance(nested, Mapping):
            deeper = _records(nested)
            return deeper or [nested]
    return [payload] if payload else []


def _walk_values(record: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if not isinstance(nested, (Mapping, list)):
                flattened.setdefault(normalized, nested)
            elif isinstance(nested, Mapping):
                visit(nested)

    visit(record)
    return flattened


def _first(record: Mapping[str, Any], *names: str) -> Any:
    values = _walk_values(record)
    for name in names:
        value = values.get(name)
        if value is not None:
            return value
    return None


def _number(record: Mapping[str, Any], *names: str) -> float | None:
    value = _first(record, *names)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str) and value.strip():
        candidate = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            return fallback
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    return fallback


class AisaMarketPulseClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        root = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._base_url = root if root.endswith("/apis/v1") else f"{root}/apis/v1"
        self._api_key = api_key
        self._timeout = timeout_seconds

    def get(self, path: str, *, params: dict[str, Any]) -> Any:
        response = httpx.get(
            f"{self._base_url}/{path.lstrip('/')}",
            params=params,
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return response.json()


class AisaNewsProvider:
    def __init__(self, client: AisaMarketPulseClient) -> None:
        self._client = client

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        try:
            payload = self._client.get("financial/news", params={"ticker": symbol, "limit": 20})
            records = _records(payload)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return NewsAssessment(
                status=ProviderStatus.ERROR,
                score_contribution=50.0,
                message=f"Noticias MarketPulse: {describe_failure(exc)}",
            )

        items: list[NewsItem] = []
        sentiments: list[float] = []
        for record in records:
            headline = _first(record, "headline", "title", "name")
            if not headline:
                continue
            raw_sentiment = _number(
                record, "sentiment", "sentiment_score", "news_sentiment", "sentimentscore"
            )
            sentiment = None
            if raw_sentiment is not None:
                sentiment = (
                    max(-1.0, min(1.0, raw_sentiment))
                    if -1.0 <= raw_sentiment <= 1.0
                    else max(-1.0, min(1.0, (raw_sentiment - 50.0) / 50.0))
                )
                sentiments.append(sentiment)

            raw_impact = str(_first(record, "impact", "importance") or "MEDIUM").upper()
            impact = cast(
                Literal["LOW", "MEDIUM", "HIGH"],
                raw_impact if raw_impact in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM",
            )
            published = _parse_datetime(
                _first(
                    record,
                    "published_at",
                    "published_utc",
                    "publishedat",
                    "published_date",
                    "date",
                ),
                fallback=now,
            )
            currency = _first(record, "currency")
            items.append(
                NewsItem(
                    headline=str(headline),
                    published_at=published,
                    impact=impact,
                    currency=str(currency) if currency else None,
                    sentiment=sentiment,
                )
            )

        if not items:
            return NewsAssessment(
                status=ProviderStatus.ERROR,
                score_contribution=50.0,
                message=f"MarketPulse nao retornou noticias utilizaveis para {symbol}.",
            )

        contribution = 50.0
        if sentiments:
            contribution = _clip(50.0 + (sum(sentiments) / len(sentiments)) * 50.0)
        return NewsAssessment(
            status=ProviderStatus.OK,
            score_contribution=contribution,
            items=items,
            message=(
                f"{len(items)} noticia(s) MarketPulse; "
                f"{len(sentiments)} com sentimento quantitativo."
            ),
        )


class AisaFundamentalsProvider:
    def __init__(self, client: AisaMarketPulseClient) -> None:
        self._client = client

    def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
        del now
        try:
            payload = self._client.get(
                "financial/financial-metrics",
                params={"ticker": symbol, "period": "annual", "limit": 4},
            )
            records = _records(payload)
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            return FundamentalsAssessment(
                status=ProviderStatus.ERROR,
                score_contribution=50.0,
                message=f"Fundamentos MarketPulse: {describe_failure(exc)}",
            )

        if not records:
            return FundamentalsAssessment(
                status=ProviderStatus.ERROR,
                score_contribution=50.0,
                message=f"MarketPulse nao retornou fundamentos utilizaveis para {symbol}.",
            )

        latest = records[0]
        checks: list[tuple[str, bool]] = []
        metrics = (
            ("crescimento de receita", _number(latest, "revenue_growth", "revenuegrowth"), 0, None),
            ("margem liquida", _number(latest, "net_profit_margin", "net_margin"), 0, None),
            ("ROE", _number(latest, "return_on_equity", "roe"), 0, None),
            ("ROIC", _number(latest, "return_on_invested_capital", "roic"), 0, None),
            ("P/L", _number(latest, "price_to_earnings_ratio", "pe_ratio", "pe"), 0, 30),
            (
                "EV/EBITDA",
                _number(latest, "enterprise_value_over_ebitda", "ev_to_ebitda", "ev_ebitda"),
                0,
                20,
            ),
        )
        for name, value, lower, upper in metrics:
            if value is not None:
                checks.append((name, value > lower and (upper is None or value <= upper)))

        if not checks:
            return FundamentalsAssessment(
                status=ProviderStatus.ERROR,
                score_contribution=50.0,
                message=f"Resposta MarketPulse sem metricas reconhecidas para {symbol}.",
            )

        positive = sum(1 for _, passed in checks if passed)
        score = _clip(100.0 * positive / len(checks))
        details = ", ".join(
            f"{name}: {'favoravel' if passed else 'desfavoravel'}" for name, passed in checks
        )
        return FundamentalsAssessment(
            status=ProviderStatus.OK,
            score_contribution=score,
            message=f"MarketPulse avaliou {len(checks)} metrica(s): {details}.",
        )

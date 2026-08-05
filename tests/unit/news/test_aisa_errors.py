"""Mensagem de erro da API paga.

Antes o relatorio dizia so "MarketPulse indisponivel (HTTPStatusError)".
Chave errada, endpoint errado e cota estourada sao TRES problemas com tres
solucoes diferentes, e todos apareciam com o mesmo texto — o que torna o
erro impossivel de consertar sem abrir o codigo.
"""

from __future__ import annotations

import httpx
import pytest

from app.news.aisa import AisaNewsProvider, describe_failure


def _status_error(codigo: int, corpo: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.aisa.one/apis/v1/financial/news")
    response = httpx.Response(codigo, text=corpo, request=request)
    return httpx.HTTPStatusError("erro", request=request, response=response)


def test_the_status_code_survives() -> None:
    assert "403" in describe_failure(_status_error(403))


@pytest.mark.parametrize(
    ("codigo", "trecho"),
    [
        (401, "chave nao aceita"),
        (403, "sem permissao"),
        (404, "nao existe"),
        (429, "limite de requisicoes"),
    ],
)
def test_each_code_says_what_to_do(codigo: int, trecho: str) -> None:
    """O codigo sozinho e para quem escreveu a API; a dica e para quem
    configurou a chave."""
    assert trecho in describe_failure(_status_error(codigo))


def test_the_response_body_comes_along() -> None:
    """A API costuma explicar a recusa no corpo — jogar fora e perder a
    unica pista que existe."""
    mensagem = describe_failure(_status_error(403, '{"error":"skill not enabled"}'))

    assert "skill not enabled" in mensagem


def test_an_empty_body_says_so_instead_of_leaving_a_gap() -> None:
    assert "(vazia)" in describe_failure(_status_error(500))


def test_the_body_is_trimmed_so_it_fits_the_screen() -> None:
    mensagem = describe_failure(_status_error(400, "x" * 5_000))

    assert len(mensagem) < 400


def test_a_timeout_is_not_confused_with_a_refusal() -> None:
    """Esperar demais e ser recusado pedem acoes opostas: uma e rede, a
    outra e credencial."""
    mensagem = describe_failure(httpx.TimeoutException("timeout"))

    assert "tempo esgotado" in mensagem
    assert "HTTP" not in mensagem


def test_a_connection_failure_points_at_the_network() -> None:
    mensagem = describe_failure(httpx.ConnectError("dns"))

    assert "conectar" in mensagem


def test_an_unreadable_payload_is_described_too() -> None:
    assert "ilegivel" in describe_failure(ValueError("json invalido"))


# --- ponta a ponta com o provedor -----------------------------------------


class _ClienteQueFalha:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def get(self, path: str, *, params: dict) -> object:
        raise self._exc


def test_the_provider_message_carries_the_detail() -> None:
    from datetime import UTC, datetime

    provider = AisaNewsProvider(
        _ClienteQueFalha(_status_error(401, '{"detail":"invalid api key"}'))
    )

    assessment = provider.fetch_assessment("EURUSD", now=datetime.now(UTC))

    assert "401" in assessment.message
    assert "invalid api key" in assessment.message

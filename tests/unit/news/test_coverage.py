"""O que a API de acoes consegue responder.

O erro que consumiu a cota nao era chave nem URL: era pergunta errada. O
sistema pedia "metricas financeiras anuais do ticker EURUSD" a uma API de
mercado acionario. Par de moedas nao tem balanco — nenhuma chave faz esse
dado existir.

O que estes testes protegem: que a pergunta sem sentido nao saia (custo
zero), que ela vire SKIPPED e nao ERROR (nao houve falha da API), e que a
regra continue conservadora — bloquear so o que se SABE que nao e coberto.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.news.coverage import CoverageGuard, describe_gap, is_covered
from app.news.provider import NewsAssessment, ProviderStatus

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "symbol", ["EURUSD", "GBPUSD", "USDJPY", "AUDNZD", "EURGBP", "gbpjpy"]
)
def test_a_currency_pair_is_not_covered(symbol: str) -> None:
    assert is_covered(symbol) is False


@pytest.mark.parametrize("symbol", ["EURUSD.a", "EURUSD_i", "EURUSDm", "XAUUSD.pro"])
def test_broker_suffixes_do_not_smuggle_the_symbol_through(symbol: str) -> None:
    """Sufixo de corretora nao pode virar um ticker "desconhecido" e passar."""
    assert is_covered(symbol) is False


@pytest.mark.parametrize("symbol", ["XAUUSD", "XAGUSD", "XPTUSD"])
def test_metals_are_not_covered_either(symbol: str) -> None:
    """Ouro nao publica demonstrativo financeiro. `currencies_for_symbol`
    nao pega esse caso porque XAU nao e moeda ISO."""
    assert is_covered(symbol) is False


@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "PETR4"])
def test_a_stock_ticker_still_passes(symbol: str) -> None:
    """A regra bloqueia o que se sabe que nao e coberto, e nao tudo o que
    nao se reconhece: o dia em que o sistema operar acoes, nada aqui muda."""
    assert is_covered(symbol) is True


def test_the_explanation_says_no_quota_was_spent() -> None:
    """Sem isso o operador le "sem dados" e conclui que a assinatura esta
    sendo gasta a toa."""
    texto = describe_gap("EURUSD")

    assert "EURUSD" in texto
    assert "nenhuma cota" in texto.lower()


# --- guarda ----------------------------------------------------------------


class _NuncaChamado:
    def __init__(self) -> None:
        self.chamadas = 0

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        self.chamadas += 1
        return NewsAssessment(status=ProviderStatus.OK, score_contribution=70.0)


def _skipped(message: str) -> NewsAssessment:
    return NewsAssessment(
        status=ProviderStatus.SKIPPED, score_contribution=50.0, message=message
    )


def test_a_currency_pair_never_reaches_the_api() -> None:
    inner = _NuncaChamado()
    guard = CoverageGuard(inner, skipped_factory=_skipped)

    guard.fetch_assessment("EURUSD", now=NOW)

    assert inner.chamadas == 0


def test_the_result_is_skipped_not_error() -> None:
    """ERROR descreveria uma falha da API que nao aconteceu — e, pior,
    ficaria guardado como falha, segurando tentativas legitimas."""
    guard = CoverageGuard(_NuncaChamado(), skipped_factory=_skipped)

    resultado = guard.fetch_assessment("EURUSD", now=NOW)

    assert resultado.status == ProviderStatus.SKIPPED


def test_a_covered_symbol_goes_through() -> None:
    inner = _NuncaChamado()
    guard = CoverageGuard(inner, skipped_factory=_skipped)

    guard.fetch_assessment("AAPL", now=NOW)

    assert inner.chamadas == 1


def test_the_factor_leaves_the_calculation_instead_of_scoring_fifty() -> None:
    """SKIPPED tem que redistribuir o peso. Valer 50 seria fabricar uma
    opiniao neutra a partir de uma pergunta que nunca foi feita."""
    from app.market.scoring import score_news

    guard = CoverageGuard(_NuncaChamado(), skipped_factory=_skipped)
    fator = score_news(guard.fetch_assessment("EURUSD", now=NOW))

    assert fator.has_data is False

"""Armazenamento diario das avaliacoes da API paga.

O caso que este modulo existe para impedir esta no primeiro teste da secao
"gasto": com a API respondendo erro, o cache antigo nao guardava nada e cada
ciclo do worker tentava de novo — a cada 15 segundos, ate a cota acabar.
Guardar a FALHA e o que transforma isso em uma tentativa por hora.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.system_setting_repository import SystemSettingRepository
from app.news.provider import FundamentalsAssessment, NewsAssessment, NewsItem, ProviderStatus
from app.news.store import (
    KIND_FUNDAMENTALS,
    KIND_NEWS,
    STORE_SETTING,
    StoredAssessmentProvider,
    StoredEntry,
    is_fresh,
    list_entries,
    load_entry,
    save_entry,
)

NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
NS = "https://api.aisa.one"


@pytest.fixture(autouse=True)
def _limpa(db_session):
    def apagar() -> None:
        SystemSettingRepository(db_session).set(STORE_SETTING, "")
        db_session.commit()

    apagar()
    yield
    apagar()


class _Contador:
    """Provedor que conta chamadas reais e responde o que mandarem."""

    def __init__(self, status: ProviderStatus = ProviderStatus.OK) -> None:
        self.chamadas = 0
        self.status = status

    def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
        self.chamadas += 1
        return NewsAssessment(
            status=self.status,
            score_contribution=72.0,
            items=[
                NewsItem(
                    headline="BCE mantem juros",
                    published_at=now,
                    impact="HIGH",
                    currency="EUR",
                    sentiment=0.4,
                )
            ],
            message="1 noticia",
        )


def _provider(inner, **kwargs) -> StoredAssessmentProvider:
    base = {"namespace": NS, "kind": KIND_NEWS, "refresh_hours": 24, "retry_after_minutes": 60}
    base.update(kwargs)
    return StoredAssessmentProvider(inner, **base)


# --- persistencia ----------------------------------------------------------


def test_an_entry_survives_a_round_trip(db_session) -> None:
    save_entry(
        db_session,
        namespace=NS,
        kind=KIND_NEWS,
        symbol="EURUSD",
        entry=StoredEntry(fetched_at=NOW, status="OK", message="ok", score=70.0),
    )
    db_session.commit()

    guardado = load_entry(db_session, namespace=NS, kind=KIND_NEWS, symbol="EURUSD")

    assert guardado is not None
    assert guardado.score == 70.0
    assert guardado.ok


def test_symbols_do_not_collide_with_each_other(db_session) -> None:
    for par, nota in (("EURUSD", 70.0), ("GBPUSD", 30.0)):
        save_entry(
            db_session, namespace=NS, kind=KIND_NEWS, symbol=par,
            entry=StoredEntry(fetched_at=NOW, status="OK", message="", score=nota),
        )
    db_session.commit()

    assert load_entry(db_session, namespace=NS, kind=KIND_NEWS, symbol="GBPUSD").score == 30.0


def test_news_and_fundamentals_do_not_collide(db_session) -> None:
    """Sao duas consultas diferentes do mesmo par; misturar serviria a
    resposta de uma como se fosse da outra."""
    for tipo, nota in ((KIND_NEWS, 70.0), (KIND_FUNDAMENTALS, 20.0)):
        save_entry(
            db_session, namespace=NS, kind=tipo, symbol="EURUSD",
            entry=StoredEntry(fetched_at=NOW, status="OK", message="", score=nota),
        )
    db_session.commit()

    assert load_entry(db_session, namespace=NS, kind=KIND_NEWS, symbol="EURUSD").score == 70.0


def test_changing_the_endpoint_invalidates_what_was_stored(db_session) -> None:
    """Trocar a URL base nao pode servir resposta do endpoint anterior."""
    save_entry(
        db_session, namespace=NS, kind=KIND_NEWS, symbol="EURUSD",
        entry=StoredEntry(fetched_at=NOW, status="OK", message="", score=70.0),
    )
    db_session.commit()

    assert load_entry(
        db_session, namespace="https://outra.api", kind=KIND_NEWS, symbol="EURUSD"
    ) is None


def test_a_corrupt_store_starts_over(db_session) -> None:
    SystemSettingRepository(db_session).set(STORE_SETTING, "isso nao e json")
    db_session.commit()

    assert load_entry(db_session, namespace=NS, kind=KIND_NEWS, symbol="EURUSD") is None
    assert list_entries(db_session) == []


# --- validade --------------------------------------------------------------


def _entrada(status: str, *, idade_min: float) -> StoredEntry:
    return StoredEntry(
        fetched_at=NOW - timedelta(minutes=idade_min), status=status, message="", score=50.0
    )


def test_a_good_answer_lasts_the_whole_day() -> None:
    assert is_fresh(_entrada("OK", idade_min=60 * 20), now=NOW, refresh_hours=24, retry_after_minutes=60)


def test_a_good_answer_expires_after_the_configured_window() -> None:
    assert not is_fresh(
        _entrada("OK", idade_min=60 * 25), now=NOW, refresh_hours=24, retry_after_minutes=60
    )


def test_a_failure_does_not_last_a_day() -> None:
    """Se a API voltar as 10h, esperar ate amanha seria absurdo."""
    assert not is_fresh(
        _entrada("ERROR", idade_min=90), now=NOW, refresh_hours=24, retry_after_minutes=60
    )


def test_a_recent_failure_still_blocks_a_retry() -> None:
    """E o oposto que quebrou: falha valendo zero fazia cada ciclo tentar."""
    assert is_fresh(
        _entrada("ERROR", idade_min=10), now=NOW, refresh_hours=24, retry_after_minutes=60
    )


def test_a_timestamp_from_the_future_is_treated_as_stale() -> None:
    """Relogio para tras nao pode congelar um registro para sempre."""
    futuro = StoredEntry(
        fetched_at=NOW + timedelta(hours=5), status="OK", message="", score=50.0
    )

    assert not is_fresh(futuro, now=NOW, refresh_hours=24, retry_after_minutes=60)


# --- gasto (o motivo do modulo) -------------------------------------------


def test_the_second_read_of_the_day_costs_nothing(db_session) -> None:
    inner = _Contador()
    provider = _provider(inner)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW + timedelta(hours=3))

    assert inner.chamadas == 1


def test_a_new_day_reads_again(db_session) -> None:
    inner = _Contador()
    provider = _provider(inner)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW + timedelta(hours=25))

    assert inner.chamadas == 2


def test_a_broken_endpoint_is_not_retried_every_cycle(db_session) -> None:
    """ESTE e o caso que consumiu a cota. Com a API em erro e o cache
    guardando so sucesso, cada ciclo do worker tentava de novo."""
    inner = _Contador(ProviderStatus.ERROR)
    provider = _provider(inner)

    # Um ciclo a cada 15s durante 10 minutos.
    for i in range(40):
        provider.fetch_assessment("EURUSD", now=NOW + timedelta(seconds=15 * i))

    assert inner.chamadas == 1


def test_after_the_retry_window_it_tries_again(db_session) -> None:
    """Guardar a falha nao pode virar desistir dela."""
    inner = _Contador(ProviderStatus.ERROR)
    provider = _provider(inner)

    provider.fetch_assessment("EURUSD", now=NOW)
    provider.fetch_assessment("EURUSD", now=NOW + timedelta(minutes=61))

    assert inner.chamadas == 2


def test_a_skipped_answer_is_not_stored(db_session) -> None:
    """Consulta que nao aconteceu (orcamento esgotado, bloqueio local) nao
    descreve a API — guardar isso bloquearia a proxima tentativa legitima."""

    class _Pulado:
        def fetch_assessment(self, symbol: str, *, now: datetime) -> NewsAssessment:
            return NewsAssessment(
                status=ProviderStatus.SKIPPED, score_contribution=50.0, message="pulado"
            )

    _provider(_Pulado()).fetch_assessment("EURUSD", now=NOW)

    assert load_entry(db_session, namespace=NS, kind=KIND_NEWS, symbol="EURUSD") is None


# --- o que volta -----------------------------------------------------------


def test_the_stored_answer_keeps_the_news_items(db_session) -> None:
    """Servir do banco nao pode empobrecer a analise: as manchetes fazem
    parte do relatorio."""
    provider = _provider(_Contador())

    provider.fetch_assessment("EURUSD", now=NOW)
    devolvido = provider.fetch_assessment("EURUSD", now=NOW + timedelta(hours=2))

    assert devolvido.score_contribution == 72.0
    assert len(devolvido.items) == 1
    assert devolvido.items[0].headline == "BCE mantem juros"


def test_the_answer_says_it_came_from_storage(db_session) -> None:
    """Quem le o relatorio precisa saber que o dado tem idade."""
    provider = _provider(_Contador())

    provider.fetch_assessment("EURUSD", now=NOW)
    devolvido = provider.fetch_assessment("EURUSD", now=NOW + timedelta(hours=2))

    assert "guardado ha 2 h" in devolvido.message


def test_fundamentals_come_back_as_fundamentals(db_session) -> None:
    class _Fundamentos:
        def fetch_assessment(self, symbol: str, *, now: datetime) -> FundamentalsAssessment:
            return FundamentalsAssessment(
                status=ProviderStatus.OK, score_contribution=64.0, message="4 registros"
            )

    provider = _provider(_Fundamentos(), kind=KIND_FUNDAMENTALS)

    provider.fetch_assessment("EURUSD", now=NOW)
    devolvido = provider.fetch_assessment("EURUSD", now=NOW + timedelta(hours=1))

    assert isinstance(devolvido, FundamentalsAssessment)
    assert devolvido.score_contribution == 64.0

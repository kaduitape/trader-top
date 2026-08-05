"""Verificacao do portao de eventos pelo painel.

Existe para responder tres perguntas sem esperar uma noticia: o arquivo
esta sendo lido? os horarios estao no fuso certo? o robo bloquearia AGORA?
Descobrir que a integracao nao funciona no meio de um payroll e o pior
momento possivel para descobrir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.calendar_feed.diagnostics import check_calendar
from app.calendar_feed.factory import reset_calendar_provider
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _sem_cache():
    reset_calendar_provider()
    yield
    reset_calendar_provider()


def _settings(caminho: str | None) -> Settings:
    return Settings(
        app_env="test",
        app_secret_key="chave-de-teste-1234",
        calendar_file_path=caminho,
        calendar_blackout_before_minutes=30,
        calendar_blackout_after_minutes=15,
        calendar_min_impact="HIGH",
    )


def _escreve(tmp_path, eventos: list[dict]) -> str:
    caminho = tmp_path / "calendar.json"
    caminho.write_text(json.dumps(eventos), encoding="utf-8")
    return str(caminho)


def _evento(minutos: float, *, currency: str = "USD", impact: str = "HIGH") -> dict:
    quando = datetime.now(UTC) + timedelta(minutes=minutos)
    return {
        "title": "Non-Farm Payrolls",
        "scheduled_at": quando.isoformat(),
        "currency": currency,
        "impact": impact,
    }


def test_without_a_configured_file_the_filter_is_reported_as_inactive() -> None:
    """"Nao configurado" nao pode parecer "tudo certo": o robo esta operando
    sem essa protecao."""
    resultado = check_calendar(_settings(None), symbol="EURUSD")

    assert resultado.usable is False
    assert resultado.blocked_now is False


def test_a_valid_file_is_read(tmp_path) -> None:
    caminho = _escreve(tmp_path, [_evento(180)])

    resultado = check_calendar(_settings(caminho), symbol="EURUSD")

    assert resultado.usable is True
    assert resultado.status == "OK"
    assert len(resultado.events) == 1


def test_it_says_whether_it_would_block_right_now(tmp_path) -> None:
    """A pergunta que so um evento de verdade respondia antes."""
    caminho = _escreve(tmp_path, [_evento(12)])

    resultado = check_calendar(_settings(caminho), symbol="EURUSD")

    assert resultado.blocked_now is True
    assert "Non-Farm Payrolls" in (resultado.blocking_reason or "")


def test_a_distant_event_does_not_block(tmp_path) -> None:
    caminho = _escreve(tmp_path, [_evento(240)])

    resultado = check_calendar(_settings(caminho), symbol="EURUSD")

    assert resultado.blocked_now is False


def test_an_event_of_another_currency_is_filtered_out(tmp_path) -> None:
    """O portao antigo ignorava a moeda do evento — um dado do Japao
    bloqueava EURUSD."""
    caminho = _escreve(tmp_path, [_evento(12, currency="JPY")])

    resultado = check_calendar(_settings(caminho), symbol="EURUSD")

    assert resultado.blocked_now is False
    assert resultado.events == ()


def test_the_symbol_currencies_are_reported(tmp_path) -> None:
    caminho = _escreve(tmp_path, [_evento(180)])

    resultado = check_calendar(_settings(caminho), symbol="EURUSD")

    assert resultado.currencies == ("EUR", "USD")


def test_a_corrupt_file_is_not_reported_as_ok(tmp_path) -> None:
    caminho = tmp_path / "calendar.json"
    caminho.write_text("isso nao e json", encoding="utf-8")

    resultado = check_calendar(_settings(str(caminho)), symbol="EURUSD")

    assert resultado.usable is False

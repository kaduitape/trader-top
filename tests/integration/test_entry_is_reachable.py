"""A pergunta que ficou sem resposta durante todo o projeto: ele ENTRA?

O sistema nunca fez uma entrada. A explicacao que faltava estava no portao
externo de `analysis_service`: ele exigia `status == OK` de noticias E de
fundamentos. A API da AIsa nao cobre pares de moedas, entao a resposta
nunca era OK — ERROR antes, SKIPPED depois do guarda de cobertura — e os
dois bloqueios disparavam em TODA analise de cambio. Nao era uma questao de
score alto o suficiente: era impossivel por construcao.

Estes testes existem para que isso nunca mais passe despercebido. O
primeiro monta um cenario favoravel e exige `ENTER`. Se alguem reintroduzir
um veto inalcancavel, ele quebra.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.market.multi_timeframe import ANALYSIS_TIMEFRAMES
from app.mt5.market_data import RawCandle, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification
from app.news.provider import (
    FundamentalsAssessment,
    NewsAssessment,
    ProviderStatus,
)
from app.services.analysis_service import analyze_symbol

SYMBOL = "EURUSD"
NOW = datetime(2026, 8, 4, 14, 0, tzinfo=UTC)

_TIMEFRAME_MINUTES = {
    "MN1": 43_200, "W1": 10_080, "D1": 1_440, "H4": 240,
    "H1": 60, "M30": 30, "M15": 15, "M5": 5, "M1": 1,
}


class _Provider:
    """Devolve o status pedido, sem rede."""

    def __init__(self, status: ProviderStatus, kind: str) -> None:
        self._status = status
        self._kind = kind

    def fetch_assessment(self, symbol: str, *, now: datetime):
        corpo = {
            "status": self._status,
            "score_contribution": 70.0,
            "message": f"{self._kind}: {self._status}",
        }
        if self._kind == "noticias":
            return NewsAssessment(**corpo)
        return FundamentalsAssessment(**corpo)


def _candles(minutos: int, total: int = 400) -> list[RawCandle]:
    """Acumulacao em range, rompimento com corpo forte, e climax seguido de
    queda abrupta de volume.

    O ultimo detalhe nao e enfeite: `score_volume` so premia DIVERGENCIA e
    EXAUSTAO — climax e absorcao sozinhos valem 50, que reprova no portao
    local de volume. Sem um climax seguido de secagem, nenhum cenario passa.
    """
    saida: list[RawCandle] = []
    base = 1.05
    for i in range(total):
        instante = NOW - timedelta(minutes=minutos * (total - i))
        restante = total - i
        if restante > 60:
            fase = (i % 20) / 20.0
            preco = base * (1 + 0.0025 * (fase - 0.5))
            abertura = preco
            fechamento = preco * (1.0004 if i % 2 else 0.9996)
            saida.append(
                RawCandle(
                    open_time=instante,
                    open=abertura,
                    high=max(abertura, fechamento) * 1.0004,
                    low=min(abertura, fechamento) * 0.9996,
                    close=fechamento,
                    tick_volume=1_000,
                    spread=2,
                    real_volume=0,
                )
            )
            continue

        passo = 61 - restante
        abertura = base * (1 + 0.0025 * passo * 0.4)
        fechamento = abertura * 1.0022
        if restante in (8, 20):
            volume = 5_000          # climax
        elif restante in (7, 19):
            volume = 400            # secagem -> exaustao
        else:
            volume = 1_000
        saida.append(
            RawCandle(
                open_time=instante,
                open=abertura,
                high=fechamento * 1.00025,
                low=abertura * 0.99975,
                close=fechamento,
                tick_volume=volume,
                spread=2,
                real_volume=0,
            )
        )
    return saida


def _seed_favorable_market(db_session) -> None:
    symbol = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=SYMBOL, description="Euro / Dolar", digits=5, point=0.00001,
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            trade_contract_size=100_000.0, spread=2, trade_mode=4, visible=True,
        )
    )
    repo = CandleRepository(db_session)
    for tf in ANALYSIS_TIMEFRAMES:
        repo.bulk_upsert(symbol.id, tf.value, _candles(_TIMEFRAME_MINUTES[tf.value]))
    db_session.commit()


def _limpa_candles(db_session) -> None:
    """A suite compartilha um banco so, e este arquivo semeia um pico de
    volume proposital. Sem apagar depois, ele viraria "pico atipico de
    volume" na analise de outro arquivo que usa o mesmo par."""
    from sqlalchemy import delete

    from app.database.models.candle import Candle

    symbol = SymbolRepository(db_session).get_by_name(SYMBOL)
    if symbol is not None:
        db_session.execute(delete(Candle).where(Candle.symbol_id == symbol.id))
        db_session.commit()


@pytest.fixture
def mercado(db_session):
    _limpa_candles(db_session)
    _seed_favorable_market(db_session)
    yield db_session
    _limpa_candles(db_session)


def _analyze(db_session, *, news: ProviderStatus, fundamentals: ProviderStatus, threshold=60.0):
    return analyze_symbol(
        db_session,
        symbol=SYMBOL,
        primary_timeframe=Timeframe.M15,
        threshold=threshold,
        now=NOW,
        news_provider=_Provider(news, "noticias"),
        fundamentals_provider=_Provider(fundamentals, "fundamentos"),
    )


def test_a_currency_pair_can_actually_be_entered(mercado) -> None:
    """O teste que faltava. A API nao cobre cambio, entao SKIPPED e o estado
    NORMAL — e ele nao pode significar "nunca opere"."""
    relatorio = _analyze(
        mercado, news=ProviderStatus.SKIPPED, fundamentals=ProviderStatus.SKIPPED
    )

    assert relatorio.score.recommendation == "ENTER", (
        "Nenhuma entrada e possivel em cambio. Motivos: "
        f"{relatorio.score.reasons_below_threshold}"
    )


def test_a_skipped_answer_adds_no_veto(mercado) -> None:
    relatorio = _analyze(
        mercado, news=ProviderStatus.SKIPPED, fundamentals=ProviderStatus.SKIPPED
    )

    motivos = " ".join(relatorio.score.reasons_below_threshold)
    assert "bloqueada por seguranca" not in motivos


def test_the_skipped_factors_leave_the_calculation(mercado) -> None:
    """Peso redistribuido, e nao 50 fabricado: nao perguntar e diferente de
    perguntar e receber "neutro"."""
    relatorio = _analyze(
        mercado, news=ProviderStatus.SKIPPED, fundamentals=ProviderStatus.SKIPPED
    )

    por_nome = {fator.name: fator for fator in relatorio.score.factors}
    assert por_nome["news"].has_data is False
    assert por_nome["fundamentals"].has_data is False


# --- o que AINDA bloqueia, de proposito ------------------------------------


def test_an_api_error_still_blocks(mercado) -> None:
    """Aqui existe pergunta sem resposta: a API foi consultada e falhou. O
    veto continua — a mudanca foi separar "nao sei" de "nao perguntei"."""
    relatorio = _analyze(
        mercado, news=ProviderStatus.ERROR, fundamentals=ProviderStatus.SKIPPED
    )

    assert relatorio.score.recommendation == "DO_NOT_ENTER"
    assert any(
        "Noticias sem confirmacao" in motivo
        for motivo in relatorio.score.reasons_below_threshold
    )


def test_a_missing_configuration_still_blocks(mercado) -> None:
    relatorio = _analyze(
        mercado, news=ProviderStatus.SKIPPED, fundamentals=ProviderStatus.NOT_CONFIGURED
    )

    assert relatorio.score.recommendation == "DO_NOT_ENTER"


def test_an_unreachable_threshold_still_blocks(mercado) -> None:
    """Destravar o veto externo nao pode ter afrouxado o limiar de score."""
    relatorio = _analyze(
        mercado,
        news=ProviderStatus.SKIPPED,
        fundamentals=ProviderStatus.SKIPPED,
        threshold=99.9,
    )

    assert relatorio.score.recommendation == "DO_NOT_ENTER"


def test_the_default_threshold_of_90_is_out_of_reach_in_practice(mercado) -> None:
    """O SEGUNDO portao inalcancavel, e este e de configuracao.

    Com o veto externo consertado, o limiar padrao (90) passa a ser o que
    impede a entrada: um cenario deliberadamente favoravel — tendencia
    limpa, padrao de candle forte, climax com exaustao — chega a ~76. Para
    passar de 90 seria preciso liquidez e volume tambem perto do maximo,
    combinacao que quase nao ocorre.

    Este teste nao defende o numero 76: defende a EXISTENCIA de um teto
    medido. Se alguem mexer nos pesos ou nos fatores, o valor muda e o
    teste avisa — em vez de o operador descobrir por meses sem operacao.
    """
    relatorio = _analyze(
        mercado,
        news=ProviderStatus.SKIPPED,
        fundamentals=ProviderStatus.SKIPPED,
        threshold=60.0,
    )

    assert 70.0 <= relatorio.score.total_score <= 85.0
    assert relatorio.score.total_score < 90.0, (
        "Se o cenario mais favoravel passar de 90, revise este teste — mas "
        "ate la, limiar 90 significa nunca operar."
    )

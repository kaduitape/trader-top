"""FotoAnalise de ponta a ponta, sobre candles reais no banco.

O risco central deste modulo nao e errar uma conta: e virar uma SEGUNDA
analise, divergente da Analise PRO sobre o mesmo mercado. Por isso o teste
mais importante daqui nao verifica um numero — verifica que a tendencia,
o score e os motivos vieram de `analyze_symbol`, e nao de logica local.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.routes.foto_analise import FotoAnaliseIn, build_foto, serialize
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.foto_analise.annotations import ChartAnnotationService
from app.foto_analise.service import FotoAnaliseService
from app.mt5.market_data import RawCandle, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification

SIMBOLO = "FOTOTEST"
INICIO = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpa(engine):
    """O banco em memoria e compartilhado pela suite inteira."""
    del engine

    def apagar() -> None:
        from app.database.models.candle import Candle
        from app.database.models.symbol import Symbol
        from app.database.models.tick import Tick
        from app.database.session import get_session_factory

        sessao = get_session_factory()()
        try:
            registro = SymbolRepository(sessao).get_by_name(SIMBOLO)
            if registro is not None:
                sessao.query(Candle).filter_by(symbol_id=registro.id).delete()
                # Ticks explicitamente: o SQLite nao aplica ON DELETE CASCADE
                # por padrao, e um tick sobrevivente vira preco de outro teste.
                sessao.query(Tick).filter_by(symbol_id=registro.id).delete()
                sessao.query(Symbol).filter_by(id=registro.id).delete()
                sessao.commit()
        finally:
            sessao.close()

    apagar()
    yield
    apagar()


def _semeia(db_session, *, barras: int = 320, tendencia: float = 1.0) -> None:
    """Candles sinteticos com tendencia definida — mas REAIS do ponto de
    vista do sistema: entram pelo mesmo repositorio que o coletor usa."""
    simbolo = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=SIMBOLO,
            description="ativo de teste",
            digits=2,
            point=0.25,
            volume_min=1.0,
            volume_max=100.0,
            volume_step=1.0,
            trade_contract_size=1.0,
            spread=2,
            trade_mode=0,
            visible=True,
        )
    )
    velas: list[RawCandle] = []
    preco = 24500.0
    for i in range(barras):
        abertura = preco
        fechamento = abertura + tendencia * (1.5 if i % 3 else -0.8)
        alta = max(abertura, fechamento) + 1.2
        baixa = min(abertura, fechamento) - 1.2
        velas.append(
            RawCandle(
                open_time=INICIO + timedelta(minutes=15 * i),
                open=abertura,
                high=alta,
                low=baixa,
                close=fechamento,
                tick_volume=1000 + i,
                spread=2,
                real_volume=0,
            )
        )
        preco = fechamento

    CandleRepository(db_session).bulk_upsert(simbolo.id, Timeframe.M15.value, velas)
    db_session.commit()


def _foto(db_session, **kwargs):
    padrao = {"symbol": SIMBOLO, "timeframe": Timeframe.M15, "take_ticks": 20}
    padrao.update(kwargs)
    return FotoAnaliseService(db_session).build(**padrao)


# --- reuso: a razao de ser do modulo ---------------------------------------


def test_the_trend_comes_from_the_existing_analysis(db_session, monkeypatch) -> None:
    """Se o FotoAnalise classificasse tendencia por conta propria, duas
    telas do mesmo sistema poderiam discordar sobre o mesmo mercado."""
    _semeia(db_session)
    chamadas: list[str] = []

    from app.foto_analise import service as modulo

    original = modulo.analyze_symbol

    def espiao(*args, **kwargs):
        chamadas.append(kwargs.get("symbol", ""))
        return original(*args, **kwargs)

    monkeypatch.setattr(modulo, "analyze_symbol", espiao)

    foto = _foto(db_session)

    assert chamadas == [SIMBOLO], "a analise existente precisa ser a fonte"
    assert foto.score >= 0


def test_the_score_is_the_analysis_score(db_session) -> None:
    from app.services.analysis_service import analyze_symbol

    _semeia(db_session)
    relatorio = analyze_symbol(
        db_session, symbol=SIMBOLO, primary_timeframe=Timeframe.M15, enforce_gates=False
    )

    assert _foto(db_session).score == round(relatorio.score.total_score, 1)


def test_the_candles_are_the_real_ones(db_session) -> None:
    """Nada e sintetizado no desenho: sao as candles do banco."""
    _semeia(db_session)
    foto = _foto(db_session)

    ultima = foto.candles[-1]
    assert foto.current_price == ultima.close
    assert ultima.high >= ultima.close >= ultima.low


# --- o take participa ------------------------------------------------------


def test_the_take_changes_the_map(db_session) -> None:
    """Foi o requisito declarado como importante: a mesma tela precisa
    responder diferente para objetivos diferentes."""
    _semeia(db_session)

    curto = _foto(db_session, take_ticks=10)
    longo = _foto(db_session, take_ticks=200)

    assert [b.buy_score for b in curto.heatmap] != [b.buy_score for b in longo.heatmap]


def test_the_take_price_follows_what_was_asked(db_session) -> None:
    _semeia(db_session)
    foto = _foto(db_session, take_ticks=20)

    if foto.entry_zone is not None and foto.take is not None:
        distancia = abs(foto.take - foto.entry_zone.sweet_spot)
        assert abs(distancia - 20 * foto.tick_size) < foto.tick_size


# --- direcao ---------------------------------------------------------------


def test_auto_follows_the_analysed_trend(db_session) -> None:
    _semeia(db_session, tendencia=1.0)

    assert _foto(db_session, direction="AUTO").bias == "LONG"


def test_a_forced_direction_wins(db_session) -> None:
    """Quem pede "so venda" quer ver a melhor venda possivel; recusar seria
    responder outra pergunta."""
    _semeia(db_session, tendencia=1.0)

    assert _foto(db_session, direction="VENDA").bias == "SHORT"


# --- ausencia de dados -----------------------------------------------------


def test_a_symbol_without_candles_is_a_404(db_session) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        build_foto(db_session, FotoAnaliseIn(symbol="NAOEXISTE", timeframe="M15"))

    assert exc.value.status_code == 404


def test_too_few_candles_warns_instead_of_crashing(db_session) -> None:
    _semeia(db_session, barras=3)

    foto = _foto(db_session)

    assert foto.decision == "SEM_ENTRADA"
    assert foto.warnings


# --- contrato de saida -----------------------------------------------------


def test_the_payload_matches_the_documented_shape(db_session) -> None:
    _semeia(db_session)
    dados = serialize(_foto(db_session))

    for chave in (
        "decision", "bias", "score", "current_price",
        "entry_zone", "stop", "take", "status", "heatmap",
    ):
        assert chave in dados

    if dados["heatmap"]:
        primeira = dados["heatmap"][0]
        assert {"price", "buy_score", "sell_score"} <= set(primeira)


def test_nothing_claims_probability_of_profit(db_session) -> None:
    """Restricao explicita do pedido: sem estatistica historica, chamar
    confluencia de "chance de ganho" seria um numero que faz alguem
    arriscar dinheiro por um motivo falso."""
    _semeia(db_session)
    texto = str(serialize(_foto(db_session))).lower()

    for proibido in ("chance de ganh", "probabilidade de lucro", "% de acerto"):
        assert proibido not in texto


# --- desenho ---------------------------------------------------------------


def test_the_chart_renders_real_candles_and_zones(db_session) -> None:
    _semeia(db_session)
    svg = ChartAnnotationService().render(_foto(db_session))

    assert svg.startswith("<svg")
    assert svg.count("<rect") > 10, "os candles reais precisam estar no desenho"
    assert "</svg>" in svg


def test_the_projection_is_labelled_as_such(db_session) -> None:
    """Seta solida leria como afirmacao sobre o futuro."""
    _semeia(db_session)
    foto = _foto(db_session)

    if foto.entry_zone is not None and foto.take is not None:
        svg = ChartAnnotationService().render(foto)
        assert "projecao do cenario, nao previsao" in svg
        assert "stroke-dasharray" in svg


def test_the_chart_survives_without_candles(db_session) -> None:
    """Zero candles e um estado real (simbolo recem-cadastrado). Desenhar
    um grafico vazio seria pior que dizer o que falta."""
    from dataclasses import replace

    _semeia(db_session, barras=3)
    vazia = replace(_foto(db_session), candles=[])

    svg = ChartAnnotationService().render(vazia)

    assert "Sem candles" in svg
    assert "<svg" not in svg


def test_the_scale_includes_stop_and_take(db_session) -> None:
    """Enquadrar so pelos candles esconderia justamente os dois niveis que
    respondem "vale a pena?"."""
    _semeia(db_session)
    foto = _foto(db_session)

    if foto.stop is not None:
        escala = ChartAnnotationService()._scale(foto)
        assert escala.low <= foto.stop <= escala.high
        assert escala.low <= foto.take <= escala.high


# --- a pagina --------------------------------------------------------------


@pytest.fixture
def logged_in(client, db_session, request):
    from app.core.security import hash_password
    from app.database.repositories.user_repository import UserRepository

    username = f"foto_{abs(hash(request.node.name)) % 10**8}"
    repo = UserRepository(db_session)
    papel = repo.get_or_create_role("ADMIN")
    repo.create_user(
        username=username,
        email=f"{username}@example.com",
        password_hash=hash_password("Sup3rSecret!"),
        roles=[papel],
    )
    db_session.commit()
    client.post(
        "/login",
        data={"username": username, "password": "Sup3rSecret!"},
        follow_redirects=False,
    )
    return client


def test_the_page_renders_the_chart(logged_in, db_session) -> None:
    """Erro de template so aparece renderizando de verdade — testar o
    servico sozinho deixaria a tela quebrada passar."""
    _semeia(db_session)

    resposta = logged_in.get(f"/dashboard/foto-analise?symbol={SIMBOLO}&timeframe=M15")

    assert resposta.status_code == 200
    assert "<svg" in resposta.text
    assert "GERAR FOTO ANÁLISE" in resposta.text


def test_the_page_answers_the_seven_questions(logged_in, db_session) -> None:
    """A regra visual do pedido: tendencia, lado, agora ou esperar, melhor
    regiao, sweet spot, take e invalidacao — tudo numa olhada."""
    _semeia(db_session)

    texto = logged_in.get(
        f"/dashboard/foto-analise?symbol={SIMBOLO}&timeframe=M15&take_ticks=20"
    ).text

    for esperado in ("Força do setup", "Sweet spot", "Take", "Stop", "Status"):
        assert esperado in texto


def test_the_heatmap_view_shows_buy_and_sell_scores(logged_in, db_session) -> None:
    _semeia(db_session)

    texto = logged_in.get(
        f"/dashboard/foto-analise?symbol={SIMBOLO}&timeframe=M15&view=HEATMAP"
    ).text

    assert "Buy / Sell score por faixa" in texto


def test_an_unknown_symbol_shows_a_message_not_a_stacktrace(logged_in) -> None:
    resposta = logged_in.get("/dashboard/foto-analise?symbol=NAOEXISTE&timeframe=M15")

    assert resposta.status_code == 200
    assert "Dados de mercado" in resposta.text


def test_the_page_requires_login(client) -> None:
    resposta = client.get("/dashboard/foto-analise", follow_redirects=False)

    assert resposta.status_code in (302, 303, 401)


# --- atualidade dos dados --------------------------------------------------


def test_stale_data_is_announced_not_drawn_silently(db_session) -> None:
    """Uma foto bonita sobre precos de ontem PARECE atual — e e assim que
    alguem opera em cima do passado sem perceber."""
    _semeia(db_session)

    # A semente termina em INICIO + 320*15min; "agora" muito depois disso.
    foto = _foto(db_session, now=INICIO + timedelta(days=30))

    assert foto.is_stale is True
    assert foto.data_age_minutes > 0
    assert any("DESATUALIZADOS" in aviso for aviso in foto.warnings)


def test_fresh_data_is_not_flagged(db_session) -> None:
    _semeia(db_session)
    ultima = INICIO + timedelta(minutes=15 * 319)

    foto = _foto(db_session, now=ultima + timedelta(minutes=5))

    assert foto.is_stale is False


def test_the_staleness_limit_follows_the_timeframe(db_session) -> None:
    """30 minutos de atraso e irrelevante no D1 e inaceitavel no M1 — um
    limite fixo estaria errado nos dois casos."""
    _semeia(db_session)
    ultima = INICIO + timedelta(minutes=15 * 319)

    dentro = _foto(db_session, now=ultima + timedelta(minutes=40))
    fora = _foto(db_session, now=ultima + timedelta(minutes=50))

    assert dentro.is_stale is False, "40min < 3 barras de M15"
    assert fora.is_stale is True, "50min > 3 barras de M15"


def test_the_stale_banner_is_impossible_to_miss(db_session) -> None:
    _semeia(db_session)
    foto = _foto(db_session, now=INICIO + timedelta(days=30))

    svg = ChartAnnotationService().render(foto)

    assert "DADOS DESATUALIZADOS" in svg


def test_a_fresh_chart_has_no_banner(db_session) -> None:
    _semeia(db_session)
    ultima = INICIO + timedelta(minutes=15 * 319)

    svg = ChartAnnotationService().render(
        _foto(db_session, now=ultima + timedelta(minutes=5))
    )

    assert "DADOS DESATUALIZADOS" not in svg


def test_the_last_candle_is_the_newest_one(db_session) -> None:
    """O pedido literal: pegar a candle mais atualizada, nao uma antiga."""
    _semeia(db_session)
    foto = _foto(db_session)

    esperado = INICIO + timedelta(minutes=15 * 319)
    assert foto.last_candle_at.replace(tzinfo=UTC) == esperado
    assert foto.candles[-1].time.replace(tzinfo=UTC) == esperado


# --- preco mais atual ------------------------------------------------------


def _semeia_tick(db_session, *, quando, bid: float, ask: float) -> None:
    from app.database.repositories.tick_repository import TickRepository
    from app.mt5.market_data import RawTick

    simbolo = SymbolRepository(db_session).get_by_name(SIMBOLO)
    TickRepository(db_session).bulk_upsert(
        simbolo.id,
        [RawTick(timestamp=quando, bid=bid, ask=ask, last=bid, volume=1, flags=0)],
    )
    db_session.commit()


def test_a_newer_tick_wins_over_the_closed_candle(db_session) -> None:
    """A coleta so grava candles FECHADAS: a ultima ja nasce ate um
    timeframe atrasada. Com tick mais novo, ele e o preco de verdade."""
    _semeia(db_session)
    ultima = INICIO + timedelta(minutes=15 * 319)
    _semeia_tick(db_session, quando=ultima + timedelta(minutes=5), bid=25000.0, ask=25000.5)

    foto = _foto(db_session, now=ultima + timedelta(minutes=6))

    assert foto.price_source == "TICK"
    assert foto.current_price == 25000.25


def test_an_older_tick_is_ignored(db_session) -> None:
    """Tick antigo nao pode sobrescrever um fechamento mais novo."""
    _semeia(db_session)
    _semeia_tick(db_session, quando=INICIO + timedelta(minutes=10), bid=1.0, ask=1.5)

    foto = _foto(db_session)

    assert foto.price_source == "CANDLE"
    assert foto.current_price == foto.candles[-1].close


def test_without_ticks_the_close_is_used(db_session) -> None:
    _semeia(db_session)

    foto = _foto(db_session)

    assert foto.price_source == "CANDLE"


# --- clareza dos sinais ----------------------------------------------------


def test_the_chart_shows_where_we_are(db_session) -> None:
    """"Onde estamos" e a primeira pergunta da tela; sem esta linha o
    operador deduz o preco pela ponta dos candles."""
    _semeia(db_session)

    svg = ChartAnnotationService().render(_foto(db_session))

    assert "AGORA" in svg


def test_the_key_markers_are_labelled_on_the_chart(db_session) -> None:
    _semeia(db_session)
    foto = _foto(db_session)

    svg = ChartAnnotationService().render(foto)

    assert "MELHOR ENTRADA" in svg
    assert "TAKE" in svg
    assert "STOP / INVALIDACAO" in svg


def test_labels_have_a_solid_background(db_session) -> None:
    """Texto colorido sobre candles e mapa de calor fica ilegivel
    exatamente onde importa: em cima da zona de entrada."""
    _semeia(db_session)

    svg = ChartAnnotationService().render(_foto(db_session))

    assert 'rx="3"' in svg, "os rotulos precisam de fundo solido"

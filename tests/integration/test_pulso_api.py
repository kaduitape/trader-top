"""API publica do Pulso e as chaves que a protegem.

Dois riscos dominam aqui, e nenhum e "o numero saiu errado".

O primeiro e de SEGURANCA: esta rota existe para ser chamada de fora, com
uma credencial de vida longa. Se ela aceitasse chave revogada, vazasse o
segredo de volta, ou permitisse mais do que ler analise, o custo nao seria
um grafico feio.

O segundo e de CONTRATO: quem consome e MQL5, sem parser de JSON. Um campo
que vira `null`, ou um numero que vira string, quebra o indicador em
silencio — ele desenha zero e ninguem percebe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.repositories.api_token_repository import ApiTokenRepository
from app.database.repositories.candle_repository import CandleRepository
from app.database.repositories.symbol_repository import SymbolRepository
from app.mt5.market_data import RawCandle, Timeframe
from app.mt5.symbol_mapper import SymbolSpecification

SIMBOLO = "PULSOAPI"
INICIO = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _limpa(engine):
    del engine

    def apagar() -> None:
        from app.database.models.api_token import ApiToken
        from app.database.models.audit_log import AuditLog
        from app.database.models.candle import Candle
        from app.database.models.symbol import Symbol
        from app.database.models.system_setting import SystemSetting
        from app.database.session import get_session_factory

        sessao = get_session_factory()()
        try:
            registro = SymbolRepository(sessao).get_by_name(SIMBOLO)
            if registro is not None:
                sessao.query(Candle).filter_by(symbol_id=registro.id).delete()
                sessao.query(Symbol).filter_by(id=registro.id).delete()
            sessao.query(ApiToken).delete()
            # So as acoes destes testes: o audit log e de todo mundo.
            sessao.query(AuditLog).filter(
                AuditLog.action.in_(("foto_analise_toggle", "api_token_create"))
            ).delete(synchronize_session=False)
            sessao.query(SystemSetting).filter(
                SystemSetting.key == "foto_analise.enabled_symbols"
            ).delete()
            sessao.commit()
        finally:
            sessao.close()

    apagar()
    yield
    apagar()


def _semeia(db_session, *, barras: int = 320) -> None:
    simbolo = SymbolRepository(db_session).upsert_from_specification(
        SymbolSpecification(
            name=SIMBOLO, description="teste", digits=2, point=0.25,
            volume_min=1.0, volume_max=100.0, volume_step=1.0,
            trade_contract_size=1.0, spread=2, trade_mode=0, visible=True,
        )
    )
    velas: list[RawCandle] = []
    preco = 24500.0
    for i in range(barras):
        fechamento = preco + (1.5 if i % 3 else -0.8)
        velas.append(
            RawCandle(
                open_time=INICIO + timedelta(minutes=15 * i),
                open=preco,
                high=max(preco, fechamento) + 1.2,
                low=min(preco, fechamento) - 1.2,
                close=fechamento,
                tick_volume=1000 + i,
                spread=2,
                real_volume=0,
            )
        )
        preco = fechamento
    CandleRepository(db_session).bulk_upsert(simbolo.id, Timeframe.M15.value, velas)
    db_session.commit()


@pytest.fixture
def chave(db_session) -> str:
    _, segredo = ApiTokenRepository(db_session).create(name="teste")
    db_session.commit()
    return segredo


def _cabecalho(segredo: str) -> dict:
    return {"X-API-Key": segredo}


# --- autenticacao ----------------------------------------------------------


def test_without_a_key_there_is_no_analysis(client, db_session) -> None:
    _semeia(db_session)

    resposta = client.get(f"/api/pulso?symbol={SIMBOLO}")

    assert resposta.status_code == 401


def test_a_wrong_key_is_refused(client, db_session) -> None:
    _semeia(db_session)

    resposta = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho("tt_falsa"))

    assert resposta.status_code == 401


def test_a_revoked_key_stops_working(client, db_session, chave) -> None:
    """Revogar precisa ter efeito imediato: uma chave que continua valendo
    ate reiniciar o servidor nao e revogacao, e adiamento."""
    _semeia(db_session)
    repo = ApiTokenRepository(db_session)
    repo.revoke(repo.list_all()[0].id)
    db_session.commit()

    resposta = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave))

    assert resposta.status_code == 401


def test_a_valid_key_works(client, db_session, chave) -> None:
    _semeia(db_session)

    resposta = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave))

    assert resposta.status_code == 200


def test_the_secret_is_never_stored_in_clear(db_session) -> None:
    """Se o banco guardasse o segredo, um dump dele daria acesso a API."""
    registro, segredo = ApiTokenRepository(db_session).create(name="x")
    db_session.commit()

    assert segredo not in registro.token_hash
    assert len(registro.token_hash) == 64
    assert registro.prefix in segredo


def test_usage_is_recorded(client, db_session, chave) -> None:
    """Uma chave que parou de ser usada e um indicador que caiu."""
    _semeia(db_session)

    client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave))

    db_session.expire_all()
    registro = ApiTokenRepository(db_session).list_all()[0]
    assert registro.request_count >= 1
    assert registro.last_used_at is not None


# --- contrato com o MQL5 ---------------------------------------------------


def test_no_field_is_null(client, db_session, chave) -> None:
    """`null` obrigaria o parser do MQL5 a distinguir ausencia de zero
    dentro de uma string — e ele nao tem como."""
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    for campo, valor in dados.items():
        assert valor is not None, f"{campo} veio null"


def test_absence_is_a_flag_plus_zero(client, db_session, chave) -> None:
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    for flag, valor in (
        ("has_entry", "entry_min"), ("has_take", "take"), ("has_stop", "stop"),
    ):
        assert isinstance(dados[flag], bool)
        assert isinstance(dados[valor], (int, float))


def test_numbers_are_numbers_not_strings(client, db_session, chave) -> None:
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    for campo in ("score", "price", "tick_size", "take", "stop"):
        assert isinstance(dados[campo], (int, float)), f"{campo} nao e numero"


def test_zones_are_flat_and_self_describing(client, db_session, chave) -> None:
    """O indicador percorre a lista sem conhecer a semantica: adicionar uma
    zona nova no servidor nao pode exigir recompilar o MQL5."""
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["zones"], "sem zonas para desenhar"
    for zona in dados["zones"]:
        assert set(zona) == {"kind", "label", "price_min", "price_max", "color", "score"}
        assert zona["kind"] in {"ENTRY", "RISK", "HEAT"}
        assert zona["color"] in {"GREEN", "YELLOW", "RED"}


def test_the_headline_comes_ready_to_print(client, db_session, chave) -> None:
    """Formatar no MQL5 exigiria replicar as regras de arredondamento por
    tick — duas formatacoes divergem, e a divergencia viraria dois precos
    diferentes para o mesmo nivel."""
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["headline"]
    assert isinstance(dados["headline"], str)


def test_the_contract_is_versioned(client, db_session, chave) -> None:
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["contract_version"] >= 1


def test_stale_data_reaches_the_indicator(client, db_session, chave) -> None:
    """O indicador precisa saber que os dados pararam; senao ele desenha
    zonas de ontem sobre o preco de hoje."""
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["is_stale"] is True
    assert "DESATUALIZADOS" in dados["headline"]


# --- o interruptor ---------------------------------------------------------


def test_toggling_off_returns_200_not_an_error(client, db_session, chave) -> None:
    """O indicador precisa distinguir "voce desligou" de "o servidor caiu".
    Um 4xx aqui mostraria falha de conexao para uma escolha do operador."""
    _semeia(db_session)
    client.post(
        "/api/pulso/toggle",
        json={"symbol": SIMBOLO, "enabled": False},
        headers=_cabecalho(chave),
    )

    resposta = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave))

    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["enabled"] is False
    assert dados["zones"] == []
    assert "DESLIGADA" in dados["headline"]


def test_toggling_back_on_restores_the_analysis(client, db_session, chave) -> None:
    _semeia(db_session)
    for estado in (False, True):
        client.post(
            "/api/pulso/toggle",
            json={"symbol": SIMBOLO, "enabled": estado},
            headers=_cabecalho(chave),
        )

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["enabled"] is True
    assert dados["zones"]


def test_the_toggle_is_per_symbol(client, db_session, chave) -> None:
    """Quem opera dois ativos quer silenciar um sem apagar o outro."""
    _semeia(db_session)
    client.post(
        "/api/pulso/toggle",
        json={"symbol": "OUTRO", "enabled": False},
        headers=_cabecalho(chave),
    )

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["enabled"] is True


def test_a_new_symbol_starts_enabled(client, db_session, chave) -> None:
    """Guardar os desligados, e nao os ligados: um ativo novo nascendo mudo
    faria o operador procurar bug onde ha configuracao."""
    _semeia(db_session)

    dados = client.get(
        f"/api/pulso/status?symbol={SIMBOLO}", headers=_cabecalho(chave)
    ).json()

    assert dados["enabled"] is True


def test_the_toggle_is_audited(client, db_session, chave) -> None:
    """Mudanca de estado feita de fora do painel precisa deixar rastro de
    qual chave a fez."""
    from app.database.models.audit_log import AuditLog

    _semeia(db_session)
    client.post(
        "/api/pulso/toggle",
        json={"symbol": SIMBOLO, "enabled": False},
        headers=_cabecalho(chave),
    )

    db_session.expire_all()
    registros = db_session.query(AuditLog).filter_by(action="foto_analise_toggle").all()
    assert len(registros) == 1
    assert chave not in (registros[0].detail or ""), "a chave vazou para o log"


def test_the_toggle_needs_a_key(client, db_session) -> None:
    _semeia(db_session)

    resposta = client.post(
        "/api/pulso/toggle", json={"symbol": SIMBOLO, "enabled": False}
    )

    assert resposta.status_code == 401


# --- limites ---------------------------------------------------------------


def test_an_unknown_symbol_is_a_404_with_instructions(client, chave) -> None:
    resposta = client.get("/api/pulso?symbol=NAOEXISTE", headers=_cabecalho(chave))

    assert resposta.status_code == 404
    assert "Dados de mercado" in resposta.json()["detail"]


def test_an_invalid_timeframe_is_refused(client, db_session, chave) -> None:
    _semeia(db_session)

    resposta = client.get(
        f"/api/pulso?symbol={SIMBOLO}&timeframe=M7", headers=_cabecalho(chave)
    )

    assert resposta.status_code == 422


def test_the_route_never_touches_orders() -> None:
    """Garantia estrutural, nao promessa: o modulo nao importa nada de
    execucao. Um botao no grafico do MetaTrader parece um botao de robo, e
    alguem vai clicar nele achando que esta parando o robo."""
    import ast
    import pathlib

    fonte = pathlib.Path("app/api/routes/pulso_api.py").read_text()
    arvore = ast.parse(fonte)

    importados: list[str] = []
    for no in ast.walk(arvore):
        if isinstance(no, ast.ImportFrom) and no.module:
            importados.append(no.module)
        elif isinstance(no, ast.Import):
            importados.extend(alias.name for alias in no.names)

    for proibido in ("app.execution", "app.paper_trading", "app.mt5.orders"):
        assert not any(m.startswith(proibido) for m in importados), proibido


# --- a headline diz de onde vieram os numeros ------------------------------


def test_the_headline_always_names_symbol_and_timeframe(client, db_session, chave) -> None:
    """`SymbolOverride` errado desenha niveis de outro ativo no grafico. O
    caso normal — em que tudo parece funcionar — era o unico que nao dizia
    de onde os numeros vieram."""
    _semeia(db_session)

    dados = client.get(
        f"/api/pulso?symbol={SIMBOLO}&timeframe=M15", headers=_cabecalho(chave)
    ).json()

    assert SIMBOLO in dados["headline"]
    assert "M15" in dados["headline"]


def test_the_disabled_headline_also_names_the_origin(client, db_session, chave) -> None:
    _semeia(db_session)
    client.post(
        "/api/pulso/toggle",
        json={"symbol": SIMBOLO, "enabled": False},
        headers=_cabecalho(chave),
    )

    dados = client.get(
        f"/api/pulso?symbol={SIMBOLO}&timeframe=M15", headers=_cabecalho(chave)
    ).json()

    assert SIMBOLO in dados["headline"]
    assert "M15" in dados["headline"]


def test_the_tick_size_reaches_the_indicator(client, db_session, chave) -> None:
    """O EA usa este valor para dimensionar a area de risco. Com o `_Point`
    do grafico no lugar dele, num MNQ a area sai 25x menor."""
    _semeia(db_session)

    dados = client.get(f"/api/pulso?symbol={SIMBOLO}", headers=_cabecalho(chave)).json()

    assert dados["tick_size"] == 0.25


# --- download do indicador -------------------------------------------------


@pytest.fixture
def logado(client, db_session, request):
    from app.core.security import hash_password
    from app.database.repositories.user_repository import UserRepository

    nome = f"ind_{abs(hash(request.node.name)) % 10**8}"
    repo = UserRepository(db_session)
    repo.create_user(
        username=nome,
        email=f"{nome}@example.com",
        password_hash=hash_password("Sup3rSecret!"),
        roles=[repo.get_or_create_role("ADMIN")],
    )
    db_session.commit()
    client.post(
        "/login",
        data={"username": nome, "password": "Sup3rSecret!"},
        follow_redirects=False,
    )
    return client


def test_the_indicator_can_be_downloaded(logado) -> None:
    """Quem instala esta com o painel aberto na frente — e a versao servida
    aqui e a que conversa com ESTE servidor."""
    resposta = logado.get("/dashboard/mt5/indicator")

    assert resposta.status_code == 200
    assert "AITraderPulse.mq5" in resposta.headers["content-disposition"]
    assert "attachment" in resposta.headers["content-disposition"]


def test_the_downloaded_file_is_the_real_source(logado) -> None:
    """Servir uma copia desatualizada seria pior que nao servir: o operador
    instalaria um indicador que nao bate com a API."""
    import pathlib

    resposta = logado.get("/dashboard/mt5/indicator")
    original = pathlib.Path("scripts/mql5/AITraderPulse.mq5").read_text()

    assert resposta.text == original
    assert "CONTRACT_SUPPORTED" in resposta.text


def test_the_download_requires_login(client) -> None:
    resposta = client.get("/dashboard/mt5/indicator", follow_redirects=False)

    assert resposta.status_code in (302, 303, 401)


def test_a_missing_file_says_what_to_do(logado, monkeypatch) -> None:
    """Imagem construida sem `scripts/mql5` e um estado real. Dizer isso e
    melhor que um 500 — o proximo passo e reconstruir, nao depurar."""
    import pathlib

    from app.api.routes import dashboard

    monkeypatch.setattr(
        dashboard, "_indicator_path", lambda: pathlib.Path("/nao/existe.mq5")
    )

    resposta = logado.get("/dashboard/mt5/indicator")

    assert resposta.status_code == 404
    assert "docker compose build" in resposta.json()["detail"]


def test_the_settings_page_offers_the_download(logado) -> None:
    resposta = logado.get("/dashboard/settings")

    assert resposta.status_code == 200
    assert "/dashboard/mt5/indicator" in resposta.text

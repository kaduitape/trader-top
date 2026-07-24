def test_health_returns_ok(client, db_session) -> None:
    from app.database.repositories.system_setting_repository import get_current_mode

    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["database_connected"] is True
    # `system_mode` e um valor global compartilhado por toda a suite de
    # testes (mesma razao documentada nas Fases 10/12) -- le o valor
    # ATUAL persistido em vez de assumir o padrao DISABLED, que outros
    # arquivos de teste ja avancaram a essa altura da suite.
    assert body["system_mode"] == get_current_mode(db_session).value


def test_root_redirects_to_dashboard(client) -> None:
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/dashboard"

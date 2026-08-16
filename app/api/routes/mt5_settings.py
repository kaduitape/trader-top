"""API de configuracao e teste do MetaTrader 5.

## Por que o teste nao acontece aqui dentro

A biblioteca `MetaTrader5` fala com o TERMINAL instalado, e so existe para
Windows. O painel roda em container Linux. Nenhuma quantidade de codigo
nesta rota faz uma requisicao HTTP no Linux abrir uma sessao MT5 no
Windows.

Entao o teste e DELEGADO: a rota grava um pedido, o worker Windows —
que ja roda em laco ao lado do terminal — executa e publica o resultado, e
a rota espera esse resultado ate o timeout. Se o worker estiver parado, a
resposta diz isso, em vez de estourar um erro tecnico.

E o mesmo mecanismo que a tela do conector ja usava para "Testar conexao"
(`test_request_id` em `app/mt5/sync_settings.py`); reaproveita-lo evita uma
segunda arquitetura para o mesmo problema.

## Senha

Ela entra por aqui e nunca mais sai. Nenhuma resposta desta rota inclui a
senha, cifrada ou nao — nem o endpoint de status, nem o de diagnostico.
"""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dependencies.auth import get_current_user
from app.core.config import get_settings
from app.database.models.mt5_credential import ACCOUNT_TYPES, Mt5Credential
from app.database.models.user import User
from app.database.repositories.audit_log_repository import AuditLogRepository
from app.database.repositories.mt5_credential_repository import Mt5CredentialRepository
from app.database.session import get_db
from app.mt5.bridge import describe_target, resolve_target
from app.mt5.sync_settings import (
    heartbeat_is_fresh,
    load_sync_config,
    load_sync_status,
    save_sync_config,
)

router = APIRouter(prefix="/api/settings/mt5", tags=["mt5-settings"])

TEST_POLL_SECONDS = 1.0
TEST_TIMEOUT_SECONDS = 45


class Mt5SettingsIn(BaseModel):
    login: int = Field(gt=0)
    server: str = Field(min_length=1, max_length=120)
    account_type: str = "DEMO"
    terminal_path: str | None = Field(default=None, max_length=500)
    password: str | None = Field(default=None, max_length=256)
    """Ausente ou vazia MANTEM a senha atual. Sem isso, mudar o servidor
    exigiria redigitar a senha — e senha redigitada com frequencia vira
    senha anotada em papel."""

    bridge_host: str | None = Field(default=None, max_length=200)
    bridge_port: int | None = Field(default=None, gt=0, le=65535)
    """Onde esta o terminal quando ele roda sob Wine em outro container.
    Vazio = pacote `MetaTrader5` local (Windows). Ao contrario da senha,
    vazio aqui significa apagado, nao "mantenha o que estava"."""


class AccountOut(BaseModel):
    login: int
    server: str
    currency: str | None = None


class Mt5StatusOut(BaseModel):
    configured: bool
    last_test_status: str | None = None
    last_test_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    account: AccountOut | None = None
    worker_online: bool = False
    """Configuracao validada e sessao ativa sao coisas diferentes. Um teste
    que funcionou ontem nao significa que existe sessao agora."""

    bridge: str = "pacote local (Windows)"
    """Destino efetivo, ja resolvido entre banco e ambiente. Sai na resposta
    porque "salvei e nao mudou nada" quase sempre e precedencia invisivel."""


def _status_payload(db: Session) -> Mt5StatusOut:
    registro = Mt5CredentialRepository(db).get_active()
    worker = heartbeat_is_fresh(load_sync_status(db))
    host, porta = resolve_target(registro, get_settings())
    if registro is None:
        return Mt5StatusOut(
            configured=False, worker_online=worker, bridge=describe_target(host, porta)
        )
    return Mt5StatusOut(
        configured=True,
        bridge=describe_target(host, porta),
        last_test_status=registro.last_test_status,
        last_test_at=registro.last_test_at,
        last_success_at=registro.last_success_at,
        last_error=registro.last_error,
        account=AccountOut(login=registro.login, server=registro.server),
        worker_online=worker,
    )


@router.get("/status", response_model=Mt5StatusOut)
def mt5_status(
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> Mt5StatusOut:
    return _status_payload(db)


@router.put("", response_model=Mt5StatusOut)
def mt5_save(
    payload: Mt5SettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Mt5StatusOut:
    if payload.account_type.strip().upper() not in ACCOUNT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"tipo de conta invalido; use um de {ACCOUNT_TYPES}",
        )

    repo = Mt5CredentialRepository(db)
    try:
        repo.save(
            login=payload.login,
            server=payload.server,
            account_type=payload.account_type,
            terminal_path=payload.terminal_path,
            password=payload.password or None,
            bridge_host=payload.bridge_host,
            bridge_port=payload.bridge_port,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    AuditLogRepository(db).record(
        action="mt5_credentials_save",
        entity="mt5_credentials",
        # Conta e servidor sim; senha nunca, nem para dizer que mudou de
        # qual valor para qual.
        detail=(
            f"conta {payload.login}@{payload.server} "
            f"({payload.account_type}) por {user.username}"
            + (" — senha atualizada" if payload.password else "")
        ),
        user_id=user.id,
    )
    db.commit()
    return _status_payload(db)


class Mt5TestOut(BaseModel):
    success: bool
    message: str
    account: dict | None = None
    tested_at: datetime | None = None


def _request_worker_test(db: Session) -> str:
    config = load_sync_config(db)
    pedido = uuid4().hex
    save_sync_config(db, replace(config, test_request_id=pedido))
    db.commit()
    return pedido


def _wait_for_result(
    db: Session, credential_id: int, *, deadline: float
) -> Mt5Credential | None:
    """Espera o worker publicar o resultado, ate o prazo.

    Poll simples de propósito: o worker escreve no banco e nao ha canal de
    notificacao entre os dois processos. Um segundo de intervalo e barato
    perto de uma conexao com corretora.
    """
    while time.monotonic() < deadline:
        db.expire_all()
        registro = db.get(Mt5Credential, credential_id)
        if registro is not None and registro.last_test_at is not None:
            return registro
        time.sleep(TEST_POLL_SECONDS)
    return None


def _test_through_bridge(
    db: Session, repo, registro, user, host: str, porta: int
) -> Mt5TestOut:
    """Testa daqui mesmo, pela ponte. A senha so existe em memoria."""
    from app.core.crypto import CredentialCryptoError
    from app.mt5.connection_service import MT5ConnectionService

    try:
        senha = repo.reveal_password(registro)
    except CredentialCryptoError as exc:
        repo.record_test(registro, success=False, error=str(exc)[:500])
        db.commit()
        return Mt5TestOut(success=False, message=str(exc))

    resultado = MT5ConnectionService(
        timeout_seconds=TEST_TIMEOUT_SECONDS, bridge_host=host, bridge_port=porta
    ).test_connection(
        login=registro.login,
        password=senha,
        server=registro.server,
        terminal_path=registro.terminal_path,
    )
    del senha

    repo.record_test(
        registro,
        success=resultado.success,
        error=None if resultado.success else resultado.message[:500],
    )
    AuditLogRepository(db).record(
        action="mt5_connection_test",
        entity="mt5_credentials",
        detail=(
            f"teste pela ponte para {registro.login}@{registro.server} "
            f"por {user.username}: {'ok' if resultado.success else 'falhou'}"
        ),
        user_id=user.id,
    )
    db.commit()

    conta = None
    if resultado.account is not None:
        c = resultado.account
        conta = {
            "login": c.login, "name": c.name, "server": c.server,
            "company": c.company, "account_type": c.account_type,
            "currency": c.currency, "balance": c.balance, "equity": c.equity,
            "margin": c.margin, "margin_free": c.margin_free,
            "leverage": c.leverage,
        }

    return Mt5TestOut(
        success=resultado.success,
        message=resultado.message,
        account=conta,
        tested_at=registro.last_test_at,
    )


@router.post("/test", response_model=Mt5TestOut)
def mt5_test(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Mt5TestOut:
    repo = Mt5CredentialRepository(db)
    registro = repo.get_active()
    if registro is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nenhuma credencial MT5 cadastrada.",
        )

    # Com a ponte configurada (mt5-wine em Docker), o terminal e alcancavel
    # daqui mesmo: nao ha motivo para delegar nem para exigir o worker.
    host, porta = resolve_target(registro, get_settings())
    if host:
        return _test_through_bridge(db, repo, registro, user, host, porta)

    if not heartbeat_is_fresh(load_sync_status(db)):
        # Falha honesta: o teste depende de um processo que nao esta vivo.
        return Mt5TestOut(
            success=False,
            message=(
                "Conector MT5 offline. O teste roda na maquina Windows, ao "
                "lado do terminal — suba o conector e tente de novo."
            ),
        )

    marca_anterior = registro.last_test_at
    registro.last_test_at = None
    registro.last_test_status = "pending"
    db.commit()

    _request_worker_test(db)
    AuditLogRepository(db).record(
        action="mt5_connection_test",
        entity="mt5_credentials",
        detail=f"teste solicitado para {registro.login}@{registro.server} por {user.username}",
        user_id=user.id,
    )
    db.commit()

    atualizado = _wait_for_result(
        db, registro.id, deadline=time.monotonic() + TEST_TIMEOUT_SECONDS
    )
    if atualizado is None:
        registro.last_test_at = marca_anterior
        registro.last_test_status = "failure"
        registro.last_error = "Timeout esperando o conector executar o teste."
        db.commit()
        return Mt5TestOut(
            success=False,
            message=(
                f"Timeout: o conector nao respondeu em {TEST_TIMEOUT_SECONDS}s. "
                "Confirme que ele esta rodando e que o MetaTrader esta aberto."
            ),
        )

    sucesso = atualizado.last_test_status == "success"
    return Mt5TestOut(
        success=sucesso,
        message=(
            "Conexao validada."
            if sucesso
            else (atualizado.last_error or "Falha nao identificada.")
        ),
        tested_at=atualizado.last_test_at,
    )

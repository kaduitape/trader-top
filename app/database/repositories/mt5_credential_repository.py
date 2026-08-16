"""Acesso as credenciais MT5. A senha entra cifrada e sai cifrada.

A unica porta para o texto em claro e `reveal_password`, que existe para
ser facil de auditar: quem procura "onde a senha e lida" acha um lugar so.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret, encrypt_secret
from app.database.models.mt5_credential import (
    ACCOUNT_TYPE_DEMO,
    ACCOUNT_TYPES,
    Mt5Credential,
)


class Mt5CredentialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_active(self) -> Mt5Credential | None:
        stmt = (
            select(Mt5Credential)
            .where(Mt5Credential.is_active.is_(True))
            .order_by(Mt5Credential.id.desc())
        )
        return self._session.execute(stmt).scalars().first()

    def save(
        self,
        *,
        login: int,
        server: str,
        account_type: str,
        terminal_path: str | None = None,
        password: str | None = None,
        bridge_host: str | None = None,
        bridge_port: int | None = None,
    ) -> Mt5Credential:
        """Cria ou atualiza a credencial ativa.

        `password=None` mantem a senha existente — e o requisito de "editar
        sem apagar a senha". Um formulario que reenvia a senha a cada edicao
        obriga o operador a redigita-la para mudar o servidor, e senha
        redigitada com frequencia vira senha anotada em papel.

        `bridge_host` segue a regra oposta da senha: em branco significa
        "sem ponte" (terminal local), e nao "mantenha o que estava". Sao
        campos de natureza diferente — a senha o formulario nao devolve, o
        endereco sim, entao o que chega em branco foi apagado de proposito.
        """
        tipo = account_type.strip().upper()
        if tipo not in ACCOUNT_TYPES:
            tipo = ACCOUNT_TYPE_DEMO

        registro = self.get_active()
        if registro is None:
            if not password:
                raise ValueError("a senha e obrigatoria no primeiro cadastro.")
            registro = Mt5Credential(
                login=login,
                password_encrypted=encrypt_secret(password),
                server=server.strip(),
                terminal_path=(terminal_path or "").strip() or None,
                account_type=tipo,
                is_active=True,
                bridge_host=(bridge_host or "").strip() or None,
                bridge_port=bridge_port,
            )
            self._session.add(registro)
            return registro

        registro.login = login
        registro.server = server.strip()
        registro.terminal_path = (terminal_path or "").strip() or None
        registro.account_type = tipo
        registro.bridge_host = (bridge_host or "").strip() or None
        registro.bridge_port = bridge_port
        if password:
            registro.password_encrypted = encrypt_secret(password)
            # Trocar a senha invalida o resultado anterior: um "sucesso" de
            # ontem nao diz nada sobre a credencial de agora.
            registro.last_test_status = None
            registro.last_error = None
        return registro

    def reveal_password(self, credential: Mt5Credential) -> str:
        """Texto em claro, so em memoria e so para falar com o terminal."""
        return decrypt_secret(credential.password_encrypted)

    def record_test(
        self,
        credential: Mt5Credential,
        *,
        success: bool,
        error: str | None = None,
        now: datetime | None = None,
    ) -> Mt5Credential:
        momento = (now or datetime.now(UTC)).replace(tzinfo=None)
        credential.last_test_at = momento
        credential.last_test_status = "success" if success else "failure"
        credential.last_error = (error or None) if not success else None
        if success:
            credential.last_success_at = momento
        return credential

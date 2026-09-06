"""Acesso as chaves de API. O segredo existe uma vez e nunca mais.

`create` e o unico lugar do sistema que ve a chave em claro, e ela sai de
la para a resposta HTTP sem passar pelo banco. Perdeu, gera outra — nao ha
recuperacao, e isso e proposital: se o sistema conseguisse mostrar a chave
de novo, um vazamento do banco tambem conseguiria.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.api_token import PREFIX_LENGTH, ApiToken

TOKEN_BYTES = 32
"""256 bits. E o que dispensa hash lento na verificacao."""

VISIBLE_PREFIX = "tt_"
"""Prefixo fixo para a chave ser reconhecivel num arquivo de configuracao
— e para varredores de segredo (GitHub, gitleaks) terem o que casar caso
ela vaze para um repositorio."""


def hash_token(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


class ApiTokenRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, *, name: str, user_id: int | None = None) -> tuple[ApiToken, str]:
        """Devolve o registro e o segredo em claro — a unica vez."""
        segredo = VISIBLE_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        registro = ApiToken(
            name=name.strip()[:80] or "sem nome",
            prefix=segredo[:PREFIX_LENGTH],
            token_hash=hash_token(segredo),
            is_active=True,
            created_by_user_id=user_id,
        )
        self._session.add(registro)
        self._session.flush()
        return registro, segredo

    def list_all(self) -> list[ApiToken]:
        stmt = select(ApiToken).order_by(ApiToken.id.desc())
        return list(self._session.execute(stmt).scalars())

    def resolve(self, plain: str) -> ApiToken | None:
        """Chave em claro -> registro ativo, ou None.

        Busca pelo HASH, nao pelo prefixo: o prefixo existe para a tela,
        nao para a autenticacao. Procurar por ele abriria espaco para uma
        colisao decidir o resultado.
        """
        if not plain:
            return None
        stmt = select(ApiToken).where(
            ApiToken.token_hash == hash_token(plain),
            ApiToken.is_active.is_(True),
        )
        return self._session.execute(stmt).scalars().first()

    def touch(self, token: ApiToken, *, now: datetime | None = None) -> None:
        """Marca o uso. Uma chave que parou de ser usada e um indicador que
        caiu — e isso e informacao operacional, nao enfeite."""
        token.last_used_at = now or datetime.now(UTC)
        token.request_count = (token.request_count or 0) + 1

    def revoke(self, token_id: int) -> ApiToken | None:
        registro = self._session.get(ApiToken, token_id)
        if registro is None:
            return None
        registro.is_active = False
        return registro

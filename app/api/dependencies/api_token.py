"""Autenticacao por chave de API, para clientes que nao sao navegador.

Header: `X-API-Key: tt_...`

Separado de `get_current_user` de proposito. Sao duas populacoes com
necessidades opostas: sessao de gente deve expirar rapido; credencial de
maquina deve durar e ser revogavel uma a uma. Misturar as duas obrigaria a
escolher entre um JWT longo demais para o navegador ou um curto demais
para o indicador.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database.models.api_token import ApiToken
from app.database.repositories.api_token_repository import ApiTokenRepository
from app.database.session import get_db

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    # Mensagem unica para ausente, malformada e revogada: distinguir os
    # casos diria a quem esta tentando adivinhar se ele acertou o formato.
    detail="Chave de API ausente ou invalida. Envie o header X-API-Key.",
    headers={"WWW-Authenticate": "X-API-Key"},
)


def get_api_token(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> ApiToken:
    """Valida a chave e registra o uso.

    O `commit` do uso acontece aqui, antes de a rota rodar: se a analise
    demorar ou falhar, o registro de "esta chave esta viva" ja existe — e
    e justamente num erro que saber se o indicador chegou a chamar importa.
    """
    repo = ApiTokenRepository(db)
    token = repo.resolve((x_api_key or "").strip())
    if token is None:
        raise _INVALID

    repo.touch(token)
    db.commit()
    return token

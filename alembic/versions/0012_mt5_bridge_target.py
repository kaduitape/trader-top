"""endereco da ponte MT5 junto da credencial

Ate aqui a ponte (`MT5_BRIDGE_HOST`/`MT5_BRIDGE_PORT`) so existia como
variavel de ambiente. Isso obriga a editar `.env` e reconstruir o container
para trocar o destino — exatamente o tipo de passo manual que o painel
deveria ter eliminado.

Vira coluna porque o destino da ponte pertence a MESMA decisao que login,
senha e servidor: "com qual terminal eu falo". Manter metade dessa decisao
no banco e metade no ambiente e o que produz "salvei no painel e nao mudou
nada".

O ambiente continua valendo como padrao quando a coluna esta vazia, para
nao quebrar instalacoes que ja configuraram por `.env`.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-16

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "mt5_credentials",
        sa.Column("bridge_host", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "mt5_credentials",
        sa.Column("bridge_port", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("mt5_credentials", "bridge_port")
    op.drop_column("mt5_credentials", "bridge_host")

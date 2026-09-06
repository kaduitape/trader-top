"""chaves de API para clientes de maquina (indicador MT5)

O JWT do painel expira em 30 minutos e nasce de um login com senha — um
indicador rodando dentro do MetaTrader nao tem como refazer isso, e ficaria
mudo no meio do pregao.

A chave e guardada como SHA-256 do segredo, nunca em claro. Hash lento
(bcrypt) nao se justifica aqui: o segredo tem 256 bits de entropia, entao
forca bruta ja e impossivel e a lentidao so viraria latencia em cada
consulta do indicador.

`is_active` em vez de DELETE: revogar nao pode apagar o rastro do que
aquela chave fez enquanto valia.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-06

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_api_tokens_hash"),
    )
    op.create_index("ix_api_tokens_prefix", "api_tokens", ["prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_prefix", table_name="api_tokens")
    op.drop_table("api_tokens")

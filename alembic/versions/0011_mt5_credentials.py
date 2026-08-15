"""credenciais MetaTrader 5 com senha cifrada

Ate aqui as credenciais viviam so no `.env` do host Windows. Passam a ser
configuraveis pelo painel, e por isso precisam de armazenamento — com a
senha CIFRADA (`app/core/crypto.py`), nunca em texto puro.

A tabela ja nasce preparada para varias contas (`is_active`), e separa
`last_test_at` de `last_success_at`: "testei agora e falhou" e "funcionou
tres dias atras" sao fatos diferentes, e junta-los faria a tela dizer
"conectado" sobre uma configuracao que parou de funcionar.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-11

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mt5_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.Integer(), nullable=False),
        sa.Column("password_encrypted", sa.Text(), nullable=False),
        sa.Column("server", sa.String(length=120), nullable=False),
        sa.Column("terminal_path", sa.String(length=500), nullable=True),
        sa.Column("account_type", sa.String(length=8), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_status", sa.String(length=16), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_mt5_credentials_login"), "mt5_credentials", ["login"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_mt5_credentials_login"), table_name="mt5_credentials")
    op.drop_table("mt5_credentials")

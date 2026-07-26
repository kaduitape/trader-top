"""system_settings.value: TEXT em vez de VARCHAR(1000)

O status ao vivo do piloto automatico (`app.execution.autopilot_status`)
usa o mesmo canal chave-valor ja compartilhado entre o worker Windows e o
dashboard, mas carrega um feed de atividades alem dos campos de estado —
conteudo que passa com folga de 1000 caracteres. No MySQL isso seria um
erro de escrita (ou truncamento silencioso em modo nao estrito), entao a
coluna passa a TEXT.

No SQLite (dialeto da suite de testes) esta migration nao faz nada:
VARCHAR nao impoe limite de tamanho la, e o tipo declarado ja e apenas
afinidade.

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.alter_column(
        "system_settings",
        "value",
        type_=sa.Text(),
        existing_type=sa.String(length=1000),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.alter_column(
        "system_settings",
        "value",
        type_=sa.String(length=1000),
        existing_type=sa.Text(),
        existing_nullable=False,
    )

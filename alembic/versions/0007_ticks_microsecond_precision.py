"""ticks.timestamp: precisao de microssegundos no MySQL

O MySQL trunca DATETIME para resolucao de segundo inteiro por padrao (sem
`fsp`) -- ticks reais que chegam mais de uma vez por segundo (comum em
mercado ativo) perdiam a granularidade que os distinguia, colidindo na
unique constraint `uq_ticks_symbol_time_bid_ask` mesmo quando o
MetaTrader5 reportava timestamps distintos. Bug real, achado coletando
ticks de producao (Fase 16) -- nao aparecia nos testes porque a suite usa
SQLite, que nunca trunca precisao de datetime.

No SQLite (dialeto de teste) esta migration nao faz nada -- `DATETIME(6)`
e uma feature exclusiva do dialeto MySQL (`with_variant`, ver
`app/database/models/tick.py`); o SQLite ja preservava microssegundos
sem qualquer ajuste de tipo de coluna.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-23

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.alter_column(
        "ticks",
        "timestamp",
        type_=MySQLDateTime(fsp=6),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return
    op.alter_column(
        "ticks",
        "timestamp",
        type_=MySQLDateTime(fsp=0),
        existing_nullable=False,
    )

"""live_trades.initial_stop_loss: preserva o risco ORIGINAL da operacao

`stop_loss` passa a ser reescrito pelo trailing stop / break-even
(`app.execution.position_manager`), o que apagava a distancia de stop com
que a operacao NASCEU — justamente a que define 1R. Sem ela, o R-multiplo
das operacoes bem-sucedidas (as unicas em que o trailing atua) ficaria
incalculavel, e o Learning Engine perderia a metrica mais comparavel entre
pares e tamanhos diferentes.

Coluna anulavel de proposito: operacoes gravadas antes desta migration
continuam sem o valor, e o calculo de R devolve `None` para elas em vez de
inventar um numero a partir do stop ja movido.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "live_trades",
        sa.Column("initial_stop_loss", sa.Numeric(precision=18, scale=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("live_trades", "initial_stop_loss")

"""Store the executable price increment from MetaTrader.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Zero means "not synchronized yet".  The runtime safely falls back to
    # `point`, and the next MT5 catalog synchronization writes the real value.
    op.add_column(
        "symbols",
        sa.Column(
            "trade_tick_size",
            sa.Numeric(precision=18, scale=10),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("symbols", "trade_tick_size")

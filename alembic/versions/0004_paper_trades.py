"""paper trades

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=False),
        sa.Column("strategy_name", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("take_profit", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("exit_reason", sa.String(length=20), nullable=True),
        sa.Column("net_pnl", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("bars_held", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_paper_trades_symbol_id"), "paper_trades", ["symbol_id"], unique=False
    )
    op.create_index(
        op.f("ix_paper_trades_strategy_name"), "paper_trades", ["strategy_name"], unique=False
    )
    op.create_index(op.f("ix_paper_trades_status"), "paper_trades", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_paper_trades_status"), table_name="paper_trades")
    op.drop_index(op.f("ix_paper_trades_strategy_name"), table_name="paper_trades")
    op.drop_index(op.f("ix_paper_trades_symbol_id"), table_name="paper_trades")
    op.drop_table("paper_trades")

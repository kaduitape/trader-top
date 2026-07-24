"""live trades

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_trades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=False),
        sa.Column("strategy_name", sa.String(length=100), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("signal_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=5), nullable=False),
        sa.Column("order_state", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("mt5_order_ticket", sa.Integer(), nullable=True),
        sa.Column("mt5_position_ticket", sa.Integer(), nullable=True),
        sa.Column("signal_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entry_price", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("stop_loss", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("take_profit", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("exit_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("exit_price", sa.Numeric(precision=18, scale=8), nullable=True),
        sa.Column("net_pnl", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_live_trades_symbol_id"), "live_trades", ["symbol_id"], unique=False)
    op.create_index(
        op.f("ix_live_trades_strategy_name"), "live_trades", ["strategy_name"], unique=False
    )
    op.create_index(
        op.f("ix_live_trades_order_state"), "live_trades", ["order_state"], unique=False
    )
    op.create_index(
        op.f("ix_live_trades_mt5_position_ticket"),
        "live_trades",
        ["mt5_position_ticket"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_live_trades_mt5_position_ticket"), table_name="live_trades")
    op.drop_index(op.f("ix_live_trades_order_state"), table_name="live_trades")
    op.drop_index(op.f("ix_live_trades_strategy_name"), table_name="live_trades")
    op.drop_index(op.f("ix_live_trades_symbol_id"), table_name="live_trades")
    op.drop_table("live_trades")

"""market data schema: symbols, candles, ticks

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-21

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "symbols",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=30), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("digits", sa.Integer(), nullable=False),
        sa.Column("point", sa.Numeric(precision=18, scale=10), nullable=False),
        sa.Column("volume_min", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume_max", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume_step", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("trade_contract_size", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(op.f("ix_symbols_name"), "symbols", ["name"], unique=False)

    op.create_table(
        "candles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("high", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("low", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("close", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("tick_volume", sa.BigInteger(), nullable=False),
        sa.Column("spread", sa.Integer(), nullable=False),
        sa.Column("real_volume", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol_id", "timeframe", "open_time", name="uq_candles_symbol_tf_time"),
    )
    op.create_index(op.f("ix_candles_symbol_id"), "candles", ["symbol_id"], unique=False)
    op.create_index(op.f("ix_candles_timeframe"), "candles", ["timeframe"], unique=False)
    op.create_index(op.f("ix_candles_open_time"), "candles", ["open_time"], unique=False)

    op.create_table(
        "ticks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bid", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("ask", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("last", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("volume", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column("flags", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol_id", "timestamp", "bid", "ask", name="uq_ticks_symbol_time_bid_ask"
        ),
    )
    op.create_index(op.f("ix_ticks_symbol_id"), "ticks", ["symbol_id"], unique=False)
    op.create_index(op.f("ix_ticks_timestamp"), "ticks", ["timestamp"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ticks_timestamp"), table_name="ticks")
    op.drop_index(op.f("ix_ticks_symbol_id"), table_name="ticks")
    op.drop_table("ticks")

    op.drop_index(op.f("ix_candles_open_time"), table_name="candles")
    op.drop_index(op.f("ix_candles_timeframe"), table_name="candles")
    op.drop_index(op.f("ix_candles_symbol_id"), table_name="candles")
    op.drop_table("candles")

    op.drop_index(op.f("ix_symbols_name"), table_name="symbols")
    op.drop_table("symbols")

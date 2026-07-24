"""data quality events

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_quality_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=True),
        sa.Column("check_name", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_data_quality_events_symbol_id"), "data_quality_events", ["symbol_id"], unique=False
    )
    op.create_index(
        op.f("ix_data_quality_events_check_name"), "data_quality_events", ["check_name"], unique=False
    )
    op.create_index(
        op.f("ix_data_quality_events_severity"), "data_quality_events", ["severity"], unique=False
    )
    op.create_index(
        op.f("ix_data_quality_events_detected_at"),
        "data_quality_events",
        ["detected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_data_quality_events_detected_at"), table_name="data_quality_events")
    op.drop_index(op.f("ix_data_quality_events_severity"), table_name="data_quality_events")
    op.drop_index(op.f("ix_data_quality_events_check_name"), table_name="data_quality_events")
    op.drop_index(op.f("ix_data_quality_events_symbol_id"), table_name="data_quality_events")
    op.drop_table("data_quality_events")

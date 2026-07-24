"""drift events

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drift_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("drift_type", sa.String(length=20), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=True),
        sa.Column("symbol_id", sa.Integer(), nullable=True),
        sa.Column("timeframe", sa.String(length=5), nullable=True),
        sa.Column("metric_name", sa.String(length=100), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=True),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("detail", sa.String(length=1000), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_drift_events_drift_type"), "drift_events", ["drift_type"], unique=False)
    op.create_index(op.f("ix_drift_events_severity"), "drift_events", ["severity"], unique=False)
    op.create_index(
        op.f("ix_drift_events_model_version"), "drift_events", ["model_version"], unique=False
    )
    op.create_index(op.f("ix_drift_events_symbol_id"), "drift_events", ["symbol_id"], unique=False)
    op.create_index(
        op.f("ix_drift_events_detected_at"), "drift_events", ["detected_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_drift_events_detected_at"), table_name="drift_events")
    op.drop_index(op.f("ix_drift_events_symbol_id"), table_name="drift_events")
    op.drop_index(op.f("ix_drift_events_model_version"), table_name="drift_events")
    op.drop_index(op.f("ix_drift_events_severity"), table_name="drift_events")
    op.drop_index(op.f("ix_drift_events_drift_type"), table_name="drift_events")
    op.drop_table("drift_events")

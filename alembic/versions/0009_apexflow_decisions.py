"""apexflow decisions (Learning Engine)

Uma linha por decisao do motor ApexFlow AI — inclusive as de NAO OPERAR,
que sao a maioria e sem as quais o historico ficaria enviesado (seria
impossivel avaliar se o robo deixou passar boas oportunidades).

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apexflow_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=5), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("probability_buy", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("probability_sell", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("probability_abstain", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("min_confidence", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("model_version", sa.String(length=50), nullable=False),
        sa.Column("feature_version", sa.String(length=50), nullable=False),
        sa.Column("completeness", sa.Numeric(precision=6, scale=4), nullable=False),
        sa.Column("context_state", sa.String(length=30), nullable=False),
        sa.Column("session_rating", sa.String(length=16), nullable=False),
        sa.Column("volume_level", sa.String(length=16), nullable=False),
        sa.Column("spread_points", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("atr_points", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("ticks_per_second", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("mtf_alignment", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("vetoes", sa.Text(), nullable=True),
        sa.Column("reasons", sa.Text(), nullable=True),
        sa.Column("feature_vector", sa.Text(), nullable=True),
        sa.Column("live_trade_id", sa.Integer(), nullable=True),
        sa.Column("result_net_pnl", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("result_r_multiple", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("result_max_drawdown", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["live_trade_id"], ["live_trades.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_apexflow_decisions_symbol_id"), "apexflow_decisions", ["symbol_id"]
    )
    op.create_index(
        op.f("ix_apexflow_decisions_decided_at"), "apexflow_decisions", ["decided_at"]
    )
    op.create_index(op.f("ix_apexflow_decisions_action"), "apexflow_decisions", ["action"])
    op.create_index(
        op.f("ix_apexflow_decisions_context_state"), "apexflow_decisions", ["context_state"]
    )
    op.create_index(
        op.f("ix_apexflow_decisions_live_trade_id"), "apexflow_decisions", ["live_trade_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_apexflow_decisions_live_trade_id"), table_name="apexflow_decisions")
    op.drop_index(op.f("ix_apexflow_decisions_context_state"), table_name="apexflow_decisions")
    op.drop_index(op.f("ix_apexflow_decisions_action"), table_name="apexflow_decisions")
    op.drop_index(op.f("ix_apexflow_decisions_decided_at"), table_name="apexflow_decisions")
    op.drop_index(op.f("ix_apexflow_decisions_symbol_id"), table_name="apexflow_decisions")
    op.drop_table("apexflow_decisions")

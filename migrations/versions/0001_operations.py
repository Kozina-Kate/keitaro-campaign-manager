"""Создать журнал операций."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("campaign_name", sa.String(255), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'partial')",
            name="ck_operations_status",
        ),
    )


def downgrade():
    op.drop_table("operations")

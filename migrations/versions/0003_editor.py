"""Потоки, черновики долей и локальный архив офферов."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editor_streams",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("schema", sa.String(40), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("remote_exists", sa.Boolean(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("pending_offers", sa.JSON(), nullable=True),
        sa.Column("publish_started", sa.Float(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_index("ix_editor_streams_campaign_id", "editor_streams", ["campaign_id"])
    op.create_table(
        "editor_offers",
        sa.Column(
            "stream_id",
            sa.Integer(),
            sa.ForeignKey("editor_streams.id"),
            primary_key=True,
        ),
        sa.Column("offer_id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("share", sa.Integer(), nullable=False),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("base_active", sa.Boolean(), nullable=False),
        sa.Column("base_share", sa.Integer(), nullable=False),
        sa.Column("base_pinned", sa.Boolean(), nullable=False),
        sa.Column("draft_new", sa.Boolean(), nullable=False),
        sa.CheckConstraint("share >= 0 AND share <= 100", name="ck_editor_offer_share"),
        sa.CheckConstraint(
            "base_share >= 0 AND base_share <= 100", name="ck_editor_offer_base_share"
        ),
    )


def downgrade():
    op.drop_table("editor_offers")
    op.drop_index("ix_editor_streams_campaign_id", table_name="editor_streams")
    op.drop_table("editor_streams")

"""Сохранить запрос и результат создания для защиты от повторной отправки."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("operations") as batch:
        batch.add_column(sa.Column("request_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("request_payload", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("result", sa.JSON(), nullable=True))
        batch.create_unique_constraint("uq_operations_request_id", ["request_id"])


def downgrade():
    with op.batch_alter_table("operations") as batch:
        batch.drop_constraint("uq_operations_request_id", type_="unique")
        batch.drop_column("result")
        batch.drop_column("request_payload")
        batch.drop_column("request_id")

"""add tariff position

Revision ID: b1f9b8b8e4c7
Revises: 9f7f03a2a035
Create Date: 2025-02-14 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b1f9b8b8e4c7"
down_revision = "9f7f03a2a035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tariffs",
        sa.Column("position", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("tariffs", "position")

"""add tariff localized price and description

Revision ID: 6b7c6b5f1f2e
Revises: a28b72a32145
Create Date: 2026-01-08 12:23:18.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "6b7c6b5f1f2e"
down_revision: Union[str, None] = "a28b72a32145"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tariffs", sa.Column("price_ru", sa.String(), nullable=True))
    op.add_column("tariffs", sa.Column("price_en", sa.String(), nullable=True))
    op.add_column("tariffs", sa.Column("price_uz", sa.String(), nullable=True))
    op.add_column("tariffs", sa.Column("description_ru", sa.String(), nullable=True))
    op.add_column("tariffs", sa.Column("description_en", sa.String(), nullable=True))
    op.add_column("tariffs", sa.Column("description_uz", sa.String(), nullable=True))
    op.execute(
        "UPDATE tariffs SET price_ru = price, price_en = price, price_uz = price"
    )


def downgrade() -> None:
    op.drop_column("tariffs", "description_uz")
    op.drop_column("tariffs", "description_en")
    op.drop_column("tariffs", "description_ru")
    op.drop_column("tariffs", "price_uz")
    op.drop_column("tariffs", "price_en")
    op.drop_column("tariffs", "price_ru")

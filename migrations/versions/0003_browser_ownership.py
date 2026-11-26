"""Anonymous visitor ownership; old local conversations remain unowned."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if "visitor_hash" not in {column["name"] for column in inspector.get_columns("conversations")}:
        op.add_column("conversations", sa.Column("visitor_hash", sa.String(64), nullable=True))
    if "ix_conversations_visitor_hash" not in {index["name"] for index in inspector.get_indexes("conversations")}:
        op.create_index("ix_conversations_visitor_hash", "conversations", ["visitor_hash"])


def downgrade():
    op.drop_index("ix_conversations_visitor_hash", table_name="conversations")
    op.drop_column("conversations", "visitor_hash")

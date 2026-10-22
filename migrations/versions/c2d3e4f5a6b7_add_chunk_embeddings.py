"""add a pgvector embedding column to document_chunks

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-10-22

Semantic search needs the chunk text as a vector, and vectors need a real
vector type: an ordinary text column cannot be compared for similarity.

The column is nullable with no default. A chunk with no embedding is "not
embedded yet", which is different from "embedded as the zero vector" - the
zero vector sits at the origin and would match everything equally.

The HNSW index is what makes the comparison fast enough to be worth
having: a sequential scan over every chunk comparing 1536 dimensions is
not a search, it is a slow query.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Kept in step with src/db/vector.py:EMBEDDING_DIMENSIONS. A vector column
# has a fixed width, and changing it later means a new column and a
# re-embed of every chunk.
EMBEDDING_DIMENSIONS = 1536

INDEX_NAME = "idx_chunks_embedding_hnsw"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        f"ALTER TABLE document_chunks "
        f"ADD COLUMN embedding VECTOR({EMBEDDING_DIMENSIONS})"
    )
    # Cosine distance, because the embedding model's output is compared by
    # angle rather than magnitude.
    op.execute(
        f"CREATE INDEX {INDEX_NAME} ON document_chunks "
        f"USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")

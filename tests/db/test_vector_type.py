"""The vector column type (Phase 3a).

The column holds a fixed number of dimensions. A vector of the wrong width
has to be refused on the way in - a 768-dimension vector in a 1536-dimension
column cannot be compared with anything, and storing it anyway would only
move the failure to the moment somebody tried to search.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, String, create_engine, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from db.vector import EMBEDDING_DIMENSIONS, Vector  # noqa: E402


class Base(DeclarativeBase):
    pass


class Row(Base):
    __tablename__ = "vector_rows"
    id = Column(Integer, primary_key=True)
    name = Column(String(64))
    embedding = Column(Vector(EMBEDDING_DIMENSIONS))


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        yield s


class TestTheVectorType:
    def test_the_dimension_is_fixed(self):
        assert EMBEDDING_DIMENSIONS == 1536

    def test_a_vector_round_trips(self, session):
        vector = [0.5] * EMBEDDING_DIMENSIONS
        session.add(Row(id=1, name="ok", embedding=vector))
        session.commit()

        stored = session.execute(select(Row)).scalar_one()
        assert stored.embedding == vector

    def test_a_null_vector_stays_null(self, session):
        session.add(Row(id=1, name="ok", embedding=None))
        session.commit()
        stored = session.execute(select(Row)).scalar_one()
        assert stored.embedding is None

    def test_a_vector_of_the_wrong_width_is_refused(self, session):
        session.add(Row(id=1, name="wrong", embedding=[0.1] * 100))
        # SQLAlchemy wraps the type's own error; the reason has to survive
        # that wrapping, otherwise the operator only sees a stack trace.
        with pytest.raises(StatementError) as exc:
            session.commit()
        assert "1536 dimensions" in str(exc.value)
        assert isinstance(exc.value.orig, ValueError)

    def test_the_error_says_what_was_wrong(self, session):
        session.add(Row(id=1, name="wrong", embedding=[0.1] * 1535))
        with pytest.raises(StatementError) as exc:
            session.commit()
        assert "1535" in str(exc.value)

    def test_a_vector_of_the_right_width_is_accepted(self, session):
        session.add(Row(id=1, name="ok", embedding=[0.25] * EMBEDDING_DIMENSIONS))
        session.commit()
        stored = session.execute(select(Row)).scalar_one()
        assert len(stored.embedding) == EMBEDDING_DIMENSIONS

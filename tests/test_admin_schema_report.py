"""Tests for the admin schema-report endpoint (read-only DB structure)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
from db.connection import get_session_factory, get_session
from db.repositories import UserRepo
from api.main import app


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(engine):
    app.state.db_engine = engine
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_headers(client, engine):
    """Promote a registered user to admin and return auth headers."""
    client.post(
        "/api/v1/auth/register",
        json={"email": "schema-admin@example.com", "password": "pass123"},
    )
    with get_session(engine) as s:
        u = UserRepo(s).get_by_email("schema-admin@example.com")
        UserRepo(s).set_role(str(u.user_id), "admin")
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "schema-admin@example.com", "password": "pass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class TestAccessGating:
    def test_unauthenticated_rejected(self, client):
        resp = client.get("/api/v1/admin/schema")
        assert resp.status_code == 401

    def test_non_admin_rejected(self, client):
        client.post(
            "/api/v1/auth/register",
            json={"email": "schema-user@example.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "schema-user@example.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        r = client.get("/api/v1/admin/schema", headers=headers)
        assert r.status_code == 403


class TestSchemaReport:
    def test_returns_full_structure(self, client, admin_headers):
        resp = client.get("/api/v1/admin/schema", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["dialect"] == "sqlite"
        names = {t["name"] for t in data["tables"]}
        assert "government_sources" in names
        assert "api_users" in names
        assert "requirements" in names
        assert data["table_count"] == len(data["tables"])

    def test_columns_and_keys_reported(self, client, admin_headers):
        resp = client.get("/api/v1/admin/schema", headers=admin_headers)
        data = resp.json()
        gov = next(t for t in data["tables"] if t["name"] == "government_sources")
        assert gov["column_count"] == len(gov["columns"])
        assert {"name", "type", "nullable"} <= set(gov["columns"][0])
        # FK surface: government_documents -> government_sources must appear once
        # the table exists in this in-memory engine (schema created by create_all).
        assert any(
            fk.startswith("government_documents.source_id -> government_sources.source_id")
            for fk in data["foreign_keys"]
        )

    def test_counts_and_migration_null_on_hermetic_engine(self, client, admin_headers):
        """create_all() does not create alembic_version — report must not crash
        and must report the missing migration tracker as null."""
        resp = client.get("/api/v1/admin/schema", headers=admin_headers)
        data = resp.json()
        assert data["alembic_version"] is None
        src = next(t for t in data["tables"] if t["name"] == "government_sources")
        assert src["rows"] == 0
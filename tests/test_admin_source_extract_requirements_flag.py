"""Item 3: the per-source extract_requirements scope flag.

Covers the admin API surface (visible on GET, settable via POST/PATCH).
See test_fetch_flow.py for the fetch-time behavior (fetched/hashed/stored
as evidence regardless; extraction skipped only when the flag is false).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base, Country, ImmigrationProgram, GovernmentSource
from db.connection import get_session_factory
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
    client.post(
        "/api/v1/auth/register",
        json={"email": "extract-flag-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "extract-flag-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "extract-flag-admin@test.com", "password": "admin123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def seeded_source(engine):
    with get_session_factory(engine)() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AUS"))
        s.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU", visa_category="SKILLED",
            subclass_code="189", name="Skilled Independent", status="ACTIVE",
        ))
        s.merge(GovernmentSource(
            source_id="SRC-AU-TEST-SCOPE", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.invalid/scope", source_type="HTML",
            priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
        s.commit()
    return "SRC-AU-TEST-SCOPE"


class TestExtractRequirementsFlagVisibility:
    def test_defaults_true_and_is_visible_on_get(self, client, admin_headers, seeded_source):
        resp = client.get(f"/api/v1/admin/sources/{seeded_source}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["extract_requirements"] is True

    def test_visible_in_list(self, client, admin_headers, seeded_source):
        resp = client.get("/api/v1/admin/sources", headers=admin_headers)
        assert resp.status_code == 200
        rows = {r["source_id"]: r for r in resp.json()}
        assert rows[seeded_source]["extract_requirements"] is True


class TestExtractRequirementsFlagSettable:
    def test_patch_can_scope_a_source_out(self, client, admin_headers, seeded_source):
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"extract_requirements": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["extract_requirements"] is False

        # Persisted, not just echoed back.
        again = client.get(f"/api/v1/admin/sources/{seeded_source}", headers=admin_headers)
        assert again.json()["extract_requirements"] is False

    def test_patch_omitted_leaves_it_unchanged(self, client, admin_headers, seeded_source):
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"notes": "unrelated change"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["extract_requirements"] is True  # unchanged default

    def test_create_can_scope_a_new_source_out(self, client, admin_headers):
        resp = client.post(
            "/api/v1/admin/sources",
            json={
                "source_id": "SRC-AU-NEW-REFERENCE-ONLY",
                "country_id": "AU", "program_id": "SKILLED_189", "visa_category": "SKILLED",
                "authority": "Home Affairs", "url": "https://example.invalid/reference-only",
                "extract_requirements": False,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["extract_requirements"] is False

    def test_create_defaults_true_when_omitted(self, client, admin_headers):
        resp = client.post(
            "/api/v1/admin/sources",
            json={
                "source_id": "SRC-AU-NEW-DEFAULT",
                "country_id": "AU", "program_id": "SKILLED_189", "visa_category": "SKILLED",
                "authority": "Home Affairs", "url": "https://example.invalid/default",
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["extract_requirements"] is True

    def test_requires_admin(self, client, engine, seeded_source):
        client.post(
            "/api/v1/auth/register",
            json={"email": "plain-user-scope@test.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "plain-user-scope@test.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        patch_resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"extract_requirements": False},
            headers=headers,
        )
        assert patch_resp.status_code == 403

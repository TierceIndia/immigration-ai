"""Regression tests for PATCH /api/v1/admin/sources/{id} status validation.

Before this fix, SourceUpdateRequest.status accepted any string, relying only on
the DB CHECK constraint (ck_source_status) to reject bad values — which surfaces
as a raw 500 (IntegrityError) on PostgreSQL, not a clean 422, and isn't enforced
at all on SQLite by default. An admin must only be able to set status to a value
that makes sense as a manual action (ACTIVE / DISABLED / NEEDS_MANUAL_CAPTURE) —
the other government_sources.status values (ACCESS_BLOCKED, NETWORK_RESTRICTED,
REQUIRES_BROWSER, REQUIRES_MANUAL_DOWNLOAD, BROKEN) are system-detected fetch
outcomes the sync pipeline sets automatically, not something to impose by hand.
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
        json={"email": "source-update-admin@test.com", "password": "admin123"},
    )
    with get_session_factory(engine)() as s:
        from db.models import ApiUser
        user = s.query(ApiUser).filter(ApiUser.email == "source-update-admin@test.com").first()
        user.role = "admin"
        s.commit()
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "source-update-admin@test.com", "password": "admin123"},
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
            source_id="SRC-AU-TEST-PATCH", country_id="AU", program_id="SKILLED_189",
            visa_category="SKILLED", authority="Home Affairs",
            url="https://example.invalid/189", source_type="HTML",
            priority="PRIMARY", monitoring_method="WEB", crawl_frequency="DAILY",
            status="ACTIVE",
        ))
        s.commit()
    return "SRC-AU-TEST-PATCH"


class TestSourceUpdateStatusValidation:
    @pytest.mark.parametrize("status", ["ACTIVE", "DISABLED", "NEEDS_MANUAL_CAPTURE"])
    def test_valid_status_is_accepted(self, client, admin_headers, seeded_source, status):
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"status": status},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == status

    @pytest.mark.parametrize("bad_status", [
        "ACCESS_BLOCKED", "NETWORK_RESTRICTED", "REQUIRES_BROWSER",
        "REQUIRES_MANUAL_DOWNLOAD", "BROKEN", "active", "not-a-real-status", "",
    ])
    def test_invalid_status_returns_422(self, client, admin_headers, seeded_source, bad_status):
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"status": bad_status},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_omitted_status_still_updates_other_fields(self, client, admin_headers, seeded_source):
        """Validation must not block updates that don't touch status at all."""
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"notes": "updated via test"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["notes"] == "updated via test"

    def test_explicit_null_status_returns_422_not_a_raw_500(self, client, admin_headers, seeded_source):
        """government_sources.status is NOT NULL — explicitly sending "status":
        null must be rejected cleanly, not reach the DB and raise a raw 500."""
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"status": None, "notes": "still fine"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_omitted_status_is_a_no_op(self, client, admin_headers, seeded_source):
        """Not sending the key at all must still work — this is the normal case
        for a PATCH that only touches other fields."""
        resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"notes": "no status touched"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ACTIVE"  # unchanged from the fixture

    def test_requires_admin(self, client, engine, seeded_source):
        client.post(
            "/api/v1/auth/register",
            json={"email": "plain-user-patch@test.com", "password": "pass123"},
        )
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "plain-user-patch@test.com", "password": "pass123"},
        )
        headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
        patch_resp = client.patch(
            f"/api/v1/admin/sources/{seeded_source}",
            json={"status": "DISABLED"},
            headers=headers,
        )
        assert patch_resp.status_code == 403

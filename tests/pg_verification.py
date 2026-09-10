"""Standalone PostgreSQL verification — tests G10, G11, G12 directly against real PG.

Run: DATABASE_URL=postgresql://... python tests/pg_verification.py
"""
from __future__ import annotations

import os
import sys

# Ensure src/ is on path
_SRC = os.path.join(os.path.dirname(__file__), '..', 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# CRITICAL: do NOT set TESTING=true — we want the lifespan to connect to PG
# But we need to test the routes directly, not through the lifespan
import uuid
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from db.models import Base, ApiUser, ApplicantProfile, Rule, Requirement, ImmigrationProgram, Country
from db.repositories import UserRepo, ApplicantRepo
from phase3.rule_repository import EnhancedRuleRepo
from api.helpers import parse_uuid_or_404
from fastapi import HTTPException

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("SKIP: DATABASE_URL not set")
    sys.exit(0)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Base.metadata.create_all(bind=engine)
Session = sessionmaker(bind=engine)

passed = 0
failed = 0
errors = []

# -----------------------------------------------------------------------
# G10: UUID path-parameter validation
# -----------------------------------------------------------------------
print("\n=== G10: UUID path-parameter validation ===")

def test_g10_valid_uuid():
    """Valid UUID should parse successfully."""
    result = parse_uuid_or_404(str(uuid.uuid4()))
    assert result is not None
    print("  PASS: valid UUID parses")

def test_g10_invalid_uuid_returns_404():
    """Non-UUID string should raise HTTPException 404, not 500."""
    try:
        parse_uuid_or_404("nonexistent")
        print("  FAIL: should have raised HTTPException")
        return False
    except HTTPException as e:
        assert e.status_code == 404, f"Expected 404, got {e.status_code}"
        print("  PASS: 'nonexistent' raises 404")
        return True
    except Exception as e:
        print(f"  FAIL: unexpected exception {type(e).__name__}: {e}")
        return False

def test_g10_empty_string():
    """Empty string should raise HTTPException 404."""
    try:
        parse_uuid_or_404("")
        print("  FAIL: should have raised HTTPException")
        return False
    except HTTPException as e:
        assert e.status_code == 404
        print("  PASS: empty string raises 404")
        return True

for fn in [test_g10_valid_uuid, test_g10_invalid_uuid_returns_404, test_g10_empty_string]:
    try:
        ok = fn()
        if ok is False:
            failed += 1
            errors.append(f"G10: {fn.__name__}")
        else:
            passed += 1
    except Exception as e:
        failed += 1
        errors.append(f"G10: {fn.__name__}: {e}")
        print(f"  FAIL: {e}")

# -----------------------------------------------------------------------
# G11: Response schema serialization under PostgreSQL
# -----------------------------------------------------------------------
print("\n=== G11: Response schema serialization under PostgreSQL ===")

def test_g11_applicant_id_is_string():
    """PostgreSQL ApplicantProfile.applicant_id should be a plain string."""
    with Session() as s:
        user_repo = UserRepo(s)
        user = user_repo.create(email=f"pg-test-{uuid.uuid4().hex[:8]}@test.com",
                                password_hash="hash", full_name="PG Test")
        s.flush()
        app_repo = ApplicantRepo(s)
        profile = app_repo.create(user_id=user.user_id, age=30)
        s.flush()
        
        # The value from the ORM should be a string (not uuid.UUID)
        val = profile.applicant_id
        print(f"    applicant_id type: {type(val).__name__}, value: {val}")
        assert isinstance(val, str), f"Expected str, got {type(val).__name__}"
        
        # Verify it round-trips through Pydantic schema
        from api.schemas.models import ApplicantResponse
        resp = ApplicantResponse(
            applicant_id=profile.applicant_id,
            user_id=profile.user_id,
            age=profile.age,
            created_at=profile.created_at,
        )
        assert isinstance(resp.applicant_id, str)
        print("  PASS: applicant_id is string and round-trips through Pydantic")
        s.rollback()
        return True

def test_g11_created_at_serialization():
    """PostgreSQL datetime with timezone should serialize correctly."""
    from datetime import datetime, timezone
    with Session() as s:
        now = datetime.now(timezone.utc)
        from api.schemas.models import ApplicantResponse
        resp = ApplicantResponse(
            applicant_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            created_at=now,
        )
        assert resp.created_at is not None
        print("  PASS: datetime with timezone serializes through Pydantic")
        return True

for fn in [test_g11_applicant_id_is_string, test_g11_created_at_serialization]:
    try:
        ok = fn()
        if ok is False:
            failed += 1
            errors.append(f"G11: {fn.__name__}")
        else:
            passed += 1
    except Exception as e:
        failed += 1
        errors.append(f"G11: {fn.__name__}: {e}")
        print(f"  FAIL: {type(e).__name__}: {e}")

# -----------------------------------------------------------------------
# G12: FK constraints — Rule.requirement_id must reference real Requirement
# -----------------------------------------------------------------------
print("\n=== G12: FK constraint enforcement under PostgreSQL ===")

def test_g12_rule_with_orphaned_requirement_id():
    """Creating a Rule with non-existent requirement_id should fail with FK violation."""
    with Session() as s:
        # First, create a program (needed for the Rule FK)
        country = Country(country_id="AU", name="Australia", iso_code="AU")
        s.merge(country)
        prog = ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="SKILLED", subclass_code="189",
            name="Skilled Independent", status="ACTIVE"
        )
        s.merge(prog)
        s.flush()
        
        # Now try to create a Rule with a fake requirement_id
        try:
            rule = Rule(
                rule_id=f"RULE-FK-TEST-{uuid.uuid4().hex[:8]}",
                requirement_id="NONEXISTENT-REQ-ID",
                program_id="SKILLED_189",
                category="age", field="age", operator="LT",
                value_json="45", mandatory=True, critical=True,
                message="FK test", status="DRAFT", current_version=1,
            )
            s.add(rule)
            s.flush()
            s.rollback()
            # If we get here, FK is NOT enforced
            print("  WARNING: FK violation NOT raised — SQLite-style behavior on PG?")
            return False  # Not necessarily a failure but noteworthy
        except Exception as e:
            s.rollback()
            if "foreign key" in str(e).lower() or "violates" in str(e).lower():
                print(f"  PASS: FK violation correctly raised: {type(e).__name__}")
                return True
            else:
                print(f"  FAIL: unexpected error: {type(e).__name__}: {e}")
                return False

def test_g12_rule_with_valid_requirement_id():
    """Creating a Rule with a valid requirement_id should succeed."""
    with Session() as s:
        s.merge(Country(country_id="AU", name="Australia", iso_code="AU"))
        s.merge(ImmigrationProgram(
            program_id="SKILLED_189", country_id="AU",
            visa_category="SKILLED", subclass_code="189",
            name="Skilled Independent", status="ACTIVE"
        ))
        s.flush()
        
        # Create a real Requirement first
        from db.models import ExtractionRun, GovernmentDocument, GovernmentSource
        from datetime import datetime, timezone
        # ExtractionRun.document_id is an FK to government_documents.document_id,
        # and government_documents.source_id is an FK to government_sources.source_id,
        # so both parent rows must exist before the child can be inserted.
        s.merge(GovernmentSource(
            source_id="SRC-AU-TEST",
            country_id="AU",
            program_id="SKILLED_189",
            visa_category="SKILLED",
            authority="Home Affairs",
            url="https://test.example/source",
            source_type="HTML",
            priority="PRIMARY",
        ))
        s.flush()
        s.merge(GovernmentDocument(
            document_id="DOC-TEST",
            source_id="SRC-AU-TEST",
            country_id="AU",
            program_id="SKILLED_189",
            visa_category="SKILLED",
            document_type="HTML",
            url="https://test.example/doc",
            retrieved_at=datetime.now(timezone.utc),
            content_hash="testhash",
            raw_path="/tmp/test",
        ))
        s.flush()
        er = ExtractionRun(
            run_id=f"ER-{uuid.uuid4().hex[:12]}",
            document_id="DOC-TEST", document_version=1,
            model="test", prompt_version="v1",
            status="COMPLETED",
        )
        s.add(er)
        s.flush()
        
        req = Requirement(
            requirement_id="REQ-FK-TEST-001",
            semantic_key="test-age-key",
            program_id="SKILLED_189",
            country_id="AU",
            category="age",
            name="Age requirement",
            operator="LT",
            status="APPROVED",
            extraction_run_id=er.run_id,
        )
        s.add(req)
        s.flush()
        
        rule = Rule(
            rule_id="RULE-FK-TEST-VALID",
            requirement_id="REQ-FK-TEST-001",
            program_id="SKILLED_189",
            category="age", field="age", operator="LT",
            value_json="45", mandatory=True, critical=True,
            message="Valid FK test", status="DRAFT", current_version=1,
        )
        s.add(rule)
        s.flush()
        
        # Verify it exists
        loaded = s.get(Rule, "RULE-FK-TEST-VALID")
        assert loaded is not None
        print("  PASS: Rule with valid requirement_id created successfully")
        s.rollback()
        return True

for fn in [test_g12_rule_with_orphaned_requirement_id, test_g12_rule_with_valid_requirement_id]:
    try:
        ok = fn()
        if ok is False:
            failed += 1
            errors.append(f"G12: {fn.__name__}")
        else:
            passed += 1
    except Exception as e:
        failed += 1
        errors.append(f"G12: {fn.__name__}: {e}")
        print(f"  FAIL: {type(e).__name__}: {e}")

# -----------------------------------------------------------------------
# G17: JWT sub-claim UUID hardening (auth_service.get_current_user)
# -----------------------------------------------------------------------
print("\n=== G17: JWT sub-claim UUID hardening under PostgreSQL ===")

def test_g17_raw_user_repo_get_with_malformed_id_crashes():
    """PROOF OF BUG: on a real PostgreSQL native-UUID column, UserRepo.get() with a
    non-UUID string raises a DataError/InvalidTextRepresentation - this is exactly the
    crash the G17 guard must prevent at the service layer."""
    with Session() as s:
        repo = UserRepo(s)
        try:
            repo.get("garbage-not-a-uuid")
            print("  WARNING: malformed id did NOT crash on PG (unexpected)")
            s.rollback()
            return False
        except Exception as e:
            s.rollback()
            if any(k in str(e).lower() for k in
                   ("invalid input syntax", "invalid text representation", "uuid")):
                print(f"  CONFIRMED: raw lookup crashes on PG: {type(e).__name__}")
                return True
            print(f"  FAIL: wrong error type: {type(e).__name__}: {e}")
            return False

def test_g17_auth_service_malformed_sub_returns_none():
    """The G17 guard: AuthService.get_current_user() must return None (-> caller 401)
    for a validly-signed token carrying a non-UUID sub claim, never crash the DB."""
    import os as _os
    from unittest.mock import patch
    from services.auth_service import AuthService, create_access_token

    _gen = str(uuid.uuid4())
    with patch.dict(_os.environ, {"JWT_SECRET_KEY": "pg-g17-test-key"}):
        token = create_access_token("garbage-not-a-uuid")["access_token"]
        svc = AuthService.__new__(AuthService)
        # Use the REAL UserRepo bound to the real PG session so that if the guard is
        # removed the test would crash with a DataError (proving the guard is load-bearing).
        svc._user_repo = UserRepo(Session())
        result = svc.get_current_user(token)
        assert result is None, f"Expected None, got {result!r}"
        print("  PASS: malformed sub claim -> get_current_user returns None (no crash)")
        return True

for fn in [test_g17_raw_user_repo_get_with_malformed_id_crashes,
           test_g17_auth_service_malformed_sub_returns_none]:
    try:
        ok = fn()
        if ok is False:
            failed += 1
            errors.append(f"G17: {fn.__name__}")
        else:
            passed += 1
    except Exception as e:
        failed += 1
        errors.append(f"G17: {fn.__name__}: {e}")
        print(f"  FAIL: {type(e).__name__}: {e}")

# -----------------------------------------------------------------------
# Diagnostic: per-source document version counts.
# IMPORTANT: document_versions has NO source_id column — the source lives on
# government_documents (document_versions only carries document_id). The query
# below is the CORRECT join. A previous manual check ran
#   SELECT source_id, COUNT(*) FROM document_versions GROUP BY source_id
# which errors: column "source_id" does not exist. Use this instead.
# -----------------------------------------------------------------------
def _diagnostic_source_version_counts():
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT d.source_id, COUNT(*) AS versions "
            "FROM document_versions v "
            "JOIN government_documents d ON d.document_id = v.document_id "
            "GROUP BY d.source_id ORDER BY d.source_id"
        )).fetchall()
    print(f"  PASS: source version counts ({len(rows)} sources)")
    for source_id, versions in rows:
        print(f"    {source_id}: {versions}")
    return True

try:
    if _diagnostic_source_version_counts():
        passed += 1
except Exception as e:
    failed += 1
    errors.append(f"document_versions -> source diagnostic: {e}")
    print(f"  FAIL: {type(e).__name__}: {e}")

# -----------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------
print(f"\n{'='*60}")
print(f"PostgreSQL verification: {passed} passed, {failed} failed")
if errors:
    print(f"Failed tests: {errors}")
print(f"{'='*60}")

sys.exit(1 if failed else 0)

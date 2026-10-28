"""Regression tests for the dev/image Postgres driver divergence.

This project never named a Postgres driver: every ``DATABASE_URL`` in the repo
was a bare ``postgresql://``, so the effective driver was whatever the installed
SQLAlchemy version defaults to. That default changed in SQLAlchemy 2.1
(psycopg2 -> psycopg v3), and because ``requirements.txt`` pinned only
``sqlalchemy>=2.0``:

  * the Docker image resolved to the psycopg v3 dialect, had no ``psycopg``
    module, and died at ``alembic upgrade head`` with
    ``ModuleNotFoundError: No module named 'psycopg'``;
  * a developer machine on SQLAlchemy 2.0.x resolved to psycopg2 and worked.

These tests pin the invariant that prevents a recurrence: the driver must be
named explicitly in every URL the project ships, that driver must be declared in
requirements.txt, and the SQLAlchemy major line must not float unbounded.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy.engine import make_url

# Files that declare a Postgres connection string the application will use.
URL_DECLARING_FILES = [
    "docker-compose.yml",
    ".env.example",
]

_BARE_PG_URL = re.compile(r"postgresql://")

# SQLAlchemy driver name -> the distribution(s) that provide it. A driver is
# importable if ANY of these is importable.
_DRIVER_PACKAGES = {
    "psycopg2": ("psycopg2", "psycopg2-binary"),
    "psycopg": ("psycopg",),
}


def _driver_is_importable(driver: str) -> bool:
    import importlib

    return any(
        _is_importable(mod) for mod in _DRIVER_PACKAGES.get(driver, (driver,))
    )


def _is_importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def _requirements() -> dict[str, str]:
    """Parse requirements.txt into {package_name: specifier}."""
    out: dict[str, str] = {}
    for raw in _read("requirements.txt").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[\s]", line, maxsplit=1)[0].strip().lower()
        spec = line[len(name) :].strip()
        out[name] = spec
    return out


class TestRequirementsPinTheDialectSource:
    def test_sqlalchemy_major_line_is_bounded(self):
        """An unbounded `sqlalchemy>=2.0` is what let the image and a dev
        machine land on different major versions in the first place."""
        spec = _requirements()["sqlalchemy"]
        assert "<" in spec, f"sqlalchemy must have an upper bound, got {spec!r}"
        assert any(c.isdigit() for c in spec.split("<", 1)[1]), (
            f"sqlalchemy upper bound must name a version, got {spec!r}"
        )

    def test_psycopg2_is_still_declared(self):
        assert "psycopg2-binary" in _requirements()


class TestShippedUrlsNameTheDriverExplicitly:
    @pytest.mark.parametrize("filename", URL_DECLARING_FILES)
    def test_no_bare_postgresql_url(self, filename):
        """A bare `postgresql://` silently re-inherits the version-dependent
        default dialect that caused the outage."""
        offenders = [
            f"{i}: {line.strip()}"
            for i, line in enumerate(_read(filename).splitlines(), 1)
            if _BARE_PG_URL.search(line) and not line.lstrip().startswith("#")
        ]
        assert not offenders, (
            f"{filename} uses a bare postgresql:// URL (driver not pinned): "
            + "; ".join(offenders)
        )

    @pytest.mark.parametrize("filename", URL_DECLARING_FILES)
    def test_declared_driver_is_present_in_requirements(self, filename):
        urls = re.findall(r"postgresql\+(\w+)://", _read(filename))
        assert urls, f"{filename} declares no explicit-driver PostgreSQL URL"
        reqs = _requirements()
        for driver in set(urls):
            packages = _DRIVER_PACKAGES.get(driver, (driver,))
            assert any(p in reqs for p in packages), (
                f"{filename} requests driver {driver!r} but requirements.txt "
                f"declares none of {packages}"
            )

    @pytest.mark.parametrize("filename", URL_DECLARING_FILES)
    def test_declared_driver_is_importable(self, filename):
        for driver in set(re.findall(r"postgresql\+(\w+)://", _read(filename))):
            assert _driver_is_importable(driver), (
                f"{filename} names driver {driver!r}, which is not importable "
                f"in this environment"
            )

    def test_compose_url_resolves_to_the_psycopg2_dialect(self):
        url = re.search(
            r"postgresql\+(\w+)://[^\s\"']+", _read("docker-compose.yml")
        ).group(0)
        dialect = make_url(url).get_dialect()
        assert dialect.driver == "psycopg2"


class TestExplicitDriverKeepsBackendDetectionWorking:
    """Making the driver explicit must not change how the app reports its
    backend — `config.database_query()` branches on `url.startswith("postgres")`
    and `database_info()` uses `get_backend_name()`."""

    def test_database_query_reports_postgresql(self, monkeypatch):
        from config import database_query

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@dbhost:5432/mydb")
        assert database_query() == "postgresql"

    def test_database_info_reports_postgresql_backend(self, monkeypatch):
        from config import database_info

        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@dbhost:5432/mydb")
        info = database_info()
        assert info["backend"] == "postgresql"
        assert info["host"] == "dbhost"
        assert info["database"] == "mydb"

    def test_credential_scrubbing_still_matches_explicit_driver_url(self, monkeypatch):
        from config import scrub_credential_string

        scrubbed = scrub_credential_string(
            "could not connect: postgresql+psycopg2://app:realpassword@dbhost:5432 failed"
        )
        assert "realpassword" not in scrubbed
        assert "app:***@dbhost" in scrubbed

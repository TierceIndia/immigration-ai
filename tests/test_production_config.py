"""Fail-closed production configuration tests.

Development remains convenient; production must refuse placeholders, short
secrets, credential-free databases and unsafe CORS/network escape hatches.
"""
from __future__ import annotations

import pytest

from production_config import (
    ProductionConfigError,
    is_production,
    validate_database_url,
    validate_jwt_secret,
    validate_runtime_config,
)


@pytest.fixture()
def prod_env(monkeypatch):
    for key in (
        "ENVIRONMENT", "JWT_SECRET_KEY", "DATABASE_URL", "CORS_ORIGINS",
        "JWT_ALGORITHM", "DEBUG", "ALLOW_PUBLIC_REGISTRATION",
        "FETCH_ALLOW_PRIVATE_HOSTS", "FETCH_VALIDATE_DESTINATION_DNS",
        "PASSWORD_MIN_LENGTH",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "8")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 64)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://immigration_user:strong-db-password@db:5432/immigration",
    )
    monkeypatch.setenv("CORS_ORIGINS", "https://knowledge.example.gov.au")
    monkeypatch.setenv("DEBUG", "false")


def test_development_is_not_constrained(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "dev-secret-change-me")
    assert is_production() is False
    assert validate_runtime_config()["validated"] is False


def test_valid_production_config_passes(prod_env):
    result = validate_runtime_config()
    assert result == {"environment": "production", "validated": True}


@pytest.mark.parametrize("value", ["", "   ", "short", "dev-secret-change-me", "change-me-in-production"])
def test_jwt_secret_rejects_missing_short_and_placeholder(value):
    assert validate_jwt_secret(value)


def test_jwt_secret_accepts_a_long_non_placeholder():
    assert validate_jwt_secret("z" * 32) == []


@pytest.mark.parametrize(
    "url",
    [
        "",
        "sqlite:///local.db",
        "postgresql+psycopg2://db:5432/immigration",
        "postgresql+psycopg2://user:@db:5432/immigration",
        "postgresql+psycopg2://user:immigration_dev@db:5432/immigration",
        "postgresql+psycopg2://user:password@db:5432/immigration",
    ],
)
def test_database_url_rejects_unsafe_values(url):
    assert validate_database_url(url)


def test_database_url_accepts_postgres_with_real_credentials():
    assert validate_database_url(
        "postgresql+psycopg2://user:strong-password@db:5432/immigration"
    ) == []


def test_runtime_rejects_network_escape_hatches(prod_env, monkeypatch):
    monkeypatch.setenv("FETCH_ALLOW_PRIVATE_HOSTS", "true")
    monkeypatch.setenv("FETCH_VALIDATE_DESTINATION_DNS", "false")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_runtime_config()
    assert "FETCH_ALLOW_PRIVATE_HOSTS" in str(excinfo.value)
    assert "FETCH_VALIDATE_DESTINATION_DNS" in str(excinfo.value)


def test_runtime_rejects_insecure_cors_and_debug(prod_env, monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,*")
    monkeypatch.setenv("DEBUG", "true")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_runtime_config()
    message = str(excinfo.value)
    assert "CORS" in message
    assert "DEBUG" in message


def test_runtime_rejects_weak_password_policy(prod_env, monkeypatch):
    monkeypatch.setenv("PASSWORD_MIN_LENGTH", "6")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_runtime_config()
    assert "PASSWORD_MIN_LENGTH" in str(excinfo.value)


def test_runtime_rejects_unknown_jwt_algorithm(prod_env, monkeypatch):
    monkeypatch.setenv("JWT_ALGORITHM", "none")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_runtime_config()
    assert "JWT_ALGORITHM" in str(excinfo.value)


def test_runtime_lists_all_problems_at_once(prod_env, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///nope.db")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost")
    with pytest.raises(ProductionConfigError) as excinfo:
        validate_runtime_config()
    message = str(excinfo.value)
    assert message.count(";") >= 2

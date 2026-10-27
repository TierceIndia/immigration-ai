"""Fail-closed validation for production runtime configuration.

Development is allowed to be convenient. Production is not: a deployment with a
missing, short, or well-known placeholder secret is refused at startup rather
than quietly running with a key an attacker can guess. The same applies to a
credential-free database URL and a wildcard/plain-HTTP CORS origin.

This module is deliberately side-effect free except for logging, so it can be
unit-tested without constructing the FastAPI app.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from logging_config import get_logger

logger = get_logger(__name__)

PRODUCTION = "production"

# Values that have shipped in examples, compose defaults, or docs. They are
# never acceptable as production secrets, even if long enough.
PLACEHOLDER_SECRETS = {
    "change-me-in-production",
    "changeme-generate-a-real-jwt-secret",
    "changeme-generate-a-real-secret",
    "changeme",
    "change-me",
    "dev-secret-change-me",
    "secret",
    "password",
    "your-key-here",
    "sk-your-key-here",
}

MIN_JWT_SECRET_LENGTH = 32
_ALLOWED_JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}


class ProductionConfigError(RuntimeError):
    """Production configuration is unsafe; startup must stop."""


def environment() -> str:
    return (os.getenv("ENVIRONMENT") or "development").strip().lower()


def is_production() -> bool:
    return environment() == PRODUCTION


def _is_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered in PLACEHOLDER_SECRETS
        or "change-me" in lowered
        or "changeme" in lowered
        or lowered.startswith("your-")
    )


def validate_jwt_secret(value: str | None) -> list[str]:
    problems: list[str] = []
    if not value or not value.strip():
        return ["JWT_SECRET_KEY is required in production"]
    if _is_placeholder(value):
        problems.append("JWT_SECRET_KEY is a known placeholder value")
    if len(value) < MIN_JWT_SECRET_LENGTH:
        problems.append(
            f"JWT_SECRET_KEY must be at least {MIN_JWT_SECRET_LENGTH} characters"
        )
    if value != value.strip():
        problems.append("JWT_SECRET_KEY must not have leading/trailing whitespace")
    return problems


def validate_database_url(value: str | None) -> list[str]:
    if not value or not value.strip():
        return ["DATABASE_URL is required in production"]
    try:
        parsed = urlparse(value)
    except ValueError:
        return ["DATABASE_URL is not a valid URL"]
    if not parsed.scheme.startswith("postgresql"):
        return ["DATABASE_URL must use PostgreSQL in production"]
    if not parsed.hostname:
        return ["DATABASE_URL must include a database host"]
    if not parsed.username or parsed.password is None or not parsed.password:
        return ["DATABASE_URL must include non-empty database credentials"]
    if parsed.password.strip().lower() in {
        "immigration_dev", "postgres", "password", "changeme", "change-me",
    }:
        return ["DATABASE_URL contains a well-known development database password"]
    return []


def validate_cors_origins(value: str | None) -> list[str]:
    if not value or not value.strip():
        return ["CORS_ORIGINS is required in production"]
    problems: list[str] = []
    origins = [o.strip() for o in value.split(",") if o.strip()]
    if not origins:
        return ["CORS_ORIGINS must contain at least one origin"]
    for origin in origins:
        parsed = urlparse(origin)
        if origin == "*":
            problems.append("CORS_ORIGINS must not contain '*' with credentials")
        elif parsed.scheme != "https" or not parsed.netloc:
            problems.append(f"CORS origin must be an absolute https URL: {origin!r}")
        elif parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            problems.append(f"CORS origin must not be a localhost origin: {origin!r}")
    return problems


def validate_runtime_config() -> dict[str, object]:
    """Validate production-only settings and return a safe summary.

    Raises :class:`ProductionConfigError` with *all* problems listed, so an
    operator can fix the deployment in one pass. Non-production environments
    are intentionally not constrained here.
    """
    env = environment()
    if env != PRODUCTION:
        return {"environment": env, "validated": False}

    problems: list[str] = []
    problems.extend(validate_jwt_secret(os.getenv("JWT_SECRET_KEY")))
    problems.extend(validate_database_url(os.getenv("DATABASE_URL")))
    problems.extend(validate_cors_origins(os.getenv("CORS_ORIGINS")))

    algorithm = (os.getenv("JWT_ALGORITHM") or "HS256").strip().upper()
    if algorithm not in _ALLOWED_JWT_ALGORITHMS:
        problems.append(
            f"JWT_ALGORITHM must be one of {sorted(_ALLOWED_JWT_ALGORITHMS)} "
            "when a shared HMAC secret is used"
        )

    if (os.getenv("DEBUG") or "").strip().lower() in {"1", "true", "yes", "on"}:
        problems.append("DEBUG must be false in production")

    # Local/test escape hatches that would materially weaken fetch security.
    if (os.getenv("FETCH_ALLOW_PRIVATE_HOSTS") or "false").strip().lower() == "true":
        problems.append("FETCH_ALLOW_PRIVATE_HOSTS must not be enabled in production")
    if (os.getenv("FETCH_VALIDATE_DESTINATION_DNS") or "true").strip().lower() in {
        "0", "false", "no", "off",
    }:
        problems.append("FETCH_VALIDATE_DESTINATION_DNS must not be disabled in production")

    try:
        password_min = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))
    except ValueError:
        password_min = 0
    if password_min < 8:
        problems.append("PASSWORD_MIN_LENGTH must be at least 8 in production")

    if (os.getenv("ALLOW_PUBLIC_REGISTRATION") or "false").strip().lower() == "true":
        logger.warning(
            "ALLOW_PUBLIC_REGISTRATION=true in production: anyone can create an "
            "applicant account. This is only safe if that is a deliberate choice."
        )

    if problems:
        detail = "; ".join(problems)
        logger.error("Unsafe production configuration", extra={"problems": problems})
        raise ProductionConfigError(f"Refusing to start with unsafe production configuration: {detail}")

    logger.info("Production configuration validated")
    return {"environment": env, "validated": True}

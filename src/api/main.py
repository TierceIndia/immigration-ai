"""FastAPI application entry point."""
from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from logging_config import get_logger, set_request_context, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(level="INFO", json_output=True)
    # Initialize database engine if DATABASE_URL is set
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        from db.connection import get_engine, init_db
        engine = get_engine(db_url)
        init_db(engine)
        app.state.db_engine = engine
        logger.info("Database engine initialized")
    logger.info("Immigration Knowledge API starting up")
    yield
    logger.info("Immigration Knowledge API shutting down")


app = FastAPI(
    title="Immigration Knowledge API",
    description="Deterministic eligibility assessment for Australian skilled-migration visas.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware: CORS + request-ID injection
# ---------------------------------------------------------------------------
# Use CORS_ORIGINS from env (comma-separated list). Default to localhost for dev.
# WARNING: allow_origins=["*"] with allow_credentials=True is a browser security risk.
_cors_origins_str = os.getenv("CORS_ORIGINS", "http://localhost:3000")
_cors_origins = [o.strip() for o in _cors_origins_str.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    with set_request_context(request_id=req_id):
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
from api.routes import applicants, assessments, auth, health, admin_rules  # noqa: E402

app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(applicants.router, prefix="/api/v1/applicants", tags=["applicants"])
app.include_router(assessments.router, prefix="/api/v1/assessments", tags=["assessments"])
app.include_router(admin_rules.router, prefix="/api/v1/admin/rules", tags=["admin-rules"])


def create_app() -> FastAPI:
    """Factory used by Docker CMD: ``uvicorn src.api.main:create_app --factory``."""
    return app

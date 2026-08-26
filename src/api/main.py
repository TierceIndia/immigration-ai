"""FastAPI application entry point."""
from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager

# Ensure src/ is on sys.path so top-level imports (logging_config, db, etc.) resolve
# when running via: uvicorn src.api.main:create_app --factory
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from logging_config import get_logger, set_request_context, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(level="INFO", json_output=True)

    # Always initialize db_engine so routes can safely access it
    if not getattr(app.state, "db_engine", None):
        app.state.db_engine = None

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            from db.connection import get_engine, init_db
            engine = get_engine(db_url)
            init_db(engine)
            app.state.db_engine = engine
            logger.info("Database engine initialized")
        except Exception as e:
            logger.warning("Database connection failed — starting without DB",
                           extra={"error": str(e)})

    # Start scheduler if ENABLE_SCHEDULER=true
    if os.getenv("ENABLE_SCHEDULER", "false").lower() == "true":
        if getattr(app.state, "db_engine", None):
            from scheduler import start_scheduler
            start_scheduler(app.state.db_engine)
            logger.info("Scheduler started")
        else:
            logger.warning("Scheduler disabled — no database connection")

    logger.info("Immigration Knowledge API starting up")
    yield

    # Shutdown scheduler
    from scheduler import shutdown_scheduler
    shutdown_scheduler()
    logger.info("Immigration Knowledge API shutting down")


app = FastAPI(
    title="Immigration Knowledge API",
    description="Deterministic eligibility assessment for Australian skilled-migration visas.",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(RuntimeError)
async def runtime_error_handler(request: Request, exc: RuntimeError):
    if "Database not available" in str(exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"detail": "Database not available. Check DATABASE_URL configuration."},
        )
    raise exc


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
from api.routes import applicants, assessments, auth, health, admin_rules, pipeline_runs  # noqa: E402

app.include_router(health.router)
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(applicants.router, prefix="/api/v1/applicants", tags=["applicants"])
app.include_router(assessments.router, prefix="/api/v1/assessments", tags=["assessments"])
app.include_router(admin_rules.router, prefix="/api/v1/admin/rules", tags=["admin-rules"])
app.include_router(pipeline_runs.router, prefix="/api/v1/admin/runs", tags=["pipeline-runs"])


@app.post("/api/v1/admin/sync", tags=["admin-sync"])
def trigger_sync(request: Request):
    """Manually trigger government source sync (admin only)."""
    from db.connection import get_db_engine
    from scheduler import trigger_sync_now
    engine = get_db_engine(request)
    return trigger_sync_now(engine)


def create_app() -> FastAPI:
    """Factory used by Docker CMD: ``uvicorn src.api.main:create_app --factory``."""
    return app

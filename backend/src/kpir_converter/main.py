"""FastAPI application factory and local server entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api import routes_documents, routes_export, routes_jobs
from .api.deps import CSRF_COOKIE, issue_csrf_token, new_correlation_id
from .application.services import AppContext
from .config import Settings, get_settings
from .infrastructure.db.database import Database
from .infrastructure.storage.profiles_loader import load_registry
from .logging_setup import configure_logging

logger = logging.getLogger(__name__)

CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)


def build_context(settings: Settings) -> AppContext:
    settings.ensure_directories()
    db = Database(settings.db_path)
    db.migrate()
    profiles = load_registry(settings.resolved_profiles_dir())
    return AppContext(settings=settings, db=db, profiles=profiles)


def create_app(settings: Settings | None = None, ctx: AppContext | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.ctx = ctx or build_context(settings)
        logger.info("api started", extra={"status": "ready"})
        yield
        app.state.ctx.db.close()

    app = FastAPI(
        title="KPiR Converter API",
        version="1.0.0",
        description=(
            "Lokalne API konwertera KPiR PDF -> XLSX. Działa wyłącznie offline, "
            "nasłuchuje domyślnie na 127.0.0.1."
        ),
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        lifespan=lifespan,
    )
    app.state.settings = settings

    if settings.dev_mode and settings.allowed_origins():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins(),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-CSRF-Token"],
        )

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        # 1. Strict Host allow-list: block DNS rebinding against a local service.
        host = (request.headers.get("host") or "").lower()
        allowed = {h.lower() for h in settings.allowed_hosts()}
        host_ok = host in allowed or host.split(":")[0] in {h.split(":")[0] for h in allowed}
        if settings.dev_mode:
            # A sandboxed preview proxies through a generated hostname.
            host_ok = True
        if not host_ok:
            return JSONResponse(
                status_code=421,
                content={
                    "error": {
                        "code": "HOST_NOT_ALLOWED",
                        "message": "niedozwolony nagłówek Host",
                        "correlationId": new_correlation_id(),
                    }
                },
            )

        # 2. Origin check for state changing requests.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin:
                permitted = set(settings.allowed_origins())
                permitted |= {f"http://{h}" for h in allowed}
                permitted |= {f"https://{h}" for h in allowed}
                if origin not in permitted and not settings.dev_mode:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "error": {
                                "code": "ORIGIN_NOT_ALLOWED",
                                "message": "niedozwolony nagłówek Origin",
                                "correlationId": new_correlation_id(),
                            }
                        },
                    )

        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        # 3. Double-submit CSRF cookie, issued on first contact.
        if CSRF_COOKIE not in request.cookies:
            response.set_cookie(
                CSRF_COOKIE,
                issue_csrf_token(),
                httponly=False,  # the SPA must read it to echo it back
                samesite="strict",
                secure=False,  # loopback HTTP
                path="/",
            )
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"HTTP_{exc.status_code}",
                    "message": str(exc.detail),
                    "correlationId": new_correlation_id(),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(p) for p in first.get("loc", ())[1:]) or None
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "niepoprawne dane wejściowe",
                    "field": field,
                    "correlationId": new_correlation_id(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception):
        correlation_id = new_correlation_id()
        logger.error(
            "unhandled error",
            extra={"correlation_id": correlation_id, "error_type": type(exc).__name__},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "wystąpił nieoczekiwany błąd",
                    "correlationId": correlation_id,
                }
            },
        )

    @app.get("/api/v1/health", tags=["system"])
    def health(request: Request) -> dict:
        context: AppContext = request.app.state.ctx
        return {
            "status": "ok",
            "offline": True,
            "profiles": [p.qualified_id for p in context.profiles.all()],
            "activeJobs": context.jobs.active_count(),
        }

    app.include_router(routes_documents.router)
    app.include_router(routes_jobs.router)
    app.include_router(routes_export.router)

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA same-origin when it exists (packaged mode)."""
    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not dist.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(dist.resolve())):
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


def run_api() -> None:  # pragma: no cover - entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "kpir_converter.main:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":  # pragma: no cover
    run_api()

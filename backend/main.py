import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from database import init_db, close_db
from routers import clients, candidates, mappings, scraping, whatsapp, pipeline, schedule, admin, cron
from routers import matching, outreach, ringg, analytics, client_portal, candidate_portal, company_portal

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    await close_db()

_default_origins = "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176,http://localhost:5180"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")

app = FastAPI(title="JustAccountants API", lifespan=lifespan, redirect_slashes=False)


class _StripSlash:
    """Rewrite trailing slashes without redirecting (avoids HTTP→HTTPS redirect issues on Cloud Run)."""
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path != "/" and path.endswith("/"):
                scope = {**scope, "path": path[:-1], "raw_path": path[:-1].encode()}
        await self.app(scope, receive, send)

app.add_middleware(_StripSlash)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(clients.router)
app.include_router(candidates.router)
app.include_router(mappings.router)
app.include_router(scraping.router)
app.include_router(whatsapp.router)
app.include_router(pipeline.router)
app.include_router(schedule.router)
app.include_router(admin.router)
app.include_router(cron.router)
app.include_router(matching.router)
app.include_router(outreach.router)
app.include_router(ringg.router)
app.include_router(analytics.router)
app.include_router(client_portal.router)
app.include_router(candidate_portal.router)
app.include_router(company_portal.router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled 500s, alert Google Chat, return safe response."""
    import asyncio
    from fastapi import HTTPException
    from services.google_chat_service import alert_exception

    # Let HTTPException pass through normally — those are intentional 4xx/5xx
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    asyncio.create_task(alert_exception(
        exc=exc,
        endpoint=f"{request.method} {request.url.path}",
        extra={"query": str(request.query_params) or None},
    ))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

import asyncio
import logging
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import engine, Base, SessionLocal
from app.services.seed_data import seed_database
from app.services.pricing_engine import pricing_background_task
from app.routers import auth, web, link, open_api, admin, internal

pricing_task = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Log startup configuration audit (names only, never values)
    logging.basicConfig(level=logging.INFO)
    settings.log_startup_audit()

    # Initialize DB & Seed Data
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_database(db)
    finally:
        db.close()

    # Start 10s pricing nudge background task
    global pricing_task
    pricing_task = asyncio.create_task(pricing_background_task())

    yield

    # Shutdown
    if pricing_task:
        pricing_task.cancel()

app = FastAPI(
    title=f"{settings.PLATFORM_NAME} Sandbox API",
    description="Simulated Indian Bond Investment Platform & Data Provider for Portfolio Aggregators",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS
_cors_origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins if _cors_origins else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static Files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

from app.templates_engine import templates

# Exception handler for Open API errors
@app.exception_handler(HTTPException)
async def custom_http_exception_handler(request: Request, exc: HTTPException):
    # If API call, ensure errors JSON format
    if request.url.path.startswith("/open/v1"):
        if isinstance(exc.detail, dict) and "errors" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return JSONResponse(
            status_code=exc.status_code,
            content={"errors": [{"code": f"HTTP_{exc.status_code}", "detail": str(exc.detail)}]},
            headers=exc.headers
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "UP",
        "platform": settings.PLATFORM_NAME,
        "environment": "sandbox",
        "timestamp": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat()
    }

# Include Routers
app.include_router(auth.router)
app.include_router(web.router)
app.include_router(link.router)
app.include_router(open_api.router)
app.include_router(admin.router)
app.include_router(internal.router)

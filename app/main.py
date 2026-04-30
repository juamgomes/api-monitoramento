from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import api_router, health_router
from app.config import get_settings
from app.database import init_db
from app.services.monitoring import MonitoringCoordinator


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    coordinator = MonitoringCoordinator()
    app.state.monitoring = coordinator
    await coordinator.start()
    yield
    await coordinator.stop()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_origin_regex=settings.cors_allowed_origin_regex,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(api_router)

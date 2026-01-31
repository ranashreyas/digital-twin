from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import init_db
from app.api import auth, chat

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create database tables
    await init_db()
    yield
    # Shutdown: cleanup if needed


app = FastAPI(
    title="Digital Twin API",
    description="API for the Digital Twin chatbot - your personal AI assistant",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        settings.frontend_url,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(chat.router)


@app.get("/")
async def root():
    return {
        "message": "Digital Twin API",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}

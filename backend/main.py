from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import logging
from contextlib import asynccontextmanager
from config import settings
from database import close_db_pool
from api.routes import chats, process, scraping, users, websocket_router
from websocket_manager import chat_connection_manager, set_main_loop

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown events.
    Captures the main event loop before any threads start.
    """
    # Capture the running loop before any threads start
    main_loop = asyncio.get_running_loop()
    set_main_loop(main_loop)
    logger.info(f"✅ Main event loop captured for thread-safe publishing: {id(main_loop)}")
    
    # Initialize Redis in the background
    await chat_connection_manager._ensure_redis()
    logger.info("✅ Redis initialized for WebSocket manager")
    
    yield
    
    # Shutdown: close database connections
    await close_db_pool()
    await chat_connection_manager.close_all()
    logger.info("🛑 Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Universal Scraper",
    description="Turn any website into a knowledge graph with AI-powered scraping and data extraction.",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(chats.router)
app.include_router(process.router)
app.include_router(scraping.router)
app.include_router(users.router)
app.include_router(websocket_router)


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Universal Scraper API is running",
        "version": "1.0.0",
        "status": "healthy"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from contextlib import asynccontextmanager

from config import settings
from database import close_db_pool
from api.routes import chats, process, scraping, users
from websocket_manager import chat_connection_manager

# Create FastAPI app
app = FastAPI(
    title="Universal Scraper API",
    description="API for Universal Website Scraping + RAG Platform",
    version="1.0.0"
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


@app.on_event("shutdown")
async def shutdown_event():
    """Close database connections on shutdown"""
    await close_db_pool()


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


@app.websocket("/ws/{chat_id}")
async def chat_websocket(websocket: WebSocket, chat_id: str):
    """Join a chat room and stream messages saved for that conversation."""
    await chat_connection_manager.join(chat_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        chat_connection_manager.leave(chat_id, websocket)
    except Exception:
        chat_connection_manager.leave(chat_id, websocket)


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )
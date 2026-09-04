# websocket_manager.py
import asyncio
import json
import logging
from typing import Dict, Set, Optional, Any
from fastapi import WebSocket
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class ChatConnectionManager:
    """
    WebSocket connection manager with Redis Pub/Sub for cross-process communication.
    Workers publish to Redis, and this manager forwards messages to connected clients.
    """
    
    CHANNEL_PREFIX = "ws:chat:"
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}
        self.chat_users: Dict[str, Set[str]] = {}
        self.pubsub_tasks: Dict[str, asyncio.Task] = {}
        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self._redis_initialized = False
        self._init_task: Optional[asyncio.Task] = None
    
    async def _init_redis(self):
        """Initialize Redis connection."""
        if self._redis_initialized:
            return
        
        try:
            from redis_config import REDIS_URL
            self.redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
            self.pubsub = self.redis_client.pubsub()
            self._redis_initialized = True
            logger.info("✅ Redis connection initialized for WebSocket manager")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Redis: {e}")
            self._redis_initialized = False
    
    async def _ensure_redis(self):
        """Ensure Redis is initialized, retry if needed."""
        if not self._redis_initialized:
            await self._init_redis()
        return self._redis_initialized
    
    def _get_channel(self, chat_id: str) -> str:
        """Get Redis channel name for a chat."""
        return f"{self.CHANNEL_PREFIX}{chat_id}"
    
    async def join(self, websocket: WebSocket, chat_id: str, user_id: Optional[str] = None):
        """Accept a WebSocket connection and add it to the chat room."""
        await websocket.accept()
        
        # Ensure Redis is initialized
        await self._ensure_redis()
        
        # Add to local connections
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = set()
            self.chat_users[chat_id] = set()
        
        self.active_connections[chat_id].add(websocket)
        self.connection_metadata[websocket] = {
            'chat_id': chat_id,
            'user_id': user_id,
            'connected_at': asyncio.get_event_loop().time()
        }
        
        if user_id:
            self.chat_users[chat_id].add(user_id)
            await self._broadcast_local(chat_id, {
                'type': 'user_joined',
                'user_id': user_id,
                'users': list(self.chat_users[chat_id])
            })
        
        # Start Redis listener for this chat if not already running
        if chat_id not in self.pubsub_tasks or self.pubsub_tasks[chat_id].done():
            self.pubsub_tasks[chat_id] = asyncio.create_task(
                self._listen_to_redis(chat_id)
            )
        
        logger.info(f"🔌 WebSocket connected for chat {chat_id} (user: {user_id})")
    
    async def leave(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        metadata = self.connection_metadata.get(websocket, {})
        chat_id = metadata.get('chat_id')
        user_id = metadata.get('user_id')
        
        if chat_id and chat_id in self.active_connections:
            self.active_connections[chat_id].discard(websocket)
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
                # Clean up Redis listener
                if chat_id in self.pubsub_tasks:
                    self.pubsub_tasks[chat_id].cancel()
                    del self.pubsub_tasks[chat_id]
        
        self.connection_metadata.pop(websocket, None)
        
        if user_id and chat_id and chat_id in self.chat_users:
            self.chat_users[chat_id].discard(user_id)
            await self._broadcast_local(chat_id, {
                'type': 'user_left',
                'user_id': user_id,
                'users': list(self.chat_users.get(chat_id, []))
            })
        
        try:
            await websocket.close()
        except Exception:
            pass
        
        logger.info(f"🔌 WebSocket disconnected for chat {chat_id}")
    
    async def _broadcast_local(self, chat_id: str, message: Dict[str, Any]):
        """Broadcast to local connections only (no Redis)."""
        if chat_id not in self.active_connections:
            return
        
        # Ensure message is JSON serializable
        message = self._prepare_for_json(message)
        message_json = json.dumps(message)
        to_remove = []
        
        for connection in self.active_connections[chat_id]:
            try:
                await connection.send_text(message_json)
            except Exception:
                to_remove.append(connection)
        
        for connection in to_remove:
            await self.leave(connection)
    
    def _prepare_for_json(self, data: Any) -> Any:
        """Recursively convert datetime objects to ISO strings for JSON serialization."""
        if isinstance(data, dict):
            return {k: self._prepare_for_json(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [self._prepare_for_json(item) for item in data]
        elif isinstance(data, set):
            return [self._prepare_for_json(item) for item in data]
        elif hasattr(data, 'isoformat'):
            return data.isoformat()
        elif hasattr(data, '__dict__'):
            return self._prepare_for_json(data.__dict__)
        else:
            return data
    
    async def _listen_to_redis(self, chat_id: str):
        """Listen for Redis messages for a specific chat."""
        # Ensure Redis is available
        if not await self._ensure_redis():
            logger.warning(f"⚠️ Redis not available, cannot listen for chat {chat_id}")
            return
        
        if not self.pubsub:
            logger.warning(f"⚠️ Redis pubsub not available, cannot listen for chat {chat_id}")
            return
        
        channel = self._get_channel(chat_id)
        
        try:
            await self.pubsub.subscribe(channel)
            logger.info(f"📡 Subscribed to Redis channel: {channel}")
            
            while True:
                try:
                    message = await self.pubsub.get_message(timeout=1.0)
                    if message is None:
                        continue
                    
                    if message.get('type') == 'message':
                        try:
                            data = json.loads(message.get('data', '{}'))
                            await self._broadcast_local(chat_id, data)
                        except json.JSONDecodeError:
                            logger.warning(f"Failed to parse Redis message: {message.get('data')}")
                        except Exception as e:
                            logger.error(f"Error processing Redis message: {e}")
                    
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Redis listener error for {chat_id}: {e}")
                    await asyncio.sleep(0.1)
        
        except asyncio.CancelledError:
            pass
        finally:
            try:
                await self.pubsub.unsubscribe(channel)
                logger.info(f"📡 Unsubscribed from Redis channel: {channel}")
            except Exception:
                pass
    
    async def publish_to_redis(self, chat_id: str, message: Dict[str, Any]):
        """Publish a message to Redis for cross-process delivery."""
        # Ensure Redis is initialized
        if not await self._ensure_redis():
            logger.warning(f"⚠️ Redis not available, cannot publish for chat {chat_id}")
            return False
        
        if not self.redis_client:
            logger.warning(f"⚠️ Redis client not available, cannot publish for chat {chat_id}")
            return False
        
        try:
            channel = self._get_channel(chat_id)
            message = self._prepare_for_json(message)
            message_json = json.dumps(message)
            await self.redis_client.publish(channel, message_json)
            logger.debug(f"📡 Published to Redis: {channel}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to publish to Redis: {e}")
            return False
    
    async def broadcast(self, chat_id: str, message: Dict[str, Any], publish_to_redis: bool = True):
        """
        Broadcast a message to all clients in a chat.
        If publish_to_redis is True, also publish to Redis for cross-process delivery.
        """
        message = self._prepare_for_json(message)
        
        # Send locally
        await self._broadcast_local(chat_id, message)
        
        # Publish to Redis for other processes
        if publish_to_redis:
            await self.publish_to_redis(chat_id, message)
    
    # ============================================================
    # Helper methods for common message types
    # ============================================================
    
    async def send_progress_update(self, chat_id: str, progress: int, status: str, step: str = None):
        """Send a progress update to all clients in a chat."""
        message = {
            'type': 'progress_update',
            'data': {
                'status': status,
                'progress': progress,
                'current_step': step or '',
                'is_processing': status not in ['completed', 'failed', 'answered'],
                'is_ready': status in ['completed', 'answered'],
                'is_failed': status == 'failed',
            }
        }
        await self.broadcast(chat_id, message)
    
    async def send_status(self, chat_id: str):
        """Send the current status to all clients in a chat."""
        from utils.chat_status_tracker import ChatStatusTracker
        
        status = ChatStatusTracker.get_progress_summary(chat_id)
        if status:
            status = self._prepare_for_json(status)
            await self.send_status_update(chat_id, status)
    
    async def send_status_update(self, chat_id: str, status_data: Dict[str, Any]):
        """Send a status update to all clients in a chat."""
        status_data = self._prepare_for_json(status_data)
        await self.broadcast(chat_id, {
            'type': 'status_update',
            'data': status_data
        })
    
    async def send_chat_message(self, chat_id: str, message_data: Dict[str, Any]):
        """Send a chat message to all clients in a chat."""
        await self.broadcast(chat_id, {
            'type': 'new_message',
            'message': message_data
        })
    
    async def send_message(self, chat_id: str, message_data: Dict[str, Any]):
        """Alias for send_chat_message."""
        await self.send_chat_message(chat_id, message_data)
    
    async def send_error(self, chat_id: str, error: str):
        """Send an error message to all clients in a chat."""
        await self.broadcast(chat_id, {
            'type': 'error',
            'data': {'error': error}
        })
    
    async def send_complete(self, chat_id: str):
        """Send a completion message to all clients in a chat."""
        await self.broadcast(chat_id, {
            'type': 'complete'
        })
    
    # ============================================================
    # Status and utility methods
    # ============================================================
    
    def get_connection_count(self, chat_id: str) -> int:
        """Get the number of active connections in a chat."""
        return len(self.active_connections.get(chat_id, set()))
    
    def get_users(self, chat_id: str) -> list:
        """Get the list of users in a chat."""
        return list(self.chat_users.get(chat_id, set()))
    
    async def close_all(self):
        """Close all connections and clean up."""
        for chat_id in list(self.active_connections.keys()):
            for connection in list(self.active_connections[chat_id]):
                await self.leave(connection)
        
        for task in self.pubsub_tasks.values():
            if not task.done():
                task.cancel()
        self.pubsub_tasks.clear()
        
        if self.pubsub:
            await self.pubsub.close()
        if self.redis_client:
            await self.redis_client.close()
        self._redis_initialized = False
        
        logger.info("🧹 WebSocket manager cleaned up")


# Global instance
chat_connection_manager = ChatConnectionManager()
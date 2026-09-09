# websocket_manager.py
import asyncio
import json
import logging
import threading
import traceback
from typing import Dict, Set, Optional, Any
from fastapi import WebSocket
import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError

logger = logging.getLogger(__name__)

# ============================================================
# Thread-safe bridge for publishing from sync threads
# ============================================================

_main_loop: asyncio.AbstractEventLoop = None
_main_loop_lock = threading.Lock()

def set_main_loop(loop: asyncio.AbstractEventLoop):
    """Store the main event loop at startup."""
    global _main_loop
    with _main_loop_lock:
        _main_loop = loop
        logger.info(f"✅ Main event loop captured: {loop}")

def get_main_loop() -> asyncio.AbstractEventLoop:
    """Get the main event loop."""
    with _main_loop_lock:
        return _main_loop

def publish_progress_sync(channel: str, message: dict):
    """
    Thread-safe bridge: publish from any sync thread 
    onto the main asyncio event loop.
    """
    loop = get_main_loop()
    if loop is None:
        logger.warning(f"⚠️ No main loop captured for channel: {channel}")
        # Try to use the current event loop as fallback
        try:
            loop = asyncio.get_running_loop()
            logger.info(f"🔄 Using current running loop as fallback: {loop}")
        except RuntimeError:
            logger.error(f"❌ No event loop available at all for channel: {channel}")
            return
    
    if loop.is_closed():
        logger.warning(f"⚠️ Main loop is closed for channel: {channel}")
        return

    # Create the coroutine
    coroutine = _publish_to_redis_channel(channel, message)
    
    # Run it in the main loop
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    
    try:
        # Wait for completion with a reasonable timeout
        result = future.result(timeout=5.0)
        if not result:
            logger.warning(f"⚠️ Publish returned False for {channel}")
        else:
            logger.debug(f"✅ Published to {channel}")
    except asyncio.TimeoutError:
        logger.error(f"❌ Timeout publishing to {channel} (took >5s)")
        future.cancel()
    except Exception as e:
        logger.error(f"❌ Progress publish failed for {channel}: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")

async def _publish_to_redis_channel(channel: str, message: dict) -> bool:
    """Internal async publish function."""
    try:
        # Use the global manager instance
        result = await chat_connection_manager.publish_to_redis_channel(channel, message)
        if result:
            logger.debug(f"📡 Published to Redis: {channel}")
        else:
            logger.warning(f"⚠️ Failed to publish to Redis: {channel}")
        return result
    except Exception as e:
        logger.error(f"❌ Redis publish error on {channel}: {e}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        return False

# ============================================================

def json_serializer(obj):
    """
    Custom JSON serializer for datetime objects and other non-serializable types.
    Used by json.dumps() when encountering objects it can't serialize.
    """
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    if hasattr(obj, '__dict__'):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


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
        self._redis_init_attempted = False
        self._init_task: Optional[asyncio.Task] = None
        self._redis_lock = asyncio.Lock()
        self._redis_url = None
        self._latest_status: Dict[str, tuple] = {}
    
    async def _init_redis(self):
        """Initialize Redis connection with retry logic."""
        if self._redis_initialized:
            return
        
        if self._redis_init_attempted:
            # Don't retry too often if we already failed
            return
        
        async with self._redis_lock:
            if self._redis_initialized:
                return
            
            self._redis_init_attempted = True
            
            try:
                from redis_config import REDIS_URL
                self._redis_url = REDIS_URL
                logger.info(f"🔄 Connecting to Redis at {REDIS_URL}")
                
                # Create client with shorter timeouts
                self.redis_client = await redis.from_url(
                    REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=3,
                    socket_timeout=3,
                    retry_on_timeout=True,
                    max_connections=5,
                )
                
                # Test connection with short timeout
                await asyncio.wait_for(self.redis_client.ping(), timeout=3.0)
                
                self.pubsub = self.redis_client.pubsub()
                self._redis_initialized = True
                logger.info(f"✅ Redis connection initialized for WebSocket manager")
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ Redis connection timeout - continuing without Redis")
                self._redis_initialized = False
                # Try again later
                self._redis_init_attempted = False
            except RedisConnectionError as e:
                logger.warning(f"⚠️ Redis connection error: {e} - continuing without Redis")
                self._redis_initialized = False
                # Try again later
                self._redis_init_attempted = False
            except Exception as e:
                logger.error(f"❌ Failed to initialize Redis: {e}")
                logger.error(f"   Traceback: {traceback.format_exc()}")
                self._redis_initialized = False
                # Try again later
                self._redis_init_attempted = False
    
    async def _ensure_redis(self):
        """Ensure Redis is initialized, retry if needed."""
        if not self._redis_initialized:
            await self._init_redis()
        return self._redis_initialized
    
    def _get_channel(self, chat_id: str) -> str:
        """Get Redis channel name for a chat."""
        return f"{self.CHANNEL_PREFIX}{chat_id}"
    
    async def publish_to_redis_channel(self, channel: str, message: Dict[str, Any]) -> bool:
        """
        Publish a message to a specific Redis channel.
        Returns True on success, False on failure.
        """
        # If Redis isn't initialized, try to initialize it
        if not self._redis_initialized:
            await self._ensure_redis()
            
            # If still not initialized, just log and return False
            if not self._redis_initialized:
                logger.debug(f"⏭️ Redis not available, skipping publish to {channel}")
                return False
        
        try:
            if not self.redis_client:
                logger.debug(f"⏭️ Redis client not available, skipping publish to {channel}")
                return False
            
            # Prepare message
            message = self._prepare_for_json(message)
            message_json = json.dumps(message, default=json_serializer)
            
            # Publish with short timeout
            result = await asyncio.wait_for(
                self.redis_client.publish(channel, message_json),
                timeout=2.0
            )
            logger.debug(f"📡 Published to {channel}, subscribers: {result}")
            return True
            
        except asyncio.TimeoutError:
            logger.debug(f"⏱️ Redis publish timeout to {channel} - skipping")
            return False
        except RedisConnectionError as e:
            logger.debug(f"🔌 Redis connection error on {channel}: {e}")
            # Reset state so we retry connection
            self._redis_initialized = False
            return False
        except RedisTimeoutError as e:
            logger.debug(f"⏱️ Redis timeout error on {channel}: {e}")
            return False
        except asyncio.CancelledError:
            logger.debug(f"⏭️ Publish cancelled for {channel}")
            return False
        except Exception as e:
            logger.debug(f"❌ Failed to publish to Redis {channel}: {e}")
            return False
    
    async def join(self, websocket: WebSocket, chat_id: str, user_id: Optional[str] = None):
        """Accept a WebSocket connection and add it to the chat room."""
        await websocket.accept()
        
        # Try to initialize Redis (non-blocking)
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
        
        # Start Redis listener for this chat if Redis is available
        if self._redis_initialized and chat_id not in self.pubsub_tasks:
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
                self._latest_status.pop(chat_id, None)
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

        # Redis can deliver a queued worker update after a newer update from
        # another worker. Never let an older stage overwrite a newer one in
        # the live UI; reload already gets the latest database state.
        if message.get('type') in {'progress_update', 'status_update'}:
            data = message.get('data') or {}
            status = data.get('status')
            progress = data.get('progress') or 0
            stage_order = {
                'pending': 0,
                'crawling': 1,
                'processing': 2,
                'chunking': 3,
                'embedding': 4,
                'completed': 5,
                'answered': 5,
                'failed': 5,
            }
            current = self._latest_status.get(chat_id)
            incoming = (stage_order.get(status, -1), progress)
            current_status = current[2] if current else None
            if current_status in {'completed', 'answered'} and status not in {'completed', 'answered'}:
                logger.debug(
                    "Ignoring non-terminal websocket status for completed chat %s: %s/%s",
                    chat_id,
                    status,
                    progress,
                )
                return
            if status != 'failed' and current and incoming < current[:2]:
                logger.debug(
                    "Ignoring stale websocket status for %s: %s/%s after %s",
                    chat_id,
                    status,
                    progress,
                    current,
                )
                return
            self._latest_status[chat_id] = (*incoming, status)
        
        # Ensure message is JSON serializable
        message = self._prepare_for_json(message)
        message_json = json.dumps(message, default=json_serializer)
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
        channel = self._get_channel(chat_id)
        return await self.publish_to_redis_channel(channel, message)
    
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
    
    async def send_progress_update(self, chat_id: str, progress: int, status: str, step: str = None):
        """Send a progress update via Redis Pub/Sub and local broadcast."""
        from utils.chat_status_tracker import ChatStatusTracker
        
        logger.info(f"📡 Sending progress update for chat {chat_id}: {status} ({progress}%)")
        
        # Get full status data
        status_data = ChatStatusTracker.get_progress_summary(chat_id)
        
        if not status_data:
            status_data = {
                'chat_id': chat_id,
                'exists': True,
                'status': status,
                'progress': progress,
                'current_step': step or '',
                'friendly_message': f'{status}...',
                'is_processing': True,
                'is_ready': False,
                'is_failed': False,
                'has_error': False,
                'error_message': None,
                'document_id': None,
                'started_at': None,
                'completed_at': None
            }
        
        message = {
            'type': 'progress_update',
            'chat_id': chat_id,
            'data': status_data
        }
        
        # Publish to Redis
        await self.publish_to_redis(chat_id, message)
        
        # Also broadcast locally (if in same process)
        await self._broadcast_local(chat_id, message)
    
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
            try:
                await self.pubsub.close()
            except Exception:
                pass
        if self.redis_client:
            try:
                await self.redis_client.close()
            except Exception:
                pass
        self._redis_initialized = False
        
        logger.info("🧹 WebSocket manager cleaned up")


# Global instance
chat_connection_manager = ChatConnectionManager()
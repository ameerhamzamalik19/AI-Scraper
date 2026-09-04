# websocket_manager.py
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Set, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from redis_pubsub import WebSocketPubSub
from redis_client import redis_client

logger = logging.getLogger(__name__)


def json_serializer(obj):
    """Function version of the custom serializer."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, '__dict__'):
        return str(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


class ChatConnectionManager:
    """
    Manages WebSocket connections and broadcasts status updates.
    Uses Redis Pub/Sub for cross-process communication.
    """
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.connection_metadata: Dict[WebSocket, Dict[str, Any]] = {}
        self.chat_users: Dict[str, Set[str]] = {}
        self.pubsub_subscribers: Dict[str, Any] = {}
        self.pubsub_tasks: Dict[str, asyncio.Task] = {}
    
    async def join(self, chat_id: str, websocket: WebSocket, user_id: str = None):
        """Join a WebSocket connection to a chat room."""
        await websocket.accept()
        
        if chat_id not in self.active_connections:
            self.active_connections[chat_id] = set()
        self.active_connections[chat_id].add(websocket)
        
        if user_id:
            if chat_id not in self.chat_users:
                self.chat_users[chat_id] = set()
            self.chat_users[chat_id].add(user_id)
        
        self.connection_metadata[websocket] = {
            'chat_id': chat_id,
            'user_id': user_id,
            'connected_at': asyncio.get_event_loop().time()
        }
        
        # ✅ Start Redis Pub/Sub listener for this chat
        if chat_id not in self.pubsub_tasks:
            await self._start_pubsub_listener(chat_id)
        
        logger.info(f"🔌 User {user_id or 'anonymous'} joined chat {chat_id} (Total: {len(self.active_connections[chat_id])} connections)")
    
    def leave(self, chat_id: str, websocket: WebSocket):
        """Remove a WebSocket connection from a chat room."""
        if chat_id in self.active_connections:
            self.active_connections[chat_id].discard(websocket)
            if not self.active_connections[chat_id]:
                del self.active_connections[chat_id]
                # Stop pubsub listener when no connections remain
                if chat_id in self.pubsub_tasks:
                    self.pubsub_tasks[chat_id].cancel()
                    del self.pubsub_tasks[chat_id]
                    if chat_id in self.pubsub_subscribers:
                        del self.pubsub_subscribers[chat_id]
        
        metadata = self.connection_metadata.get(websocket, {})
        user_id = metadata.get('user_id')
        if user_id and chat_id in self.chat_users:
            self.chat_users[chat_id].discard(user_id)
            if not self.chat_users[chat_id]:
                del self.chat_users[chat_id]
        
        if websocket in self.connection_metadata:
            del self.connection_metadata[websocket]
        
        logger.info(f"🔌 User {user_id or 'anonymous'} left chat {chat_id}")
    
    async def _start_pubsub_listener(self, chat_id: str):
        """Start a Redis Pub/Sub listener for a chat."""
        try:
            channel = WebSocketPubSub.get_channel(chat_id)
            pubsub = redis_client.pubsub()
            pubsub.subscribe(channel)
            self.pubsub_subscribers[chat_id] = pubsub
            
            async def listener():
                logger.info(f"📡 Started Pub/Sub listener for chat {chat_id}")
                try:
                    while True:
                        message = pubsub.get_message(timeout=1.0)
                        if message and message.get('type') == 'message':
                            try:
                                data = json.loads(message['data'])
                                await self.broadcast(chat_id, data)
                                logger.debug(f"📡 Broadcasted Pub/Sub message to chat {chat_id}")
                            except json.JSONDecodeError as e:
                                logger.warning(f"⚠️ Failed to parse Pub/Sub message: {e}")
                            except Exception as e:
                                logger.error(f"❌ Error broadcasting Pub/Sub message: {e}")
                        await asyncio.sleep(0.01)
                except asyncio.CancelledError:
                    logger.info(f"📡 Pub/Sub listener stopped for chat {chat_id}")
                except Exception as e:
                    logger.error(f"❌ Pub/Sub listener error for chat {chat_id}: {e}")
                    # Restart listener on error
                    if chat_id in self.pubsub_tasks:
                        await self._start_pubsub_listener(chat_id)
            
            # Run listener in background
            task = asyncio.create_task(listener())
            self.pubsub_tasks[chat_id] = task
            
        except Exception as e:
            logger.error(f"❌ Failed to start Pub/Sub listener for chat {chat_id}: {e}")
    
    async def broadcast(self, chat_id: str, message: Dict[str, Any]):
        """Broadcast a message to all connections in a chat room."""
        if chat_id not in self.active_connections:
            return
        
        if not self.active_connections[chat_id]:
            return
        
        try:
            message_json = json.dumps(message, default=json_serializer)
        except TypeError as e:
            logger.error(f"Failed to serialize message: {e}")
            return
        
        disconnected = set()
        
        for websocket in self.active_connections[chat_id]:
            try:
                await websocket.send_text(message_json)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                disconnected.add(websocket)
        
        for websocket in disconnected:
            self.leave(chat_id, websocket)
    
    async def send_status(self, chat_id: str):
        """Send current status to all connections in a chat."""
        from utils.chat_status_tracker import ChatStatusTracker
        
        status = ChatStatusTracker.get_progress_summary(chat_id)
        
        if not status:
            return
        
        message = {
            'type': 'status_update',
            'chat_id': chat_id,
            'data': status
        }
        
        # ✅ Publish via Redis for cross-process communication
        WebSocketPubSub.publish(chat_id, message)
        
        # Also broadcast locally
        await self.broadcast(chat_id, message)
    
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
        
        # ✅ Publish via Redis for cross-process communication
        WebSocketPubSub.publish(chat_id, message)
        
        # Also broadcast locally
        await self.broadcast(chat_id, message)
    
    async def send_message(self, chat_id: str, message_data: Dict[str, Any]):
        """Send a chat message to all connections in a chat."""
        message = {
            'type': 'new_message',
            'chat_id': chat_id,
            'data': message_data
        }
        
        # ✅ Publish via Redis
        WebSocketPubSub.publish(chat_id, message)
        
        await self.broadcast(chat_id, message)
    
    async def send_error(self, chat_id: str, error: str):
        """Send an error message to all connections in a chat."""
        message = {
            'type': 'error',
            'chat_id': chat_id,
            'data': {
                'error': error,
                'timestamp': asyncio.get_event_loop().time()
            }
        }
        
        WebSocketPubSub.publish(chat_id, message)
        await self.broadcast(chat_id, message)
    
    async def send_complete(self, chat_id: str, document_id: str = None):
        """Send a completion message to all connections in a chat."""
        message = {
            'type': 'complete',
            'chat_id': chat_id,
            'data': {
                'document_id': document_id,
                'message': 'Processing complete!',
                'timestamp': asyncio.get_event_loop().time()
            }
        }
        
        WebSocketPubSub.publish(chat_id, message)
        await self.broadcast(chat_id, message)
    
    def get_chat_users(self, chat_id: str) -> Set[str]:
        """Get all users currently connected to a chat."""
        return self.chat_users.get(chat_id, set())


# Global instance
chat_connection_manager = ChatConnectionManager()
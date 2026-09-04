# redis_pubsub.py
import json
import logging
from typing import Dict, Any, Optional
from redis_client import redis_client

logger = logging.getLogger(__name__)

class WebSocketPubSub:
    """Redis Pub/Sub for cross-process WebSocket broadcasting."""
    
    CHANNEL_PREFIX = "ws:chat:"
    
    @staticmethod
    def get_channel(chat_id: str) -> str:
        """Get the Redis channel name for a chat."""
        return f"{WebSocketPubSub.CHANNEL_PREFIX}{chat_id}"
    
    @staticmethod
    def publish(chat_id: str, message: Dict[str, Any]) -> bool:
        """Publish a message to a chat's Redis channel."""
        try:
            channel = WebSocketPubSub.get_channel(chat_id)
            message_json = json.dumps(message)
            redis_client.publish(channel, message_json)
            logger.debug(f"📡 Published to {channel}: {message.get('type', 'unknown')}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to publish message: {e}")
            return False
    
    @staticmethod
    def subscribe(chat_id: str):
        """
        Subscribe to a chat's Redis channel.
        Returns the PubSub object that can be used to listen for messages.
        """
        try:
            channel = WebSocketPubSub.get_channel(chat_id)
            pubsub = redis_client.pubsub()
            pubsub.subscribe(channel)
            logger.debug(f"📡 Subscribed to {channel}")
            return pubsub
        except Exception as e:
            logger.error(f"❌ Failed to subscribe to {chat_id}: {e}")
            return None
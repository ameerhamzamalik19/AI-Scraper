from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional
import json
import logging
from websocket_manager import chat_connection_manager
from utils.chat_status_tracker import ChatStatusTracker

router = APIRouter()
logger = logging.getLogger(__name__)


@router.websocket("/ws/{chat_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    chat_id: str,
    user_id: Optional[str] = Query(None)
):
    """WebSocket endpoint for real-time chat updates."""
    
    # TODO: Add authentication/authorization here
    
    try:
        await chat_connection_manager.join(websocket, chat_id, user_id)
        
        # Send initial status
        status = ChatStatusTracker.get_progress_summary(chat_id)
        if status:
            await chat_connection_manager.send_status_update(chat_id, status)
        
        # Listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                if not data:
                    continue
                
                try:
                    message = json.loads(data)
                    message_type = message.get('type')
                    
                    if message_type == 'ping':
                        await websocket.send_text(json.dumps({'type': 'pong'}))
                    
                    elif message_type == 'get_status':
                        status = ChatStatusTracker.get_progress_summary(chat_id)
                        if status:
                            await chat_connection_manager.send_status_update(chat_id, status)
                    
                    elif message_type == 'get_users':
                        await websocket.send_text(json.dumps({
                            'type': 'users_list',
                            'users': chat_connection_manager.get_users(chat_id)
                        }))
                    
                    elif message_type == 'typing':
                        # Broadcast typing indicator to others
                        typing_user_id = message.get('user_id')
                        is_typing = message.get('is_typing', False)
                        if typing_user_id:
                            await chat_connection_manager.broadcast(chat_id, {
                                'type': 'typing',
                                'user_id': typing_user_id,
                                'is_typing': is_typing
                            }, publish_to_redis=True)
                    
                    else:
                        logger.debug(f"Unknown message type: {message_type}")
                
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON received: {data[:100]}")
                
            except WebSocketDisconnect:
                break
        
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error for chat {chat_id}: {e}")
    finally:
        await chat_connection_manager.leave(websocket)
# utils/chat_status_tracker.py
import logging
import traceback
from typing import Dict, Any, Optional
from database_sync import execute_update, execute_one
from utils.helpers import get_current_datetime

logger = logging.getLogger(__name__)

class ChatStatusTracker:
    """Minimal status tracking for chats with WebSocket broadcasting."""
    
    # Status constants
    STATUS_PENDING = 'pending'
    STATUS_CRAWLING = 'crawling'
    STATUS_PROCESSING = 'processing'
    STATUS_CHUNKING = 'chunking'
    STATUS_EMBEDDING = 'embedding'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_ANSWERED = 'answered'
    
    @staticmethod
    def initialize(chat_id: str):
        """Initialize status tracking for a chat."""
        try:
            now = get_current_datetime().isoformat()
            execute_update(
                """UPDATE chats 
                   SET status = %s, 
                       progress = 0,
                       started_at = %s,
                       updated_at = %s,
                       error_message = NULL
                   WHERE id = %s""",
                (
                    ChatStatusTracker.STATUS_PENDING,
                    now,
                    now,
                    chat_id
                )
            )
            logger.info(f"📊 Initialized status for chat {chat_id}")
            
            # 🆕 Broadcast via WebSocket
            ChatStatusTracker._broadcast_update(chat_id, 0, ChatStatusTracker.STATUS_PENDING, "Initializing...")
            
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize status for chat {chat_id}: {e}")
            return False
    
    @staticmethod
    def update(
        chat_id: str,
        status: str,
        progress: int = None,
        current_step: str = None,
        document_id: str = None,
        error: str = None
    ):
        """Update chat status and broadcast via WebSocket."""
        try:
            now = get_current_datetime().isoformat()
            
            updates = {
                'status': status,
                'updated_at': now,
            }
            
            if progress is not None:
                if progress < 0:
                    progress = 0
                elif progress > 100:
                    progress = 100
                updates['progress'] = progress
            
            if current_step:
                if len(current_step) > 200:
                    current_step = current_step[:197] + "..."
                updates['current_step'] = current_step
            
            if document_id:
                updates['document_id'] = document_id
            
            if error:
                if len(error) > 500:
                    error = error[:497] + "..."
                updates['error_message'] = error
            
            if status in [ChatStatusTracker.STATUS_COMPLETED, ChatStatusTracker.STATUS_ANSWERED]:
                updates['completed_at'] = now
            
            # Build SET clause
            set_clause = ', '.join([f"{key} = %s" for key in updates.keys()])
            values = list(updates.values())
            values.append(chat_id)
            
            execute_update(
                f"UPDATE chats SET {set_clause} WHERE id = %s",
                tuple(values)
            )
            logger.debug(f"📊 Updated chat {chat_id}: {status} ({progress}%)")
            
            # 🆕 Broadcast via WebSocket
            if progress is not None:
                ChatStatusTracker._broadcast_update(chat_id, progress, status, current_step)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to update chat {chat_id}: {e}")
            try:
                execute_update(
                    "UPDATE chats SET error_message = %s, updated_at = %s WHERE id = %s",
                    (f"Status update failed: {str(e)[:200]}", get_current_datetime().isoformat(), chat_id)
                )
            except:
                pass
            return False
    
    @staticmethod
    def _broadcast_update(chat_id: str, progress: int, status: str, step: str = None):
        """Broadcast status update via WebSocket."""
        try:
            from websocket_manager import chat_connection_manager
            import asyncio
            
            # Create event loop if needed
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            # Schedule broadcast
            if loop.is_running():
                asyncio.create_task(chat_connection_manager.send_progress_update(
                    chat_id,
                    progress,
                    status,
                    step
                ))
            else:
                loop.run_until_complete(chat_connection_manager.send_progress_update(
                    chat_id,
                    progress,
                    status,
                    step
                ))
        except Exception as e:
            logger.warning(f"Failed to broadcast WebSocket update: {e}")
    
    @staticmethod
    def get(chat_id: str) -> Optional[Dict[str, Any]]:
        """Get chat status."""
        try:
            return execute_one(
                """SELECT 
                    status, progress, current_step, error_message,
                    document_id, started_at, completed_at
                   FROM chats WHERE id = %s""",
                (chat_id,)
            )
        except Exception as e:
            logger.error(f"❌ Failed to get status for chat {chat_id}: {e}")
            return None
    
    @staticmethod
    def get_progress_summary(chat_id: str) -> Dict[str, Any]:
        """Get a detailed progress summary."""
        try:
            status = ChatStatusTracker.get(chat_id)
            if not status:
                return {
                    'chat_id': chat_id,
                    'exists': False,
                    'status': 'not_found',
                    'progress': 0,
                    'message': 'Chat not found'
                }
            
            status_messages = {
                ChatStatusTracker.STATUS_PENDING: "Waiting to start...",
                ChatStatusTracker.STATUS_CRAWLING: "Fetching webpage...",
                ChatStatusTracker.STATUS_PROCESSING: "Extracting content...",
                ChatStatusTracker.STATUS_CHUNKING: "Creating chunks...",
                ChatStatusTracker.STATUS_EMBEDDING: "Generating embeddings...",
                ChatStatusTracker.STATUS_COMPLETED: "Ready!",
                ChatStatusTracker.STATUS_ANSWERED: "Answered!",
                ChatStatusTracker.STATUS_FAILED: "Failed"
            }
            
            return {
                'chat_id': chat_id,
                'exists': True,
                'status': status.get('status', 'unknown'),
                'progress': status.get('progress', 0),
                'current_step': status.get('current_step', ''),
                'friendly_message': status_messages.get(status.get('status'), 'Processing...'),
                'document_id': status.get('document_id'),
                'started_at': status.get('started_at'),
                'completed_at': status.get('completed_at'),
                'error_message': status.get('error_message'),
                'is_ready': status.get('status') in [
                    ChatStatusTracker.STATUS_COMPLETED,
                    ChatStatusTracker.STATUS_ANSWERED
                ],
                'is_processing': status.get('status') in [
                    ChatStatusTracker.STATUS_PENDING,
                    ChatStatusTracker.STATUS_CRAWLING,
                    ChatStatusTracker.STATUS_PROCESSING,
                    ChatStatusTracker.STATUS_CHUNKING,
                    ChatStatusTracker.STATUS_EMBEDDING
                ],
                'is_failed': status.get('status') == ChatStatusTracker.STATUS_FAILED,
                'has_error': bool(status.get('error_message'))
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get progress summary for chat {chat_id}: {e}")
            return {
                'chat_id': chat_id,
                'exists': False,
                'status': 'error',
                'progress': 0,
                'message': f'Error getting status: {str(e)}'
            }
    
    @staticmethod
    def is_ready(chat_id: str) -> bool:
        """Check if chat is ready for answering."""
        try:
            status = ChatStatusTracker.get(chat_id)
            if not status:
                return False
            return status.get('status') in [
                ChatStatusTracker.STATUS_COMPLETED,
                ChatStatusTracker.STATUS_ANSWERED
            ]
        except Exception as e:
            logger.error(f"❌ Failed to check ready status for chat {chat_id}: {e}")
            return False
    
    @staticmethod
    def is_processing(chat_id: str) -> bool:
        """Check if chat is currently processing."""
        try:
            status = ChatStatusTracker.get(chat_id)
            if not status:
                return False
            return status.get('status') in [
                ChatStatusTracker.STATUS_PENDING,
                ChatStatusTracker.STATUS_CRAWLING,
                ChatStatusTracker.STATUS_PROCESSING,
                ChatStatusTracker.STATUS_CHUNKING,
                ChatStatusTracker.STATUS_EMBEDDING
            ]
        except Exception as e:
            logger.error(f"❌ Failed to check processing status for chat {chat_id}: {e}")
            return False
    
    @staticmethod
    def mark_failed(chat_id: str, error: str):
        """Mark chat as failed with comprehensive error logging."""
        try:
            logger.error(f"❌ Chat {chat_id} failed: {error}")
            
            if len(error) > 500:
                db_error = error[:497] + "..."
            else:
                db_error = error
            
            ChatStatusTracker.update(
                chat_id,
                status=ChatStatusTracker.STATUS_FAILED,
                error=db_error,
                progress=0
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to mark chat {chat_id} as failed: {e}")
            return False
    
    @staticmethod
    def mark_answered(chat_id: str):
        """Mark chat as answered."""
        return ChatStatusTracker.update(
            chat_id,
            status=ChatStatusTracker.STATUS_ANSWERED,
            progress=100,
            current_step="Answer generated!"
        )
# utils/progress_tracker.py
"""
Pipeline-centric progress tracking for URL ingestion.
Progress is monotonic and tied to pipeline stages, not individual pages.
"""

from typing import Dict, Any, Optional
import logging
from utils.chat_status_tracker import ChatStatusTracker
from database_sync import execute_update, execute_one
from utils.helpers import get_current_datetime
import json

logger = logging.getLogger(__name__)

class PipelineProgressTracker:
    """
    Tracks progress through the ingestion pipeline with monotonic values.
    Stages: pending → crawling → processing → chunking → embedding → completed
    """
    
    # Pipeline stage definitions with overall progress ranges
    STAGES = {
        'pending': {'min': 0, 'max': 0, 'weight': 0},
        'crawling': {'min': 5, 'max': 30, 'weight': 25},
        'processing': {'min': 30, 'max': 60, 'weight': 30},
        'chunking': {'min': 60, 'max': 80, 'weight': 20},
        'embedding': {'min': 80, 'max': 100, 'weight': 20},
        'completed': {'min': 100, 'max': 100, 'weight': 0},
        'failed': {'min': 0, 'max': 0, 'weight': 0}
    }
    
    # Order of stages for transition validation
    STAGE_ORDER = ['pending', 'crawling', 'processing', 'chunking', 'embedding', 'completed']
    
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self._current_stage = 'pending'
        self._current_progress = 0
        self._last_progress = 0
        self._stage_progress = {}  # Track progress within each stage
    
    def get_stage_range(self, stage: str) -> tuple:
        """Get min/max progress for a stage."""
        stage_info = self.STAGES.get(stage, {'min': 0, 'max': 0})
        return stage_info['min'], stage_info['max']
    
    def get_stage_index(self, stage: str) -> int:
        """Get the index of a stage in the pipeline order."""
        try:
            return self.STAGE_ORDER.index(stage)
        except ValueError:
            return -1
    
    def calculate_progress(self, stage: str, stage_progress: int = 0) -> int:
        """
        Calculate overall progress based on stage and progress within stage.
        stage_progress: 0-100 within the current stage.
        Returns: 0-100 overall progress.
        """
        if stage == 'failed':
            return self._last_progress  # Keep last known progress on failure
        
        if stage == 'completed':
            return 100
        
        min_val, max_val = self.get_stage_range(stage)
        range_size = max_val - min_val
        
        if range_size <= 0:
            return min_val
        
        # Calculate progress within the stage range
        clamped_stage_progress = max(0, min(100, stage_progress))
        progress = min_val + int((clamped_stage_progress / 100) * range_size)
        
        # Check if this is a valid transition (not going backward in stages)
        current_index = self.get_stage_index(self._current_stage)
        new_index = self.get_stage_index(stage)
        
        if new_index < current_index and new_index != -1:
            # Trying to go backward in stages - ignore
            logger.warning(f"Cannot move backward from {self._current_stage} to {stage}, ignoring")
            return self._last_progress
        
        # Ensure monotonic (never decrease within same stage)
        if stage == self._current_stage and progress < self._last_progress:
            logger.debug(f"Progress would go backward ({self._last_progress} → {progress}), keeping {self._last_progress}")
            progress = self._last_progress
        
        # Clamp to 0-100
        progress = max(0, min(100, progress))
        
        self._last_progress = progress
        self._current_progress = progress
        self._current_stage = stage
        
        return progress
    
    def update_stage(self, stage: str, stage_progress: int = 0, step: str = None) -> int:
        """
        Update to a new pipeline stage with optional progress within that stage.
        Returns: the new overall progress.
        """
        # Ensure stage exists
        if stage not in self.STAGES:
            logger.warning(f"Unknown stage: {stage}, defaulting to pending")
            stage = 'pending'
        
        # Calculate new progress
        new_progress = self.calculate_progress(stage, stage_progress)
        
        # Use the database-protected update
        success = self._update_chat_status(stage, new_progress, step)
        
        if success:
            logger.info(f"📊 Pipeline progress: {stage} ({new_progress}%) - {step}")
        else:
            logger.warning(f"⚠️ Failed to update chat status for {self.chat_id}")
        
        return new_progress
    
    def _update_chat_status(self, status: str, progress: int, step: str = None) -> bool:
        """
        Update chat status with monotonic protection at the database level.
        This is the source of truth for status updates.
        """
        try:
            now = get_current_datetime().isoformat()
            
            # Get current status from database to check monotonicity
            current = execute_one(
                "SELECT status, progress FROM chats WHERE id = %s",
                (self.chat_id,)
            )
            
            if current:
                current_status = current.get('status')
                current_progress = current.get('progress') or 0
                
                # Terminal state check - don't allow changes after completion
                                # Terminal state check - don't allow changes after completion
                if current_status in ['completed', 'answered']:
                    logger.warning(f"Chat {self.chat_id} already in terminal state {current_status}, ignoring update")
                    return False

                # Don't allow failed to overwrite completed/answered
                if status == 'failed' and current_status in ['completed', 'answered']:
                    logger.warning(f"Chat {self.chat_id} already {current_status}, not marking as failed")
                    return False

                # DB-level stage-order guard: reject backward stage transitions
                # (the in-memory guard can't help when a fresh tracker is created per worker)
                current_db_index = self.get_stage_index(current_status) if current_status else -1
                new_index = self.get_stage_index(status)
                if new_index != -1 and current_db_index != -1 and new_index < current_db_index:
                    logger.warning(
                        f"Chat {self.chat_id}: rejecting backward stage transition "
                        f"{current_status} → {status} at DB level"
                    )
                    return False

                # Progress monotonic check (same stage only)
                if progress < current_progress and status == current_status:
                    logger.debug(f"Progress would go backward ({current_progress} → {progress}), keeping {current_progress}")
                    progress = current_progress
            
            # Build update
            updates = {
                'status': status,
                'progress': progress,
                'updated_at': now,
            }
            
            if step:
                updates['current_step'] = step
            
            if status in ['completed', 'answered']:
                updates['completed_at'] = now
            
            set_clause = ', '.join([f"{key} = %s" for key in updates.keys()])
            values = list(updates.values())
            values.append(self.chat_id)
            
            execute_update(
                f"UPDATE chats SET {set_clause} WHERE id = %s",
                tuple(values)
            )
            
            logger.debug(f"📊 Updated chat {self.chat_id}: {status} ({progress}%)")

            try:
                ChatStatusTracker._broadcast_update(self.chat_id, progress, status, step)
            except Exception as _be:
                logger.warning(f"WebSocket broadcast failed (non-fatal): {_be}")

            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to update chat {self.chat_id}: {e}")
            return False
    
    def mark_crawling(self, page_count: int = 0, total_pages: int = 0) -> int:
        """Update crawling progress based on pages found."""
        if total_pages > 0:
            stage_progress = min(100, int((page_count / total_pages) * 100))
        else:
            stage_progress = 0
        
        step = f"Crawling {page_count}/{total_pages} pages..." if total_pages > 0 else "Crawling..."
        return self.update_stage('crawling', stage_progress, step)
    
    def mark_processing(self, progress: int = 0, step: str = None) -> int:
        """Update processing progress."""
        return self.update_stage('processing', progress, step or "Extracting content...")
    
    def mark_chunking(self, chunk_count: int = 0, total_chunks: int = 0) -> int:
        """Update chunking progress."""
        if total_chunks > 0:
            stage_progress = min(100, int((chunk_count / total_chunks) * 100))
        else:
            stage_progress = 0
        
        step = f"Chunking {chunk_count}/{total_chunks} chunks..." if total_chunks > 0 else "Creating chunks..."
        return self.update_stage('chunking', stage_progress, step)
    
    def mark_embedding(self, embedded: int = 0, total: int = 0) -> int:
        """Update embedding progress."""
        if total > 0:
            stage_progress = min(100, int((embedded / total) * 100))
        else:
            stage_progress = 0
        
        step = f"Embedding {embedded}/{total} chunks..." if total > 0 else "Generating embeddings..."
        return self.update_stage('embedding', stage_progress, step)
    
    def mark_completed(self, message: str = "Processing complete!") -> int:
        """Mark pipeline as completed."""
        return self.update_stage('completed', 100, message)
    
    def mark_failed(self, error: str) -> int:
        """
        Mark pipeline as failed while preserving last progress.
        This gives better visibility into where the failure occurred.
        """
        # Keep last progress instead of resetting to 0
        # progress = self._last_progress if self._last_progress > 0 else 0
        
        if self._last_progress > 0:
            progress = self._last_progress
        else:
            try:
                row = execute_one(
                    "SELECT progress FROM chats WHERE id = %s", (self.chat_id,)
                )
                progress = (row.get('progress') or 0) if row else 0
            except Exception:
                progress = 0
        try:
            now = get_current_datetime().isoformat()
            
            # Check if chat is already in a terminal state
            current = execute_one(
                "SELECT status FROM chats WHERE id = %s",
                (self.chat_id,)
            )
            
            if current:
                current_status = current.get('status')
                if current_status in ['completed', 'answered']:
                    logger.warning(f"Chat {self.chat_id} already {current_status}, not marking as failed")
                    return progress
            
            # Update to failed state
            execute_update(
                """UPDATE chats 
                   SET status = %s, 
                       error_message = %s,
                       progress = %s,
                       updated_at = %s
                   WHERE id = %s""",
                ('failed', error[:500], progress, now, self.chat_id)
            )
            
            self._current_stage = 'failed'
            self._current_progress = progress
            
            logger.error(f"❌ Chat {self.chat_id} failed: {error} (progress at failure: {progress})")
            return progress
            
        except Exception as e:
            logger.error(f"❌ Failed to mark chat {self.chat_id} as failed: {e}")
            # Fallback to ChatStatusTracker
            ChatStatusTracker.mark_failed(self.chat_id, error)
            self._current_stage = 'failed'
            self._current_progress = 0
            return 0


# Global progress tracker registry (in-memory, but status is in DB)
_progress_trackers: Dict[str, PipelineProgressTracker] = {}

def get_progress_tracker(chat_id: str) -> PipelineProgressTracker:
    """Get or create a progress tracker for a chat."""
    if chat_id not in _progress_trackers:
        _progress_trackers[chat_id] = PipelineProgressTracker(chat_id)
    return _progress_trackers[chat_id]
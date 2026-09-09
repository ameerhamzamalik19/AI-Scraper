# api/routes/process.py
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from models import LinkRequest, ProcessLinkResponse
from services.user_service import UserService
from services.project_service import ProjectService
from services.chat_service import ChatService
from services.message_service import MessageService
from services.page_service import PageService
from utils.detectors import InputDetector
from utils.validators import is_valid_url_for_scraping
from utils.helpers import get_iso_timestamp, generate_uuid
from exceptions import NotFoundError
from redis_client import redis_client
from api.routes.retrieval_pipeline import answer_user_question
from websocket_manager import chat_connection_manager
from utils.chat_status_tracker import ChatStatusTracker
from utils.progress_tracker import get_progress_tracker
import re

router = APIRouter(prefix="/api", tags=["process"])

CONVERSATIONAL_PATTERNS = re.compile(
    r"^(hi|hello|hey|howdy|how are you|how's it going|what's up|wassup|"
    r"thanks|thank you|cheers|good (morning|afternoon|evening|night)|"
    r"who are you|what can you do|what do you do)\b",
    re.IGNORECASE
)

CONVERSATIONAL_RESPONSES = {
    "how are you": "I'm doing well, thanks for asking! What would you like to know about the content I've indexed?",
    "how's it going": "Going great! Ask me anything about the website you've shared.",
    "what's up": "Not much! Ready to answer questions about your content. What do you want to know?",
    "who are you": "I'm an AI assistant that answers questions based on website content you provide. Share a URL and I'll index it for you!",
    "what can you do": "I can crawl websites and answer questions about their content. Share a URL to get started!",
    "what do you do": "I can crawl websites and answer questions about their content. Share a URL to get started!",
}



@router.post("/process-link", response_model=ProcessLinkResponse)
async def process_link(request: LinkRequest):
    """Process a link or message from the user"""
    try:
        content = request.content
        if not content or not content.strip():
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
        content = content.strip()
        
        # Get or create user
        user_id = request.user_id
        if not user_id:
            user_id = await UserService.get_or_create_user()
            print(f"Created/retrieved user: {user_id}")
        
        # Get or create project (for future scraping)
        project_id = request.project_id if hasattr(request, 'project_id') else None
        if not project_id:
            project = await ProjectService.get_or_create_default_project(user_id)
            project_id = project['id']
            print(f"Created/retrieved project: {project_id}")
        
        # Detect input type
        detection = InputDetector.detect_input_type(content)
        print(f"Detection: {detection}")
        
        # Validate URL if detected
        if detection['has_url']:
            url = detection['urls'][0]
            is_valid, error_msg = is_valid_url_for_scraping(url)
            if not is_valid:
                raise HTTPException(
                    status_code=400,
                    detail=error_msg
                )
        
        # Get or create chat
        chat_id = request.chat_id
        is_new_chat = False
        
        if not chat_id:
            # Create new chat
            title = content[:40] if not detection['has_url'] else detection['urls'][0][:40]
            chat = await ChatService.create_chat(
                user_id=user_id,
                project_id=project_id,
                title=title
            )
            chat_id = chat['id']
            is_new_chat = True
            print(f"Created new chat: {chat_id}")
            
            # ✅ Initialize chat status tracking using progress tracker
            ChatStatusTracker.initialize(chat_id)
            
            # ✅ Get progress tracker and set initial stage
            tracker = get_progress_tracker(chat_id)
            tracker.update_stage('pending', 0, "Initializing...")
            
            # Broadcast new chat creation
            await chat_connection_manager.send_status(chat_id)
        
        # --- VALIDATION: First message in a new chat MUST be a URL ---
        if is_new_chat and not detection['has_url']:
            raise HTTPException(
                status_code=400,
                detail="The first message in a conversation must be a valid website URL. Please provide a URL starting with https://"
            )
        
        # --- VALIDATION: If chat exists, check if it already has messages ---
        if not is_new_chat:
            try:
                existing_messages = await MessageService.get_messages(chat_id, user_id, limit=1)
                has_messages = len(existing_messages) > 0
                
                if not has_messages and not detection['has_url']:
                    raise HTTPException(
                        status_code=400,
                        detail="The first message in a conversation must be a valid website URL. Please provide a URL starting with https://"
                    )
            except NotFoundError:
                if not detection['has_url']:
                    raise HTTPException(
                        status_code=400,
                        detail="The first message in a conversation must be a valid website URL. Please provide a URL starting with https://"
                    )

        # --- VALIDATION: No second URL in existing chat ---
        if not is_new_chat and detection['has_url']:
            raise HTTPException(
                status_code=400,
                detail="This conversation already has a URL. You cannot add a new URL to an existing conversation."
            )

        chat_history = await MessageService.get_messages(
            chat_id,
            user_id,
            limit=20,
            latest=True
        )
        
        # Save user message
        user_message = await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role='user',
            content=content,
            is_url=detection['has_url']
        )
        print(f"Created user message: {user_message['id']}")
        
        # Broadcast user message via WebSocket
        await chat_connection_manager.send_message(chat_id, user_message)
        
        # Generate response based on detection
        scraping_job_id = None
        page_id = None
        
        if detection['has_url']:
            try:
                # It's a URL - create page and enqueue scraping job
                url = detection['urls'][0]
                print(f"📄 Creating page for URL: {url}")
                
                # Create page record
                page = await PageService.create_page_for_chat(
                    chat_id=chat_id,
                    project_id=project_id,
                    url=url,
                    normalized_url=url
                )
                page_id = page['id']
                print(f"✅ Created page: {page_id}")
                
                # ✅ Update progress to crawling stage
                tracker = get_progress_tracker(chat_id)
                tracker.update_stage('crawling', 0, f"Starting crawl for {url}...")
                await chat_connection_manager.send_status(chat_id)
                
                # Enqueue scraping job to Redis
                print(f"📤 Enqueuing scraping job to Redis...")
                scraping_job_id = redis_client.add_scraping_job(
                    url=url,
                    project_id=project_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    message_id=user_message['id']
                )
                
                if scraping_job_id:
                    print(f"✅ Enqueued scraping job: {scraping_job_id}")
                    response_content = f"I've received your URL: **{url}**\n\nI'll process this website and get back to you. (Scraping job enqueued)\n\nIn the meantime, feel free to ask questions about it once I've processed it."
                    
                    # Check queue status
                    queue_length = redis_client.get_queue_length()
                    print(f"📊 Queue length: {queue_length}")
                else:
                    print("⚠️ Redis returned no job ID - job not enqueued")
                    response_content = f"I've received your URL: **{url}**\n\n⚠️ Scraping service is currently unavailable. Please try again later."
                    
                    # ✅ Mark as failed using progress tracker
                    tracker = get_progress_tracker(chat_id)
                    tracker.mark_failed("Scraping service unavailable")
                    await chat_connection_manager.send_status(chat_id)
                    
            except Exception as e:
                print(f"❌ Error during scraping setup: {str(e)}")
                import traceback
                traceback.print_exc()
                # Continue without scraping
                url = detection['urls'][0]
                response_content = f"I've received your URL: **{url}**\n\n⚠️ There was an error setting up the scraping job. Please try again later."
                
                # ✅ Mark as failed using progress tracker
                tracker = get_progress_tracker(chat_id)
                tracker.mark_failed(f"Scraping setup error: {str(e)}")
                await chat_connection_manager.send_status(chat_id)
        else:
            # It's a question - process it
            response_content = f"I received your question: \"{content}\"\n\nOnce I've processed the website content, I'll be able to answer your questions. (RAG search coming soon!)"
            
            # ✅ Update progress to processing stage
            tracker = get_progress_tracker(chat_id)
            # ✅ Short-circuit conversational messages before RAG
            if CONVERSATIONAL_PATTERNS.match(content.strip()):
                content_lower = content.lower().strip()
                response_content = next(
                    (v for k, v in CONVERSATIONAL_RESPONSES.items() if k in content_lower),
                    "Hey! Ask me anything about the content I've indexed."
                )
                tracker.mark_completed("Done")
                await chat_connection_manager.send_status(chat_id)
            else:
                # ✅ Update progress to processing stage
                tracker.update_stage('processing', 0, "Processing your question...")
                await chat_connection_manager.send_status(chat_id)
            
                response_content = await asyncio.to_thread(
                    answer_user_question,
                    content,
                    chat_id=chat_id,
                    project_id=project_id,
                    page_id=page_id,
                    chat_history=chat_history
                )
                
                if response_content is None:
                    response_content = "I couldn't process your question. Please try again."
                
                # ✅ Mark as answered
                tracker = get_progress_tracker(chat_id)
                tracker.mark_completed("Answer generated!")
                await chat_connection_manager.send_status(chat_id)
        
        # Save assistant response
        assistant_message = await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role='assistant',
            content=response_content,
            is_url=False
        )
        print(f"Created assistant message: {assistant_message['id']}")
        
        # Broadcast assistant message via WebSocket
        await chat_connection_manager.send_message(chat_id, assistant_message)
        
        # Return response
        return ProcessLinkResponse(
            chat_id=chat_id,
            user_id=user_id,
            project_id=project_id,
            message=assistant_message,
            detection=detection,
            scraping_job_id=scraping_job_id,
            is_new_chat=is_new_chat
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error processing request: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
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

router = APIRouter(prefix="/api", tags=["process"])

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
        
        # Save user message
        user_message = await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role='user',
            content=content,
            is_url=detection['has_url']
        )
        print(f"Created user message: {user_message['id']}")
        
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
            except Exception as e:
                print(f"❌ Error during scraping setup: {str(e)}")
                import traceback
                traceback.print_exc()
                # Continue without scraping
                url = detection['urls'][0]
                response_content = f"I've received your URL: **{url}**\n\n⚠️ There was an error setting up the scraping job. Please try again later."
        else:
            # It's a question - acknowledge
            response_content = f"I received your question: \"{content}\"\n\nOnce I've processed the website content, I'll be able to answer your questions. (RAG search coming soon!)"
        
        # Save assistant response
        assistant_message = await MessageService.create_message(
            chat_id=chat_id,
            user_id=user_id,
            role='assistant',
            content=response_content,
            is_url=False
        )
        print(f"Created assistant message: {assistant_message['id']}")
        
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
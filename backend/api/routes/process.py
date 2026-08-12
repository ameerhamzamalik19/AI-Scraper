from fastapi import APIRouter, Depends, HTTPException
from models import LinkRequest, ProcessLinkResponse
from services.user_service import UserService
from services.project_service import ProjectService
from services.chat_service import ChatService
from services.page_service import PageService
from services.message_service import MessageService
from utils.detectors import InputDetector
from utils.helpers import get_iso_timestamp, generate_response

router = APIRouter(prefix="/api", tags=["process"])


@router.post("/process-link", response_model=ProcessLinkResponse)
async def process_link(request: LinkRequest):
    """Process a link or message from the user"""
    try:
        content = request.content
        if not content:
            raise HTTPException(status_code=400, detail="Content cannot be empty")
        
        # Get or create user
        user_id = request.user_id
        if not user_id:
            user_id = await UserService.get_or_create_user()
        
        # Get or create project
        project_id = await ProjectService.get_or_create_project(user_id)
        
        # Detect input type
        detection = InputDetector.detect_input_type(content)
        
        # Get or create chat
        chat_id = request.chat_id
        is_new_chat = False
        
        if not chat_id:
            # Create new chat
            chat = await ChatService.create_chat(
                user_id=user_id,
                project_id=project_id,
                title=content[:40] if not detection['has_url'] else detection['urls'][0]
            )
            chat_id = chat['id']
            is_new_chat = True
            print(f"Created new chat with ID: {chat_id}")
        
        # Save user message
        user_message = await ChatService.add_message_to_chat(
            chat_id=chat_id,
            user_id=user_id,
            content=content,
            role='user',
            is_url=detection['has_url']
        )
        
        # Generate response content
        response_content = generate_response(content, detection)
        
        # Save assistant response
        assistant_message = await ChatService.add_message_to_chat(
            chat_id=chat_id,
            user_id=user_id,
            content=response_content,
            role='assistant',
            is_url=False
        )
        
        # If URL detected, create scraping job
        scraping_job_id = None
        if detection['has_url']:
            for url in detection['urls']:
                # Create page for URL scraping
                page_id, page_version_id, document_id = await PageService.create_page_from_url(
                    url=url,
                    project_id=project_id,
                    user_id=user_id
                )
                scraping_job_id = page_id  # or get the actual job ID
                print(f"Created scraping job for URL: {url} with page ID: {page_id}")
        
        current_time = get_iso_timestamp()
        
        # Return response
        return {
            "chat_id": chat_id,
            "user_id": user_id,
            "project_id": project_id,
            "message": {
                "role": "assistant",
                "content": response_content,
                "timestamp": current_time
            },
            "detection": detection,
            "scraping_job_id": scraping_job_id if detection['has_url'] else None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error processing request: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
from typing import Optional
from database import get_db_connection
from utils.helpers import generate_uuid, get_iso_timestamp, safe_datetime_for_db
from exceptions import DatabaseError
from config import settings
import datetime


class ProjectService:
    """Service for project operations"""
    
    @staticmethod
    async def get_or_create_project(
        user_id: str, 
        project_name: str = None
    ) -> str:
        """Get or create a project for a user"""
        project_name = project_name or settings.DEFAULT_PROJECT_NAME
        
        try:
            async with get_db_connection() as conn:
                # Check if user has a project
                project = await conn.fetchrow(
                    "SELECT id FROM projects WHERE user_id = $1 LIMIT 1",
                    user_id
                )
                if project:
                    return str(project['id'])
                
                # Create a default project
                project_id = generate_uuid()
                current_time = get_iso_timestamp()

                safe_time: datetime = safe_datetime_for_db(current_time)

                await conn.execute(
                    """INSERT INTO projects (id, user_id, name, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5)""",
                    project_id, user_id, project_name, safe_time, safe_time
                )
                print(f"Created new project with ID: {project_id} for user: {user_id}")
                return project_id
        except Exception as e:
            raise DatabaseError(f"Failed to get or create project: {str(e)}")
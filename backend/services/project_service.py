from typing import Optional, Dict, Any, List
from database import get_db_connection
from utils.helpers import generate_uuid, get_current_datetime, safe_datetime_for_db
from exceptions import DatabaseError, NotFoundError
from config import settings


class ProjectService:
    """Service for project operations"""
    
    @staticmethod
    async def create_project(
        user_id: str,
        name: str
    ) -> dict:
        """Create a new project for a user"""
        try:
            async with get_db_connection() as conn:
                project_id = generate_uuid()
                now = get_current_datetime()
                safe_now = safe_datetime_for_db(now)
                
                await conn.execute(
                    """INSERT INTO projects 
                       (id, user_id, name, created_at, updated_at) 
                       VALUES ($1, $2, $3, $4, $5)""",
                    project_id, user_id, name, safe_now, safe_now
                )
                
                return {
                    "id": project_id,
                    "user_id": user_id,
                    "name": name,
                    "created_at": safe_now.isoformat(),
                    "updated_at": safe_now.isoformat()
                }
        except Exception as e:
            raise DatabaseError(f"Failed to create project: {str(e)}")
    
    @staticmethod
    async def get_projects(user_id: str) -> List[dict]:
        """Get all projects for a user"""
        try:
            async with get_db_connection() as conn:
                projects = await conn.fetch(
                    "SELECT * FROM projects WHERE user_id = $1 ORDER BY created_at DESC",
                    user_id
                )
                
                return [{
                    "id": str(project['id']),
                    "user_id": str(project['user_id']),
                    "name": project['name'],
                    "created_at": project['created_at'].isoformat() if project['created_at'] else None,
                    "updated_at": project['updated_at'].isoformat() if project['updated_at'] else None
                } for project in projects]
        except Exception as e:
            raise DatabaseError(f"Failed to get projects: {str(e)}")
    
    @staticmethod
    async def get_or_create_project(user_id: str, name: Optional[str] = None) -> str:
        """Get existing project or create a default one"""
        try:
            async with get_db_connection() as conn:
                # Check if user has any projects
                projects = await conn.fetch(
                    "SELECT id FROM projects WHERE user_id = $1 LIMIT 1",
                    user_id
                )
                
                if projects:
                    return str(projects[0]['id'])
                
                # Create default project
                if not name:
                    name = settings.DEFAULT_PROJECT_NAME or "Default Project"
                
                project = await ProjectService.create_project(user_id, name)
                return project['id']
                
        except Exception as e:
            raise DatabaseError(f"Failed to get or create project: {str(e)}")
    
    @staticmethod
    async def get_or_create_default_project(user_id: str) -> dict:
        """Get or create a default project for a user (returns full project dict)"""
        try:
            async with get_db_connection() as conn:
                # Check if user has any projects
                projects = await conn.fetch(
                    "SELECT * FROM projects WHERE user_id = $1 LIMIT 1",
                    user_id
                )
                
                if projects:
                    project = projects[0]
                    return {
                        "id": str(project['id']),
                        "user_id": str(project['user_id']),
                        "name": project['name'],
                        "created_at": project['created_at'].isoformat() if project['created_at'] else None,
                        "updated_at": project['updated_at'].isoformat() if project['updated_at'] else None
                    }
                
                # Create default project
                name = settings.DEFAULT_PROJECT_NAME or "Default Project"
                return await ProjectService.create_project(user_id, name)
                
        except Exception as e:
            raise DatabaseError(f"Failed to get or create default project: {str(e)}")
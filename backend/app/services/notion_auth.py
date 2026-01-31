"""Notion token management"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decrypt_token
from app.models.user import OAuthConnection


async def get_valid_notion_token(user_id: str, db: AsyncSession) -> str | None:
    """
    Get a valid Notion access token for the user.
    Notion tokens don't expire, so this is simpler than Google.
    """
    result = await db.execute(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == "notion",
        )
    )
    oauth_conn = result.scalar_one_or_none()
    
    if not oauth_conn:
        print("[NotionAuth] No Notion connection found for user")
        return None
    
    try:
        token = decrypt_token(oauth_conn.access_token)
        print("[NotionAuth] Token retrieved successfully")
        return token
    except Exception as e:
        print(f"[NotionAuth] Error decrypting token: {e}")
        return None

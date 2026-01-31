"""Helper to get valid Google access tokens, refreshing if needed"""

from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import encrypt_token, decrypt_token
from app.models.user import OAuthConnection

settings = get_settings()


async def get_valid_google_token(
    user_id: str,
    db: AsyncSession,
) -> str | None:
    """
    Get a valid Google access token for the user.
    Refreshes the token if it's expired or about to expire.
    Returns None if user has no Google connection.
    """
    try:
        result = await db.execute(
            select(OAuthConnection).where(
                OAuthConnection.user_id == user_id,
                OAuthConnection.provider == "google",
            )
        )
        oauth_conn = result.scalar_one_or_none()
        
        if not oauth_conn:
            print(f"[GoogleAuth] No OAuth connection found for user {user_id}")
            return None
        
        # Check if token is expired or will expire in the next 5 minutes
        now = datetime.utcnow()
        buffer = timedelta(minutes=5)
        
        if oauth_conn.token_expiry and oauth_conn.token_expiry > now + buffer:
            # Token is still valid
            print(f"[GoogleAuth] Token still valid, expires at {oauth_conn.token_expiry}")
            return decrypt_token(oauth_conn.access_token)
        
        print(f"[GoogleAuth] Token expired or expiring soon, refreshing...")
        
        # Token needs refresh
        if not oauth_conn.refresh_token:
            print("[GoogleAuth] No refresh token available")
            return None
        
        refresh_token = decrypt_token(oauth_conn.refresh_token)
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                settings.google_token_uri,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        
        if response.status_code != 200:
            print(f"[GoogleAuth] Token refresh failed: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"[GoogleAuth] Error getting token: {e}")
        return None
    
    tokens = response.json()
    new_access_token = tokens["access_token"]
    expires_in = tokens.get("expires_in", 3600)
    
    # Update stored token
    oauth_conn.access_token = encrypt_token(new_access_token)
    oauth_conn.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    
    # Google sometimes returns a new refresh token
    if "refresh_token" in tokens:
        oauth_conn.refresh_token = encrypt_token(tokens["refresh_token"])
    
    await db.commit()
    
    return new_access_token

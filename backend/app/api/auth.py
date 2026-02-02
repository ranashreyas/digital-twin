import json
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import (
    create_session_token,
    verify_session_token,
    encrypt_token,
    decrypt_token,
    generate_state_token,
)
from app.models.user import User, OAuthConnection

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

# In-memory state storage (use Redis in production)
oauth_states: dict[str, datetime] = {}


@router.get("/google/login")
async def google_login(request: Request):
    """Initiate Google OAuth flow"""
    state = generate_state_token()
    oauth_states[state] = datetime.utcnow()
    
    # Clean up old states (older than 10 minutes)
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for s in list(oauth_states.keys()):
        if oauth_states[s] < cutoff:
            del oauth_states[s]
    
    # Check if user is already logged in (to link Google to existing account)
    session_token = request.cookies.get("session")
    existing_user_id = None
    if session_token:
        payload = verify_session_token(session_token)
        if payload:
            existing_user_id = payload.get("user_id")
            print(f"[Google] Existing session found, will link to user {existing_user_id}")
    
    # Store existing user ID in state for callback
    if existing_user_id:
        oauth_states[f"{state}_user"] = existing_user_id
    
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": f"{settings.backend_url}/auth/google/callback",
        "response_type": "code",
        "scope": " ".join(settings.google_scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    
    auth_url = f"{settings.google_auth_uri}?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback"""
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}"
        )
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    
    # Verify state
    if state not in oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state")
    del oauth_states[state]
    
    # Check if we have an existing user to link to
    existing_user_id = oauth_states.pop(f"{state}_user", None)
    if existing_user_id:
        print(f"[Google] Will link to existing user {existing_user_id}")
    
    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            settings.google_token_uri,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": f"{settings.backend_url}/auth/google/callback",
            },
        )
    
    if token_response.status_code != 200:
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to get tokens: {token_response.text}"
        )
    
    tokens = token_response.json()
    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)
    
    # Get user info from Google
    async with httpx.AsyncClient() as client:
        userinfo_response = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    
    if userinfo_response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to get user info")
    
    userinfo = userinfo_response.json()
    google_user_id = userinfo["id"]
    name = userinfo.get("name")
    
    print(f"[Google] User authenticated: {userinfo.get('email')} (Google ID: {google_user_id})")
    
    # Find or create user - ONLY use session, never email lookup
    user = None
    
    # If we have an existing logged-in user, link to that account
    if existing_user_id:
        result = await db.execute(
            select(User).where(User.id == existing_user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            print(f"[Google] Linking to existing user {user.id}")
    
    # No existing session = create new user
    if not user:
        user = User(name=name)
        db.add(user)
        await db.flush()
        print(f"[Google] Created new user: {user.id}")
    
    # Find or update OAuth connection
    result = await db.execute(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user.id,
            OAuthConnection.provider == "google",
        )
    )
    oauth_conn = result.scalar_one_or_none()
    
    token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
    
    if not oauth_conn:
        oauth_conn = OAuthConnection(
            user_id=user.id,
            provider="google",
            provider_user_id=google_user_id,
            access_token=encrypt_token(access_token),
            refresh_token=encrypt_token(refresh_token) if refresh_token else None,
            token_expiry=token_expiry,
            scopes=json.dumps(settings.google_scopes),
        )
        db.add(oauth_conn)
    else:
        oauth_conn.access_token = encrypt_token(access_token)
        if refresh_token:
            oauth_conn.refresh_token = encrypt_token(refresh_token)
        oauth_conn.token_expiry = token_expiry
        oauth_conn.provider_user_id = google_user_id
    
    await db.commit()
    
    # Create session token
    session_token = create_session_token(str(user.id))
    
    # Redirect to frontend with session cookie
    response = RedirectResponse(url=f"{settings.frontend_url}")
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=False,  # Set True in production with HTTPS
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    return response


# ============== Notion OAuth ==============

@router.get("/notion/login")
async def notion_login(request: Request):
    """Initiate Notion OAuth flow"""
    if not settings.notion_client_id:
        raise HTTPException(
            status_code=500, 
            detail="Notion not configured. Set NOTION_CLIENT_ID and NOTION_CLIENT_SECRET in .env"
        )
    
    state = generate_state_token()
    oauth_states[state] = datetime.utcnow()
    
    # Clean up old states (older than 10 minutes)
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    for s in list(oauth_states.keys()):
        if oauth_states[s] < cutoff:
            del oauth_states[s]
    
    # Check if user is already logged in (to link Notion to existing account)
    session_token = request.cookies.get("session")
    existing_user_id = None
    if session_token:
        payload = verify_session_token(session_token)
        if payload:
            existing_user_id = payload.get("user_id")
    
    # Store existing user ID in state for callback
    if existing_user_id:
        oauth_states[f"{state}_user"] = existing_user_id
    
    params = {
        "client_id": settings.notion_client_id,
        "redirect_uri": f"{settings.backend_url}/auth/notion/callback",
        "response_type": "code",
        "owner": "user",
        "state": state,
    }
    
    auth_url = f"https://api.notion.com/v1/oauth/authorize?{urlencode(params)}"
    return RedirectResponse(url=auth_url)


@router.get("/notion/callback")
async def notion_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle Notion OAuth callback"""
    if error:
        return RedirectResponse(
            url=f"{settings.frontend_url}?error={error}"
        )
    
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")
    
    # Verify state
    if state not in oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state")
    del oauth_states[state]
    
    # Check if we have an existing user to link to
    existing_user_id = oauth_states.pop(f"{state}_user", None)
    
    # Exchange code for tokens
    # Notion uses Basic auth with client_id:client_secret
    import base64
    credentials = base64.b64encode(
        f"{settings.notion_client_id}:{settings.notion_client_secret}".encode()
    ).decode()
    
    async with httpx.AsyncClient() as client:
        token_response = await client.post(
            "https://api.notion.com/v1/oauth/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            json={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"{settings.backend_url}/auth/notion/callback",
            },
        )
    
    if token_response.status_code != 200:
        print(f"[Notion] Token exchange failed: {token_response.text}")
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to get tokens: {token_response.text}"
        )
    
    tokens = token_response.json()
    access_token = tokens["access_token"]
    workspace_id = tokens.get("workspace_id")
    workspace_name = tokens.get("workspace_name")
    bot_id = tokens.get("bot_id")
    
    # Get user info from the token response (Notion includes it)
    owner = tokens.get("owner", {})
    notion_user = owner.get("user", {}) if owner.get("type") == "user" else {}
    
    notion_user_id = notion_user.get("id", bot_id)
    name = notion_user.get("name")
    
    print(f"[Notion] User authenticated: {name} (Notion ID: {notion_user_id})")
    print(f"[Notion] Workspace: {workspace_name} ({workspace_id})")
    
    # Find or create user - ONLY use session, never email lookup
    user = None
    
    # If we have an existing logged-in user, link to that account
    if existing_user_id:
        result = await db.execute(
            select(User).where(User.id == existing_user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            print(f"[Notion] Linking to existing user {user.id}")
    
    # No existing session = create new user
    if not user:
        user = User(name=name)
        db.add(user)
        await db.flush()
        print(f"[Notion] Created new user: {user.id}")
    
    # Find or update OAuth connection
    result = await db.execute(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user.id,
            OAuthConnection.provider == "notion",
        )
    )
    oauth_conn = result.scalar_one_or_none()
    
    # Notion tokens don't expire, but we set a far future date
    token_expiry = datetime.utcnow() + timedelta(days=365 * 10)
    
    if not oauth_conn:
        oauth_conn = OAuthConnection(
            user_id=user.id,
            provider="notion",
            provider_user_id=notion_user_id,
            access_token=encrypt_token(access_token),
            refresh_token=None,  # Notion doesn't use refresh tokens
            token_expiry=token_expiry,
            scopes=json.dumps({"workspace_id": workspace_id, "workspace_name": workspace_name}),
        )
        db.add(oauth_conn)
    else:
        oauth_conn.access_token = encrypt_token(access_token)
        oauth_conn.token_expiry = token_expiry
        oauth_conn.provider_user_id = notion_user_id
        oauth_conn.scopes = json.dumps({"workspace_id": workspace_id, "workspace_name": workspace_name})
    
    await db.commit()
    
    # Create session token
    session_token = create_session_token(str(user.id))
    
    # Redirect to frontend with session cookie
    response = RedirectResponse(url=f"{settings.frontend_url}")
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=False,  # Set True in production with HTTPS
        samesite="lax",
        max_age=86400 * 7,  # 7 days
    )
    return response


@router.get("/me")
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get current authenticated user"""
    session_token = request.cookies.get("session")
    
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = verify_session_token(session_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    user_id = payload["user_id"]
    
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    # Get connected services
    result = await db.execute(
        select(OAuthConnection.provider).where(
            OAuthConnection.user_id == user.id
        )
    )
    connected_providers = [row[0] for row in result.all()]
    
    return {
        "id": str(user.id),
        "name": user.name,
        "connected_services": connected_providers,
    }


@router.post("/logout")
async def logout(response: Response):
    """Log out the current user"""
    response.delete_cookie("session")
    return {"message": "Logged out"}


@router.delete("/disconnect/{provider}")
async def disconnect_service(
    provider: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Disconnect a service (remove OAuth tokens and user if no connections left)"""
    session_token = request.cookies.get("session")
    
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = verify_session_token(session_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    user_id = payload["user_id"]
    
    # Find and delete the OAuth connection
    result = await db.execute(
        select(OAuthConnection).where(
            OAuthConnection.user_id == user_id,
            OAuthConnection.provider == provider,
        )
    )
    oauth_conn = result.scalar_one_or_none()
    
    if not oauth_conn:
        raise HTTPException(status_code=404, detail=f"No {provider} connection found")
    
    await db.delete(oauth_conn)
    
    # Check if user has any remaining connections
    result = await db.execute(
        select(OAuthConnection).where(OAuthConnection.user_id == user_id)
    )
    remaining_connections = result.scalars().all()
    
    # If no connections left, delete the user and clear session
    if not remaining_connections:
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user:
            await db.delete(user)
        response.delete_cookie("session")
    
    await db.commit()
    
    return {"message": f"Disconnected from {provider}"}


async def get_current_user_id(request: Request) -> str:
    """Dependency to get current user ID from session"""
    session_token = request.cookies.get("session")
    
    if not session_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = verify_session_token(session_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    
    return payload["user_id"]

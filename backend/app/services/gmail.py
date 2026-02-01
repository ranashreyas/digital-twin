"""Gmail service wrapper"""

import base64
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.google_auth import get_valid_google_token


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"


async def get_emails(
    user_id: str,
    db: AsyncSession,
    query: str = "",
    start_date: str = "",
    end_date: str = "",
    max_results: int = 50,
) -> list[dict[str, Any]]:
    """
    Get emails with optional search query and date range.
    
    Args:
        query: Search query (e.g., 'from:john', 'subject:meeting', 'Createbase').
               Empty string returns all emails in date range.
        start_date: Start date in YYYY-MM-DD format (defaults to 30 days ago)
        end_date: End date in YYYY-MM-DD format (defaults to today)
        max_results: Maximum number of emails to return.
    """
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Gmail] No valid access token available")
        return []
    
    print(f"[Gmail] Getting emails (query='{query}', start_date='{start_date}', end_date='{end_date}')")
    
    # Build the search query with date filters
    now = datetime.utcnow()
    
    # Parse start_date or default to 30 days ago (emails often need wider lookback)
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            print(f"[Gmail] Invalid start_date format: {start_date}, using 30 days ago")
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    else:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=30)
    
    # Parse end_date or default to today
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            print(f"[Gmail] Invalid end_date format: {end_date}, using today")
            end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        end_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Gmail uses after: and before: with YYYY/MM/DD format
    date_query_parts = []
    date_query_parts.append(f"after:{start_dt.strftime('%Y/%m/%d')}")
    # Add 1 day to end_date because Gmail's before: is exclusive
    end_dt_plus_one = end_dt + timedelta(days=1)
    date_query_parts.append(f"before:{end_dt_plus_one.strftime('%Y/%m/%d')}")
    
    # Combine user query with date filters
    full_query = " ".join(date_query_parts)
    if query:
        full_query = f"{query} {full_query}"
    
    print(f"[Gmail] Full query: '{full_query}'")
    
    params = {
        "maxResults": max_results,
        "q": full_query,
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
    
    if response.status_code != 200:
        print(f"[Gmail] API Error {response.status_code}: {response.text}")
        return []
    
    data = response.json()
    messages = data.get("messages", [])
    
    print(f"[Gmail] Found {len(messages)} messages")
    
    emails = []
    async with httpx.AsyncClient() as client:
        for msg in messages:
            # Get full message details
            msg_response = await client.get(
                f"{GMAIL_API_BASE}/users/me/messages/{msg['id']}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            
            if msg_response.status_code != 200:
                continue
            
            msg_data = msg_response.json()
            headers = {
                h["name"]: h["value"] 
                for h in msg_data.get("payload", {}).get("headers", [])
            }
            
            emails.append({
                "id": msg["id"],
                "thread_id": msg.get("threadId"),
                "from": headers.get("From"),
                "subject": headers.get("Subject"),
                "date": headers.get("Date"),
                "snippet": msg_data.get("snippet"),
            })
    
    return emails


async def get_email_content(
    user_id: str,
    db: AsyncSession,
    message_id: str,
) -> dict[str, Any] | None:
    """Get full email content by ID"""
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Gmail] No valid access token available")
        return None
    
    print(f"[Gmail] Getting email content for message {message_id}")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GMAIL_API_BASE}/users/me/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
    
    if response.status_code != 200:
        print(f"[Gmail] API Error {response.status_code}: {response.text}")
        return None
    
    msg_data = response.json()
    headers = {
        h["name"]: h["value"] 
        for h in msg_data.get("payload", {}).get("headers", [])
    }
    
    # Try to get plain text body
    body = ""
    payload = msg_data.get("payload", {})
    
    if "body" in payload and payload["body"].get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
    elif "parts" in payload:
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                break
    
    return {
        "id": message_id,
        "thread_id": msg_data.get("threadId"),
        "from": headers.get("From"),
        "to": headers.get("To"),
        "subject": headers.get("Subject"),
        "date": headers.get("Date"),
        "body": body,
    }


async def get_email_thread(
    user_id: str,
    db: AsyncSession,
    thread_id: str,
) -> dict[str, Any] | None:
    """
    Get an entire email thread/conversation by thread ID.
    Returns all messages in the thread in chronological order.
    """
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Gmail] No valid access token available")
        return None
    
    print(f"[Gmail] Getting email thread {thread_id}")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{GMAIL_API_BASE}/users/me/threads/{thread_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
    
    if response.status_code != 200:
        print(f"[Gmail] API Error {response.status_code}: {response.text}")
        return None
    
    thread_data = response.json()
    messages = []
    
    for msg in thread_data.get("messages", []):
        headers = {
            h["name"]: h["value"] 
            for h in msg.get("payload", {}).get("headers", [])
        }
        
        # Try to get plain text body
        body = ""
        payload = msg.get("payload", {})
        
        if "body" in payload and payload["body"].get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8")
        elif "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8")
                    break
        
        messages.append({
            "id": msg.get("id"),
            "from": headers.get("From"),
            "to": headers.get("To"),
            "date": headers.get("Date"),
            "body": body,
        })
    
    # Get subject from first message
    first_msg_headers = {
        h["name"]: h["value"] 
        for h in thread_data.get("messages", [{}])[0].get("payload", {}).get("headers", [])
    }
    
    return {
        "thread_id": thread_id,
        "subject": first_msg_headers.get("Subject"),
        "message_count": len(messages),
        "messages": messages,
    }

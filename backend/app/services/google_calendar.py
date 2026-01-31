"""Google Calendar service wrapper"""

from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.google_auth import get_valid_google_token


CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


async def get_events(
    user_id: str,
    db: AsyncSession,
    query: str = "",
    start_date: str = "",
    end_date: str = "",
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """
    Get calendar events with optional search query and date range.
    - query: Optional search term to filter events by name
    - start_date: Start date in YYYY-MM-DD format (defaults to today)
    - end_date: End date in YYYY-MM-DD format (defaults to 7 days from start_date)
    - Time range is always from 12:00 AM of start_date to 11:59 PM of end_date
    """
    print(f"[Calendar] Getting events (query='{query}', start_date='{start_date}', end_date='{end_date}')")
    
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Calendar] No valid access token")
        return []
    
    now = datetime.utcnow()
    
    # Parse start_date or default to today
    if start_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            print(f"[Calendar] Invalid start_date format: {start_date}, using today")
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Parse end_date or default to 7 days from start
    if end_date:
        try:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            print(f"[Calendar] Invalid end_date format: {end_date}, using 7 days from start")
            end_dt = start_dt + timedelta(days=7)
    else:
        end_dt = start_dt + timedelta(days=7)
    
    # Start from 12:00 AM (midnight) of start_date
    time_min = start_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat() + "Z"
    
    # End at 11:59 PM of end_date
    time_max = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat() + "Z"
    
    print(f"[Calendar] Time range: {time_min} to {time_max}")
    
    # Build params
    params = {
        "maxResults": max_results,
        "singleEvents": "true",
        "orderBy": "startTime",
        "timeMin": time_min,
        "timeMax": time_max,
    }
    
    # Only add query param if provided
    if query:
        params["q"] = query
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
    
    if response.status_code != 200:
        print(f"[Calendar] API Error {response.status_code}: {response.text}")
        return []
    
    data = response.json()
    events = []
    
    print(f"[Calendar] Found {len(data.get('items', []))} events")
    
    for item in data.get("items", []):
        start = item.get("start", {})
        end = item.get("end", {})
        
        events.append({
            "id": item.get("id"),
            "summary": item.get("summary", "No title"),
            "description": item.get("description"),
            "start": start.get("dateTime") or start.get("date"),
            "end": end.get("dateTime") or end.get("date"),
            "location": item.get("location"),
            "attendees": [a.get("email") for a in item.get("attendees", [])],
            "html_link": item.get("htmlLink"),
        })
    
    return events


async def create_event(
    user_id: str,
    db: AsyncSession,
    summary: str,
    start_time: str,
    end_time: str,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
) -> dict[str, Any] | None:
    """Create a new calendar event"""
    print(f"[Calendar] Creating event: {summary}")
    print(f"[Calendar]   start_time: {start_time}")
    print(f"[Calendar]   end_time: {end_time}")
    
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Calendar] No valid access token available")
        return None
    
    # Use America/Los_Angeles (PST/PDT) as default timezone
    # TODO: Could detect user's timezone from their Google Calendar settings
    event_body = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": "America/Los_Angeles"},
        "end": {"dateTime": end_time, "timeZone": "America/Los_Angeles"},
    }
    
    if description:
        event_body["description"] = description
    if location:
        event_body["location"] = location
    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{CALENDAR_API_BASE}/calendars/primary/events",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=event_body,
            params={"sendUpdates": "all"} if attendees else {},
        )
    
    if response.status_code not in (200, 201):
        print(f"[Calendar] Create event error {response.status_code}: {response.text}")
        return None
    
    data = response.json()
    return {
        "id": data.get("id"),
        "summary": data.get("summary"),
        "start": data.get("start", {}).get("dateTime"),
        "end": data.get("end", {}).get("dateTime"),
        "html_link": data.get("htmlLink"),
    }


async def update_event(
    user_id: str,
    db: AsyncSession,
    event_id: str,
    summary: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
) -> dict[str, Any] | None:
    """Update an existing calendar event"""
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Calendar] No valid access token available")
        return None
    
    # First get the existing event
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    
    if response.status_code != 200:
        print(f"[Calendar] Get event error {response.status_code}: {response.text}")
        return None
    
    event_body = response.json()
    
    # Update fields if provided
    if summary:
        event_body["summary"] = summary
    if start_time:
        event_body["start"] = {"dateTime": start_time, "timeZone": "America/Los_Angeles"}
    if end_time:
        event_body["end"] = {"dateTime": end_time, "timeZone": "America/Los_Angeles"}
    if description is not None:
        event_body["description"] = description
    if location is not None:
        event_body["location"] = location
    
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=event_body,
        )
    
    if response.status_code != 200:
        print(f"[Calendar] Update event error {response.status_code}: {response.text}")
        return None
    
    data = response.json()
    return {
        "id": data.get("id"),
        "summary": data.get("summary"),
        "start": data.get("start", {}).get("dateTime"),
        "end": data.get("end", {}).get("dateTime"),
        "html_link": data.get("htmlLink"),
    }


async def add_attendees_to_event(
    user_id: str,
    db: AsyncSession,
    event_id: str,
    attendee_emails: list[str],
) -> dict[str, Any] | None:
    """Add attendees to an existing event (share the event)"""
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Calendar] No valid access token available")
        return None
    
    # First get the existing event
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    
    if response.status_code != 200:
        print(f"[Calendar] Get event error {response.status_code}: {response.text}")
        return None
    
    event_body = response.json()
    
    # Add new attendees to existing ones
    existing_attendees = event_body.get("attendees", [])
    existing_emails = {a.get("email") for a in existing_attendees}
    
    for email in attendee_emails:
        if email not in existing_emails:
            existing_attendees.append({"email": email})
    
    event_body["attendees"] = existing_attendees
    
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=event_body,
            params={"sendUpdates": "all"},
        )
    
    if response.status_code != 200:
        print(f"[Calendar] Add attendees error {response.status_code}: {response.text}")
        return None
    
    data = response.json()
    return {
        "id": data.get("id"),
        "summary": data.get("summary"),
        "attendees": [a.get("email") for a in data.get("attendees", [])],
        "html_link": data.get("htmlLink"),
    }


async def delete_event(
    user_id: str,
    db: AsyncSession,
    event_id: str,
) -> bool:
    """Delete a calendar event"""
    access_token = await get_valid_google_token(user_id, db)
    
    if not access_token:
        print("[Calendar] No valid access token available")
        return False
    
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{CALENDAR_API_BASE}/calendars/primary/events/{event_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    
    if response.status_code not in (200, 204):
        print(f"[Calendar] Delete event error {response.status_code}: {response.text}")
        return False
    
    return True

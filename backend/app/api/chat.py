"""Chat endpoint with LLM integration"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import verify_session_token
from app.services.google_calendar import (
    get_events,
    create_event,
    update_event,
    add_attendees_to_event,
    delete_event,
)
from app.services.gmail import get_emails, get_email_content, get_email_thread
from app.services.notion import (
    search_pages as search_notion_pages,
    get_page_content as get_notion_page_content,
    create_page as create_notion_page,
    update_page as update_notion_page,
    delete_page as delete_notion_page,
    update_block as update_notion_block,
    delete_block as delete_notion_block,
)

router = APIRouter(prefix="/chat", tags=["chat"])
settings = get_settings()


class ToolCallInfo(BaseModel):
    id: str = ""  # Tool call ID for OpenAI API
    name: str
    arguments: dict
    result: str


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str
    tool_calls: list[ToolCallInfo] = []  # Tool calls made in this message (for assistant messages)


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []  # Previous messages in the conversation


class ChatResponse(BaseModel):
    response: str
    context_used: list[str] = []
    tool_calls: list[ToolCallInfo] = []  # Detailed tool call info for history


# Define the tools the LLM can use
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Get calendar events. Can list all events or search for specific ones. Time range is from 12:00 AM of start_date to 11:59 PM of end_date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search term to filter events by name (leave empty to get all events)",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to today)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to 7 days from start_date)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of events to return (default 25)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_emails",
            "description": "Get emails from the user's inbox. Can list recent emails or search for specific ones using Gmail query syntax. Time range is from start_date to end_date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Optional search query (e.g., 'from:someone@example.com', 'subject:meeting', 'is:unread'). Leave empty to get recent inbox emails.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to today)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to 7 days from start_date)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default 25)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_content",
            "description": "Get the full content of a specific email by its ID. Use get_emails first to find email IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The ID of the email to retrieve",
                    },
                },
                "required": ["message_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_email_thread",
            "description": "Get an entire email thread/conversation (all back-and-forth messages). Use get_emails first to find the thread_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {
                        "type": "string",
                        "description": "The thread ID of the email conversation",
                    },
                },
                "required": ["thread_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Create a new calendar event. Times should be in ISO 8601 format (e.g., '2024-01-15T14:00:00Z')",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Title of the event",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "Start time in ISO 8601 format (e.g., '2024-01-15T14:00:00Z')",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "End time in ISO 8601 format (e.g., '2024-01-15T15:00:00Z')",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of the event (optional)",
                    },
                    "location": {
                        "type": "string",
                        "description": "Location of the event (optional)",
                    },
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of attendee email addresses to invite (optional)",
                    },
                },
                "required": ["summary", "start_time", "end_time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_calendar_event",
            "description": "Update an existing calendar event. First search for the event to get its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The ID of the event to update",
                    },
                    "summary": {
                        "type": "string",
                        "description": "New title of the event (optional)",
                    },
                    "start_time": {
                        "type": "string",
                        "description": "New start time in ISO 8601 format (optional)",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "New end time in ISO 8601 format (optional)",
                    },
                    "description": {
                        "type": "string",
                        "description": "New description (optional)",
                    },
                    "location": {
                        "type": "string",
                        "description": "New location (optional)",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "share_calendar_event",
            "description": "Share a calendar event by adding attendees. They will receive email invitations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The ID of the event to share",
                    },
                    "attendee_emails": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of email addresses to invite",
                    },
                },
                "required": ["event_id", "attendee_emails"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_calendar_event",
            "description": "Delete a calendar event. First search for the event to get its ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The ID of the event to delete",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
    # ============== Notion Tools ==============
    {
        "type": "function",
        "function": {
            "name": "search_notion",
            "description": "Search for pages in the user's Notion workspace. Returns titles, URLs, and metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (leave empty to get recent pages)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results (default 25)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_notion_page",
            "description": "Get the content of a specific Notion page by its ID. Use search_notion first to find the page ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The ID of the Notion page to retrieve",
                    },
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_notion_page",
            "description": "Create a new Notion page as a child of another page. Use search_notion first to find a parent page ID. Use PLAIN TEXT only for content (no markdown).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the new page",
                    },
                    "parent_page_id": {
                        "type": "string",
                        "description": "The ID of the parent page where the new page will be created",
                    },
                    "content": {
                        "type": "string",
                        "description": "Optional PLAIN TEXT content (no markdown). Use newlines for paragraphs.",
                    },
                },
                "required": ["title", "parent_page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_notion_page",
            "description": "Update a Notion page - change title and/or append new content. Use PLAIN TEXT only (no markdown). For modifying or deleting specific blocks, use update_notion_block or delete_notion_block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The ID of the page to update",
                    },
                    "new_title": {
                        "type": "string",
                        "description": "New title for the page (optional)",
                    },
                    "append_content": {
                        "type": "string",
                        "description": "PLAIN TEXT to append (no markdown). Use newlines for paragraphs.",
                    },
                },
                "required": ["page_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_notion_block",
            "description": "Update the text content of a specific block. Use get_notion_page first to see all blocks and their IDs. Use PLAIN TEXT only (no markdown).",
            "parameters": {
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "The ID of the block to update",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The new PLAIN TEXT content for the block (no markdown)",
                    },
                },
                "required": ["block_id", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_notion_block",
            "description": "Delete a specific block from a Notion page. Use get_notion_page first to see all blocks and their IDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "block_id": {
                        "type": "string",
                        "description": "The ID of the block to delete",
                    },
                },
                "required": ["block_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_notion_page",
            "description": "Delete (archive) a Notion page. This action archives the page - it can be restored from Notion's trash. Use search_notion first to find the page ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id": {
                        "type": "string",
                        "description": "The ID of the page to delete",
                    },
                },
                "required": ["page_id"],
            },
        },
    },
]

SYSTEM_PROMPT_WITH_TOOLS = """You are a helpful digital assistant that acts as the user's "digital twin". 
You have access to their connected services (Google Calendar, Gmail, Notion) and can help them manage their digital life.

GOOGLE CALENDAR & GMAIL:
- View upcoming calendar events and meetings
- Search for specific events
- Create new calendar events
- Edit existing events (change time, title, location, etc.)
- Share events by inviting people via email
- Delete events
- Read and search emails

NOTION (if connected):
- Search for pages
- Read page content
- Create new pages (as child of existing page)
- Update pages (change title, append content)
- Update or delete individual blocks
- Delete (archive) pages

IMPORTANT - Notion content formatting:
When creating or updating Notion content, use PLAIN TEXT only. Do NOT use markdown formatting (no **, #, -, etc.) as Notion's API does not render markdown - it will appear as literal characters.

IMPORTANT - Notion updates workflow:
Before making ANY update to a Notion page (updating blocks, deleting blocks, modifying content), you MUST first call get_notion_page to see the current page contents. Never update or delete blocks blindly - always fetch the page first to see what's there, then decide what to modify.

IMPORTANT - Adding vs Creating in Notion:
- "Add to a page" / "Add content" / "Write in my page" → Use update_notion_page with append_content to add blocks to an EXISTING page
- "Create a page" / "Make a new page" → Use create_notion_page to create a NEW page
Only create a new page when the user EXPLICITLY asks to create one. Adding content to an existing page is NOT creating a page.

IMPORTANT - Always ask for missing information:

CLARIFYING "MEETINGS"!!!!
When a user asks about "meetings", clarify what they mean:
  - Events with multiple attendees/invites (actual meetings with other people)?
  - Or any calendar event in the time range (including personal reminders, solo blocks, etc.)?
  Example: "Show me my meetings this week" → Ask: "Would you like to see only events with other attendees, or all calendar events?"

To CREATE an event, you need: the event name, date, and time. If any are missing, ask.
  Example: "Create a meeting called standup" → Ask: "When should I schedule this?"

To EDIT, SHARE, or DELETE an event, you need to know which event. If unclear, search first.
  Example: "Delete my appointment" → Search for it first to find the event ID. Once you have found some candidates, show them to the user for verification, then delete as deleting anything is be a CRITICAL action.

To SHARE an event, you need the person's email address.
  Example: "Invite John to my meeting" → Ask: "What is John's email address?"

If the user doesn't specify how long an event should be FIRST ASK. If they still dont comply, assume 1 hour.

SEARCHING FOR EVENTS - CRITICAL: The search is case-sensitive and exact. You MUST try multiple variations before concluding no events exist:

1. First try the exact term the user mentioned
2. If 0 results, IMMEDIATELY try variations (shorter keywords, single words)
3. If still 0 results, IMMEDIATELY try an EMPTY query "" to list ALL events
4. Only after trying at least 3 different queries can you say "no events found"
5. Maximum 5 search attempts per request

DO NOT respond with "no events found" after only 1 search attempt. KEEP TRYING.

Example: User says "find my dentist appointment"
  - Call: get_calendar_events(query="dentist appointment") → 0 results
  - Call: get_calendar_events(query="dentist") → 0 results  
  - Call: get_calendar_events(query="") → [shows all events] → scan for dentist-related
  - Now you can respond with confidence

SEARCHING FOR EMAILS - Same principle applies:

1. First try the exact term the user mentioned
2. If 0 results, try variations (simpler keywords, different phrasing)
3. If still 0 results, try an EMPTY query "" to get recent inbox emails
4. Only after trying at least 3 different queries can you say "no emails found"
5. Maximum 5 search attempts per request

Example: User says "find emails from John about the project"
  - Call: get_emails(query="from:john project") → 0 results
  - Call: get_emails(query="from:john") → 0 results
  - Call: get_emails(query="project") → [shows emails] → scan for John
  - Now you can respond with confidence

When responding, format dates and times in a human-readable way (e.g., "Tomorrow at 3:00 PM").

COMPREHENSIVE QUERIES - CRITICAL:
When users ask for "information about", "all information about", "everything about", "tell me about", "what do I have about", "how to prepare for", or similar comprehensive requests:
1. Search ALL connected services (Calendar, Gmail, AND Notion) for related information
2. Do NOT stop after searching one source - always check ALL available backends
3. For any topic X:
   - Check calendar for events related to X
   - Search emails for correspondence about X
   - Search Notion for pages/notes about X
4. Combine information from ALL sources in your response
5. Example: "Tell me about my meeting with Acme Corp"
   - get_calendar_events(query="Acme") → get event details
   - get_emails(query="Acme") → find related emails, then get_email_thread for full context
   - search_notion(query="Acme") → find any notes or pages
   - Combine ALL findings in response

Current date and time: {current_time}
"""

SYSTEM_PROMPT_NO_TOOLS = """You are a helpful digital assistant.
The user has not connected any services yet, so you cannot access their calendar or emails.
If they ask about their schedule or emails, politely let them know they need to connect their Google account first (using the Connections button in the top right).
Otherwise, just be a helpful general assistant.

Current date and time: {current_time}
"""


async def execute_tool(
    tool_name: str,
    arguments: dict[str, Any],
    user_id: str,
    db: AsyncSession,
) -> str:
    """Execute a tool call and return the result as a string"""
    try:
        if tool_name == "get_calendar_events":
            events = await get_events(
                user_id=user_id,
                db=db,
                query=arguments.get("query", ""),
                start_date=arguments.get("start_date", ""),
                end_date=arguments.get("end_date", ""),
                max_results=arguments.get("max_results", 25),
            )
            if not events:
                query = arguments.get("query", "")
                if query:
                    return f"No events found matching '{query}'. TRY AGAIN with a different query (shorter keyword or empty query to list all events)."
                return "No events found for this time period. TRY AGAIN with a wider date range or empty query."
            return json.dumps(events, indent=2)
        
        elif tool_name == "get_emails":
            emails = await get_emails(
                user_id=user_id,
                db=db,
                query=arguments.get("query", ""),
                start_date=arguments.get("start_date", ""),
                end_date=arguments.get("end_date", ""),
                max_results=arguments.get("max_results", 25),
            )
            if not emails:
                query = arguments.get("query", "")
                if query:
                    return f"No emails found matching '{query}'. TRY AGAIN with a different query (simpler keywords, different phrasing, or empty query to list recent emails)."
                return "No recent emails found, or Gmail is not connected."
            return json.dumps(emails, indent=2)
        
        elif tool_name == "get_email_content":
            email = await get_email_content(
                user_id=user_id,
                db=db,
                message_id=arguments["message_id"],
            )
            if not email:
                return "Failed to get email. Check the message ID or ensure Gmail is connected."
            return json.dumps(email, indent=2)
        
        elif tool_name == "get_email_thread":
            thread = await get_email_thread(
                user_id=user_id,
                db=db,
                thread_id=arguments["thread_id"],
            )
            if not thread:
                return "Failed to get email thread. Check the thread ID or ensure Gmail is connected."
            return json.dumps(thread, indent=2)
        
        elif tool_name == "create_calendar_event":
            event = await create_event(
                user_id=user_id,
                db=db,
                summary=arguments["summary"],
                start_time=arguments["start_time"],
                end_time=arguments["end_time"],
                description=arguments.get("description"),
                location=arguments.get("location"),
                attendees=arguments.get("attendees"),
            )
            if not event:
                return "Failed to create event. Please check the details and try again."
            return f"Event created successfully!\n{json.dumps(event, indent=2)}"
        
        elif tool_name == "update_calendar_event":
            event = await update_event(
                user_id=user_id,
                db=db,
                event_id=arguments["event_id"],
                summary=arguments.get("summary"),
                start_time=arguments.get("start_time"),
                end_time=arguments.get("end_time"),
                description=arguments.get("description"),
                location=arguments.get("location"),
            )
            if not event:
                return "Failed to update event. Please check the event ID and try again."
            return f"Event updated successfully!\n{json.dumps(event, indent=2)}"
        
        elif tool_name == "share_calendar_event":
            event = await add_attendees_to_event(
                user_id=user_id,
                db=db,
                event_id=arguments["event_id"],
                attendee_emails=arguments["attendee_emails"],
            )
            if not event:
                return "Failed to share event. Please check the event ID and try again."
            return f"Event shared successfully! Invitations sent.\n{json.dumps(event, indent=2)}"
        
        elif tool_name == "delete_calendar_event":
            success = await delete_event(
                user_id=user_id,
                db=db,
                event_id=arguments["event_id"],
            )
            if not success:
                return "Failed to delete event. Please check the event ID and try again."
            return "Event deleted successfully!"
        
        # ============== Notion Tools ==============
        elif tool_name == "search_notion":
            pages = await search_notion_pages(
                user_id=user_id,
                db=db,
                query=arguments.get("query", ""),
                max_results=arguments.get("max_results", 25),
            )
            if not pages:
                query = arguments.get("query", "")
                if query:
                    return f"No Notion pages found matching '{query}'. TRY AGAIN with different keywords or an empty query to list recent pages."
                return "No Notion pages found, or Notion is not connected."
            return json.dumps(pages, indent=2)
        
        elif tool_name == "get_notion_page":
            page = await get_notion_page_content(
                user_id=user_id,
                db=db,
                page_id=arguments["page_id"],
            )
            if not page:
                return "Failed to get page. Check the page ID or ensure the page is shared with the integration."
            return json.dumps(page, indent=2)
        
        elif tool_name == "create_notion_page":
            page = await create_notion_page(
                user_id=user_id,
                db=db,
                title=arguments["title"],
                parent_page_id=arguments["parent_page_id"],
                content=arguments.get("content", ""),
            )
            if not page:
                return "Failed to create page. Check the parent page ID and ensure it's shared with the integration."
            return f"Page created successfully!\n{json.dumps(page, indent=2)}"
        
        elif tool_name == "update_notion_page":
            page = await update_notion_page(
                user_id=user_id,
                db=db,
                page_id=arguments["page_id"],
                new_title=arguments.get("new_title"),
                append_content=arguments.get("append_content"),
            )
            if not page:
                return "Failed to update page. Check the page ID and ensure it's shared with the integration."
            return f"Page updated successfully!\n{json.dumps(page, indent=2)}"
        
        elif tool_name == "update_notion_block":
            result = await update_notion_block(
                user_id=user_id,
                db=db,
                block_id=arguments["block_id"],
                new_text=arguments["new_text"],
            )
            if not result:
                return "Failed to update block. Check the block ID and ensure the page is shared with the integration."
            return f"Block updated successfully!\n{json.dumps(result, indent=2)}"
        
        elif tool_name == "delete_notion_block":
            success = await delete_notion_block(
                user_id=user_id,
                db=db,
                block_id=arguments["block_id"],
            )
            if not success:
                return "Failed to delete block. Check the block ID and ensure the page is shared with the integration."
            return "Block deleted successfully!"
        
        elif tool_name == "delete_notion_page":
            success = await delete_notion_page(
                user_id=user_id,
                db=db,
                page_id=arguments["page_id"],
            )
            if not success:
                return "Failed to delete page. Check the page ID and ensure it's shared with the integration."
            return "Page archived successfully! (It can be restored from Notion's trash)"
        
        else:
            return f"Unknown tool: {tool_name}"
    
    except Exception as e:
        return f"Error executing {tool_name}: {str(e)}"


def get_optional_user_id(request: Request) -> str | None:
    """Get user ID from session if present, otherwise return None"""
    session_token = request.cookies.get("session")
    if not session_token:
        return None
    
    payload = verify_session_token(session_token)
    if not payload:
        return None
    
    return payload.get("user_id")


@router.post("", response_model=ChatResponse)
async def chat(
    request: Request,
    chat_request: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send a message to the digital twin chatbot"""
    try:
        user_id = get_optional_user_id(request)
        
        if not settings.openai_api_key:
            raise HTTPException(
                status_code=500, 
                detail="OpenAI API key not configured. Add OPENAI_API_KEY to your .env file."
            )
        
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        
        # Choose system prompt and tools based on whether user has connections
        has_connections = user_id is not None
        system_prompt = SYSTEM_PROMPT_WITH_TOOLS if has_connections else SYSTEM_PROMPT_NO_TOOLS
        tools_to_use = TOOLS if has_connections else None

        # Build messages with conversation history
        messages = [
            {
                "role": "system",
                "content": system_prompt.format(
                    current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ),
            },
        ]
        
        # Add conversation history (limit to last 20 messages to avoid token limits)
        for msg in chat_request.history[-20:]:
            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                if msg.tool_calls and len(msg.tool_calls) > 0:
                    # Reconstruct the assistant message with tool calls
                    tool_calls_for_api = [
                        {
                            "id": tc.id or f"call_{i}",
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            }
                        }
                        for i, tc in enumerate(msg.tool_calls)
                    ]
                    messages.append({
                        "role": "assistant",
                        "content": msg.content or None,
                        "tool_calls": tool_calls_for_api,
                    })
                    # Add tool results
                    for tc in msg.tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id or f"call_{msg.tool_calls.index(tc)}",
                            "content": tc.result,
                        })
                else:
                    messages.append({"role": "assistant", "content": msg.content})
        
        # Add current message
        messages.append({"role": "user", "content": chat_request.message})
        
        context_used = []
        tool_calls_log = []  # Track detailed tool calls
        
        # Initial completion
        completion_kwargs = {
            "model": "gpt-4o-mini",
            "messages": messages,
        }
        
        if tools_to_use:
            completion_kwargs["tools"] = tools_to_use
            completion_kwargs["tool_choice"] = "auto"
        
        response = await client.chat.completions.create(**completion_kwargs)
        assistant_message = response.choices[0].message
        
        # Handle tool calls if any (only possible if user has connections)
        while has_connections and assistant_message.tool_calls:
            messages.append(assistant_message)
            
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                
                context_used.append(tool_name)
                
                # Log the tool call
                print(f"[LLM] Calling tool: {tool_name}")
                print(f"[LLM]   Arguments: {json.dumps(arguments, indent=2)}")
                
                result = await execute_tool(tool_name, arguments, user_id, db)
                
                # Truncate result for logging (can be long)
                result_preview = result[:200] + "..." if len(result) > 200 else result
                print(f"[LLM]   Result: {result_preview}")
                
                # Store for response (include ID for history reconstruction)
                tool_calls_log.append(ToolCallInfo(
                    id=tool_call.id,
                    name=tool_name,
                    arguments=arguments,
                    result=result,
                ))
                
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
            
            # Get next response
            response = await client.chat.completions.create(**completion_kwargs)
            assistant_message = response.choices[0].message
        
        return ChatResponse(
            response=assistant_message.content or "I couldn't generate a response.",
            context_used=context_used,
            tool_calls=tool_calls_log,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error: {str(e)}"
        )

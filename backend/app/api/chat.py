"""Chat endpoint with LLM integration"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import verify_session_token
from app.models.user import User
from app.services.google_calendar import (
    get_events,
    create_event,
    update_event,
    add_attendees_to_event,
    delete_event,
)
from app.services.gmail import get_emails
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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_calendar_events",
            "description": "Get ALL calendar events in a date range. Returns every event - YOU must filter/analyze the results yourself. Time range is from 12:00 AM of start_date to 11:59 PM of end_date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to today)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to 7 days from start_date)",
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
            "description": "Search emails and return FULL THREADS. If ANY message in a thread matches the query, the ENTIRE conversation thread (all replies) is returned with full message bodies. Use specific, targeted queries (e.g., company name, person's name). Default date range is last 30 days.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query - use LEAST SPECIFIC form: strip domains ('Viven.ai' → 'Viven'), use core noun only ('technical round' → 'round'). Single word preferred. Empty string returns all.",
                    },
                    "start_date": {
                        "type": "string",
                        "description": "Start date in YYYY-MM-DD format (defaults to 30 days ago)",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "End date in YYYY-MM-DD format (defaults to today)",
                    },
                },
                "required": ["query"],
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
                        "description": "Search query - use LEAST SPECIFIC form: strip domains ('Viven.ai' → 'Viven'), use core noun only. Single word preferred. Empty = recent pages.",
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
- get_calendar_events returns ALL events in a date range - YOU must filter/analyze results
- get_emails returns FULL THREADS: if ANY message matches the query, the ENTIRE conversation (all replies) is returned with full message bodies
- Create, edit, share, and delete calendar events

NOTION (if connected):
- Search for pages
- Read page content
- Create new pages (as child of existing page)
- Update pages (change title, append content)
- Update or delete individual blocks
- Delete (archive) pages

=== HOW CALENDAR AND EMAIL RETRIEVAL WORKS ===

CALENDAR: get_calendar_events returns ALL events in the date range. YOU analyze the results.

EMAIL: get_emails returns FULL CONVERSATION THREADS. If any message in a thread matches the query, 
you get the ENTIRE thread with all replies and full message bodies. This means you can see the 
complete back-and-forth conversation without needing additional tool calls. Use SPECIFIC, TARGETED queries.

=== SMART FOLLOW-UP SEARCHES - CRITICAL ===

When you learn new information from one tool call, USE IT to make smarter subsequent calls.

GOOD Example: User asks "Tell me about my interview Monday"
1. get_calendar_events(start_date="2026-02-02", end_date="2026-02-02")
2. Find: "Interview with Amazon - Round 3" with attendees arsh@amazon.com
3. NOW search emails with what you learned:
   - get_emails(query="Amazon") ← Use the COMPANY NAME, not "interview"
   - This finds all Amazon correspondence, interview details, prep materials

BAD Example (what NOT to do):
1. get_calendar_events → Find "Interview with Amazon"
2. get_emails(query="interview") ← TOO GENERIC, returns unrelated job alerts
3. Report a bunch of irrelevant emails

RULES FOR ALL SEARCHES (Email, Calendar, Notion):
- Use the LEAST SPECIFIC, SIMPLEST form of search terms
- Strip domains, prefixes, suffixes: "Viven.ai" → search "Viven", "Amazon.com" → search "Amazon"
- Use only the core noun: "technical round" → search "round", "final interview" → search "interview"
- For names: "John Smith" → try "John" or "Smith" (single word)
- Do NOT use OR operators - they don't work correctly
- NEVER combine multiple words unless absolutely necessary
- Prefer entity names (company, person) over action words (meeting, project, call)
- If calendar event has attendees, extract the domain: "arsh@createbase.com" → search "createbase"
- Default date range is 30 days ago to today - widen if needed for historical context

EXAMPLES of query simplification:
- "Viven.ai interview" → search "Viven"
- "technical phone screen" → search "screen" or "phone"
- "Amazon final round" → search "Amazon"
- "meeting with John" → search "John"
- "Q1 planning session" → search "planning" or "Q1"

=== MISSING INFORMATION ===

To CREATE an event, you need: event name, date, and time. If any are missing, ask.
  Example: "Create a meeting called standup" → Ask: "When should I schedule this?"

To EDIT, SHARE, or DELETE an event, first get all events, find the one matching the user's description, 
then confirm with them before taking action (especially for DELETE - this is a CRITICAL action).

To SHARE an event, you need the person's email address.
  Example: "Invite John to my meeting" → Ask: "What is John's email address?"

If the user doesn't specify how long an event should be, FIRST ASK. If they don't answer, assume 1 hour.

=== CLARIFYING "MEETINGS" ===

When a user asks about "meetings", clarify:
  - Events with multiple attendees (actual meetings with other people)?
  - Or ALL calendar events (including personal reminders, blocks, etc.)?

=== NOTION GUIDELINES ===

Content formatting: Use PLAIN TEXT only. No markdown (**, #, -, etc.) - Notion won't render it.

Before updating a Notion page: ALWAYS call get_notion_page first to see current content.

"Add content to page" → Use update_notion_page with append_content
"Create a new page" → Use create_notion_page

=== COMPREHENSIVE QUERIES ===

When users ask for "information about X", "tell me about X", "prepare for X":
1. Get calendar events in relevant date range → find the specific event
2. Extract SPECIFIC info from the event (company name, attendee names, etc.)
3. Search emails using those SPECIFIC terms (not generic words)
4. Search Notion using specific terms
5. ONLY report information that is actually relevant to X

Example: "Tell me about my interview Monday"
1. get_calendar_events → Find "Amazon Round 3 Interview"
2. get_emails(query="Amazon") → Find Amazon-related emails only
3. search_notion(query="Amazon") → Find any Amazon notes
4. Report ONLY Amazon-related findings

DO NOT include unrelated emails/events just because they exist in the date range.

=== FORMATTING RESPONSES WITH SOURCES ===

ALWAYS include clickable markdown links to sources when available:

Calendar events have html_link → [Event Title](html_link)
Emails have id and thread_id → mention "Email from [sender] on [date]"
Notion pages have url → [Page Title](url)

Example response format:
"Your interview is scheduled for **Monday at 11am**:
- 📅 [Createbase Round 3 Interview](https://calendar.google.com/...)
- 👥 Attendees: arsh@createbase.com, ada@createbase.com

Related emails from Createbase:
- Email from Ada Chen on Jan 28: 'Looking forward to meeting you...'
- Email from HR on Jan 25: 'Interview confirmation...'"

Format dates/times in human-readable form (e.g., "Tomorrow at 3:00 PM").

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
                start_date=arguments.get("start_date", ""),
                end_date=arguments.get("end_date", ""),
            )
            if not events:
                return "No calendar events found in this date range."
            return json.dumps(events, indent=2)
        
        elif tool_name == "get_emails":
            emails = await get_emails(
                user_id=user_id,
                db=db,
                query=arguments.get("query", ""),
                start_date=arguments.get("start_date", ""),
                end_date=arguments.get("end_date", ""),
            )
            if not emails:
                query = arguments.get("query", "")
                return f"No email threads found matching '{query}'. Try a different search term or wider date range."
            return json.dumps(emails, indent=2)
        
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
    """Main chat endpoint"""
    try:
        user_id = get_optional_user_id(request)
        
        # === LOG: Request received ===
        print("\n" + "="*60)
        print("[CHAT] New request received")
        print(f"[CHAT] User ID: {user_id or 'anonymous'}")
        print(f"[CHAT] Message: {chat_request.message}")
        print(f"[CHAT] History length: {len(chat_request.history)} messages")
        
        if not settings.openai_api_key:
            print("[CHAT] ERROR: OpenAI API key not configured")
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")
        
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        
        has_connections = user_id is not None
        base_system_prompt = SYSTEM_PROMPT_WITH_TOOLS if has_connections else SYSTEM_PROMPT_NO_TOOLS
        tools_to_use = TOOLS if has_connections else None
        
        # Fetch user's name to exclude from searches
        user_name = None
        excluded_terms = []
        if user_id:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if user and user.name:
                user_name = user.name
                # Split name into parts (e.g., "Shreyas Rana" -> ["Shreyas", "Rana"])
                excluded_terms = [part.strip() for part in user_name.split() if len(part.strip()) > 1]
                print(f"[CHAT] User name: {user_name}")
                print(f"[CHAT] Excluded search terms: {excluded_terms}")
        
        # Dynamically add user's name exclusion to system prompt
        if excluded_terms:
            exclusion_note = f"""

=== USER IDENTITY - NEVER SEARCH FOR THESE TERMS ===

The current user's name is: {user_name}
NEVER use any of these terms in search queries (they return too many results):
{', '.join(f'"{term}"' for term in excluded_terms)}

Instead, search for OTHER entities: company names, other people's names, project names, etc.
"""
            system_prompt = base_system_prompt + exclusion_note
        else:
            system_prompt = base_system_prompt
        
        print(f"[CHAT] Has connections: {has_connections}")
        print(f"[CHAT] Tools available: {len(TOOLS) if tools_to_use else 0}")

        messages = [
            {
                "role": "system",
                "content": system_prompt.format(
                    current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ),
            },
        ]
        
        # Reconstruct conversation history
        for msg in chat_request.history[-20:]:
            if msg.role == "user":
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == "assistant":
                if msg.tool_calls and len(msg.tool_calls) > 0:
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
                    for tc in msg.tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id or f"call_{msg.tool_calls.index(tc)}",
                            "content": tc.result,
                        })
                else:
                    messages.append({"role": "assistant", "content": msg.content})
        
        messages.append({"role": "user", "content": chat_request.message})
        
        context_used = []
        tool_calls_log = []
        
        completion_kwargs = {
            "model": "gpt-5-nano",
            "messages": messages,
        }
        
        if tools_to_use:
            completion_kwargs["tools"] = tools_to_use
            completion_kwargs["tool_choice"] = "auto"
        
        # === LOG: Sending to LLM ===
        print(f"[LLM] Sending {len(messages)} messages to OpenAI...")
        
        response = await client.chat.completions.create(**completion_kwargs)
        assistant_message = response.choices[0].message
        
        # === LOG: Initial LLM response ===
        if assistant_message.tool_calls:
            print(f"[LLM] Response: {len(assistant_message.tool_calls)} tool call(s) requested")
        else:
            preview = (assistant_message.content or "")[:100]
            print(f"[LLM] Response: {preview}...")
        
        # Tool call loop
        iteration = 0
        while has_connections and assistant_message.tool_calls:
            iteration += 1
            print(f"\n[CHAIN] === Tool Call Iteration {iteration} ===")
            
            messages.append(assistant_message)
            
            for tool_call in assistant_message.tool_calls:
                tool_name = tool_call.function.name
                arguments = json.loads(tool_call.function.arguments)
                
                context_used.append(tool_name)
                
                print(f"[TOOL] Calling: {tool_name}")
                print(f"[TOOL] Args: {json.dumps(arguments)}")
                
                result = await execute_tool(tool_name, arguments, user_id, db)
                
                result_preview = result[:300] + "..." if len(result) > 300 else result
                print(f"[TOOL] Result preview: {result_preview}")
                
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
            
            print(f"[LLM] Sending tool results back to OpenAI...")
            response = await client.chat.completions.create(**completion_kwargs)
            assistant_message = response.choices[0].message
            
            if assistant_message.tool_calls:
                print(f"[LLM] Response: {len(assistant_message.tool_calls)} more tool call(s)")
            else:
                print(f"[LLM] Response: Final answer ready")
        
        # === LOG: Final response ===
        final_response = assistant_message.content or "I couldn't generate a response."
        print(f"\n[CHAT] === Final Response ===")
        print(f"[CHAT] Tools used: {context_used}")
        print(f"[CHAT] Response length: {len(final_response)} chars")
        print(f"[CHAT] Response preview: {final_response[:200]}...")
        print("="*60 + "\n")
        
        return ChatResponse(
            response=final_response,
            context_used=context_used,
            tool_calls=tool_calls_log,
        )
    
    except HTTPException:
        raise
    except Exception as e:
        print(f"[CHAT] ERROR: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

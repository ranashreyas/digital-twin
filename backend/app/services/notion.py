"""Notion API service wrapper"""

from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.notion_auth import get_valid_notion_token


NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def get_notion_headers(access_token: str) -> dict:
    """Get headers for Notion API requests"""
    return {
        "Authorization": f"Bearer {access_token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


async def search_pages(
    user_id: str,
    db: AsyncSession,
    query: str = "",
    max_results: int = 25,
) -> list[dict[str, Any]]:
    """
    Search for pages in the user's Notion workspace.
    
    Args:
        query: Search query (empty returns recent pages)
        max_results: Maximum number of results
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return []
    
    print(f"[Notion] Searching pages (query='{query}')")
    
    body: dict[str, Any] = {
        "page_size": min(max_results, 100),  # Notion max is 100
        "filter": {"property": "object", "value": "page"},  # Only pages
    }
    
    if query:
        body["query"] = query
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NOTION_API_BASE}/search",
            headers=get_notion_headers(access_token),
            json=body,
        )
    
    if response.status_code != 200:
        print(f"[Notion] API Error {response.status_code}: {response.text}")
        return []
    
    data = response.json()
    results = []
    
    print(f"[Notion] Found {len(data.get('results', []))} pages")
    
    for item in data.get("results", []):
        # Extract title
        title = "Untitled"
        props = item.get("properties", {})
        for prop_name, prop_value in props.items():
            if prop_value.get("type") == "title":
                title_arr = prop_value.get("title", [])
                if title_arr:
                    title = title_arr[0].get("plain_text", "Untitled")
                break
        
        results.append({
            "id": item.get("id"),
            "title": title,
            "url": item.get("url"),
            "created_time": item.get("created_time"),
            "last_edited_time": item.get("last_edited_time"),
            "parent_type": item.get("parent", {}).get("type"),
        })
    
    return results


async def get_page_content(
    user_id: str,
    db: AsyncSession,
    page_id: str,
) -> dict[str, Any] | None:
    """
    Get the content of a Notion page (blocks).
    
    Args:
        page_id: The ID of the page to retrieve
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return None
    
    print(f"[Notion] Getting page content for {page_id}")
    
    # First get the page metadata
    async with httpx.AsyncClient() as client:
        page_response = await client.get(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=get_notion_headers(access_token),
        )
    
    if page_response.status_code != 200:
        print(f"[Notion] API Error {page_response.status_code}: {page_response.text}")
        return None
    
    page_data = page_response.json()
    
    # Extract title
    title = "Untitled"
    props = page_data.get("properties", {})
    for prop_name, prop_value in props.items():
        if prop_value.get("type") == "title":
            title_arr = prop_value.get("title", [])
            if title_arr:
                title = title_arr[0].get("plain_text", "Untitled")
            break
    
    # Get page blocks (content)
    async with httpx.AsyncClient() as client:
        blocks_response = await client.get(
            f"{NOTION_API_BASE}/blocks/{page_id}/children",
            headers=get_notion_headers(access_token),
            params={"page_size": 100},
        )
    
    if blocks_response.status_code != 200:
        print(f"[Notion] Blocks API Error {blocks_response.status_code}: {blocks_response.text}")
        blocks = []
    else:
        blocks_data = blocks_response.json()
        blocks = []
        
        for block in blocks_data.get("results", []):
            block_type = block.get("type")
            block_content = block.get(block_type, {})
            
            # Extract text content from various block types
            text = ""
            if "rich_text" in block_content:
                text = " ".join([
                    t.get("plain_text", "") 
                    for t in block_content.get("rich_text", [])
                ])
            elif "text" in block_content:
                text = " ".join([
                    t.get("plain_text", "") 
                    for t in block_content.get("text", [])
                ])
            
            blocks.append({
                "id": block.get("id"),
                "type": block_type,
                "text": text,
                "has_children": block.get("has_children", False),
            })
    
    return {
        "id": page_id,
        "title": title,
        "url": page_data.get("url"),
        "created_time": page_data.get("created_time"),
        "last_edited_time": page_data.get("last_edited_time"),
        "content": blocks,
    }


async def create_page(
    user_id: str,
    db: AsyncSession,
    title: str,
    parent_page_id: str,
    content: str = "",
) -> dict[str, Any] | None:
    """
    Create a new Notion page as a child of another page.
    
    Args:
        title: The title of the new page
        parent_page_id: The ID of the parent page
        content: Optional text content to add to the page
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return None
    
    print(f"[Notion] Creating page '{title}' under parent {parent_page_id}")
    
    # Build the page body
    body: dict[str, Any] = {
        "parent": {"page_id": parent_page_id},
        "properties": {
            "title": {
                "title": [{"text": {"content": title}}]
            }
        },
    }
    
    # Add content blocks if provided
    if content:
        # Split content into paragraphs
        paragraphs = content.split("\n")
        children = []
        for para in paragraphs:
            if para.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para}}]
                    }
                })
        if children:
            body["children"] = children
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NOTION_API_BASE}/pages",
            headers=get_notion_headers(access_token),
            json=body,
        )
    
    if response.status_code != 200:
        print(f"[Notion] API Error {response.status_code}: {response.text}")
        return None
    
    page_data = response.json()
    print(f"[Notion] Page created successfully: {page_data.get('id')}")
    
    return {
        "id": page_data.get("id"),
        "title": title,
        "url": page_data.get("url"),
        "created_time": page_data.get("created_time"),
    }


async def update_page(
    user_id: str,
    db: AsyncSession,
    page_id: str,
    new_title: str | None = None,
    append_content: str | None = None,
) -> dict[str, Any] | None:
    """
    Update a Notion page - change title and/or append content.
    For modifying or deleting specific blocks, use update_block or delete_block.
    
    Args:
        page_id: The ID of the page to update
        new_title: New title for the page (optional)
        append_content: Text to append to the page (optional)
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return None
    
    print(f"[Notion] Updating page {page_id}")
    
    # Update title if provided
    if new_title:
        print(f"[Notion] Updating title to: '{new_title}'")
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{NOTION_API_BASE}/pages/{page_id}",
                headers=get_notion_headers(access_token),
                json={
                    "properties": {
                        "title": {
                            "title": [{"text": {"content": new_title}}]
                        }
                    }
                },
            )
        
        if response.status_code != 200:
            print(f"[Notion] Title update error {response.status_code}: {response.text}")
            return None
    
    # Append content if provided
    if append_content:
        print(f"[Notion] Appending content: '{append_content[:50]}...'")
        paragraphs = append_content.split("\n")
        children = []
        for para in paragraphs:
            if para.strip():
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": para}}]
                    }
                })
        
        if children:
            async with httpx.AsyncClient() as client:
                response = await client.patch(
                    f"{NOTION_API_BASE}/blocks/{page_id}/children",
                    headers=get_notion_headers(access_token),
                    json={"children": children},
                )
            
            if response.status_code != 200:
                print(f"[Notion] Content append error {response.status_code}: {response.text}")
                return None
    
    # Get the updated page
    return await get_page_content(user_id, db, page_id)


async def update_block(
    user_id: str,
    db: AsyncSession,
    block_id: str,
    new_text: str,
) -> dict[str, Any] | None:
    """
    Update the text content of a specific block.
    
    Args:
        block_id: The ID of the block to update
        new_text: The new text content for the block
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return None
    
    print(f"[Notion] Updating block {block_id}")
    
    # First, get the block to know its type
    async with httpx.AsyncClient() as client:
        get_response = await client.get(
            f"{NOTION_API_BASE}/blocks/{block_id}",
            headers=get_notion_headers(access_token),
        )
    
    if get_response.status_code != 200:
        print(f"[Notion] Failed to get block: {get_response.text}")
        return None
    
    block_data = get_response.json()
    block_type = block_data.get("type")
    
    # Build the update payload based on block type
    update_payload = {
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": new_text}}]
        }
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{NOTION_API_BASE}/blocks/{block_id}",
            headers=get_notion_headers(access_token),
            json=update_payload,
        )
    
    if response.status_code != 200:
        print(f"[Notion] Block update error {response.status_code}: {response.text}")
        return None
    
    updated_block = response.json()
    print(f"[Notion] Block {block_id} updated successfully")
    
    return {
        "id": block_id,
        "type": block_type,
        "new_text": new_text,
        "success": True,
    }


async def delete_block(
    user_id: str,
    db: AsyncSession,
    block_id: str,
) -> bool:
    """
    Delete a specific block from a Notion page.
    
    Args:
        block_id: The ID of the block to delete
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return False
    
    print(f"[Notion] Deleting block {block_id}")
    
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{NOTION_API_BASE}/blocks/{block_id}",
            headers=get_notion_headers(access_token),
        )
    
    if response.status_code != 200:
        print(f"[Notion] Block delete error {response.status_code}: {response.text}")
        return False
    
    print(f"[Notion] Block {block_id} deleted successfully")
    return True


async def delete_page(
    user_id: str,
    db: AsyncSession,
    page_id: str,
) -> bool:
    """
    Delete (archive) a Notion page.
    
    Args:
        page_id: The ID of the page to delete
    
    Note: Notion doesn't truly delete pages via API, it archives them.
    """
    access_token = await get_valid_notion_token(user_id, db)
    
    if not access_token:
        print("[Notion] No valid access token available")
        return False
    
    print(f"[Notion] Deleting (archiving) page {page_id}")
    
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=get_notion_headers(access_token),
            json={"archived": True},
        )
    
    if response.status_code != 200:
        print(f"[Notion] API Error {response.status_code}: {response.text}")
        return False
    
    print(f"[Notion] Page {page_id} archived successfully")
    return True

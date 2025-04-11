from typing import List, Dict, Any, Optional, Union
import httpx
import asyncio
from datetime import datetime
from collections import deque
import time
import notion.parsers as parsers
import logging


class NotionRateLimiter:
    def __init__(self, requests_per_second: int = 3):
        self.requests_per_second = requests_per_second
        self.request_times = deque(maxlen=requests_per_second)
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            current_time = time.time()

            # remove old timestamps
            while self.request_times and current_time - self.request_times[0] > 1:
                self.request_times.popleft()

            # If we've hit the limit, wait
            if len(self.request_times) >= self.requests_per_second:
                wait_time = 1 - (current_time - self.request_times[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            self.request_times.append(current_time)


class NotionClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.notion.com/v1"
        self.max_retries = 3
        self.rate_limiter = NotionRateLimiter()
        self.logger = logging.getLogger(__name__)
        self._block_handlers = {
            "paragraph": parsers._handle_paragraph,
            "heading_1": parsers._handle_heading,
            "heading_2": parsers._handle_heading,
            "heading_3": parsers._handle_heading,
            "bulleted_list_item": parsers._handle_list_item,
            "numbered_list_item": parsers._handle_numbered,
            "to_do": parsers._handle_to_do,
            "toggle": parsers._handle_toggle,
            "code": parsers._handle_code,
            "quote": parsers._handle_quote,
            "divider": lambda block: "----",
            "callout": parsers._handle_callout,
            "child_page": parsers._handle_child_page,
            "child_database": parsers._handle_child_database
        }

    def headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }
    
    async def _make_requests(self, method:str, url: str, **kwargs) -> Dict[str, Any]:
        await self.rate_limiter.acquire()

        async with httpx.AsyncClient() as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.request(
                        method,
                        url,
                        headers=self.headers(),
                        timeout=30.0,
                        **kwargs
                    )
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPError as e:
                    if attempt == self.max_retries-1:
                        raise
                    await asyncio.sleep(2 ** attempt)

    async def retrieve_page(self, page_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/pages/{page_id}"
        return await self._make_requests("GET", url)
    
    async def retrieve_database(self, database_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/databases/{database_id}"
        return await self._make_requests("GET", url)
    
    async def detect_resource_type(self, resource_id: str) -> str:
        """Detect the type of Notion resource (page, database, etc.)"""

        try:
            await self.retrieve_database(resource_id)
            return "database"
        except Exception:
            try:
                await self.retrieve_page(resource_id)
                return "page"
            except Exception as e:
                raise ValueError(f"Invalid Notion resource ID or insufficient permissions: {str(e)}")

    async def query_database(
            self,
            database_id: str,
            filter_params: Optional[Dict] = None,
            start_cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/databases/{database_id}/query"
        body = {}
        if filter_params:
            body["filter"] = filter_params
        if start_cursor:
            body["start_cursor"] = start_cursor

        return await self._make_requests("POST", url, json=body)
    
    async def get_resource_pages(self, resource_id: str) -> List[Dict]:
        """Get all page ids from a database or a single page."""
        try:
            resource_type = await self.detect_resource_type(resource_id)
            self.logger.info(f"Detected resource type: {resource_type}")
            if resource_type == "database":
                return await self.get_all_pages(resource_id)
            else:
                return await self.get_page(resource_id)
        except Exception as e:
            self.logger.error(f"Error getting pages from resource {resource_id}: {str(e)}")
            raise
    
    async def get_all_pages(self, database_id: str) -> List[Dict[str, Any]]:
        """Get all page ids from a database with pagination handling."""
        all_pages = []
        has_more = True
        next_cursor = None

        while has_more:
            response = await self.query_database(database_id, start_cursor=next_cursor)
            all_pages.extend(response["results"])
            has_more = response["has_more"]
            next_cursor = response.get("next_cursor")

        return all_pages
    
    async def get_page(self, page_id: str) -> List[Dict]:
        """Get the page details."""
        try:
            page = await self.retrieve_page(page_id)
            return [page]
        except Exception as e:
            self.logger.warning(f"Error retrieving page {page_id}: {str(e)}")
            raise
    
    async def get_page_content(self, page_id: str) -> str:
        """Extract text content from a page and return any child pages"""
        blocks = await self.get_block_children(page_id)
        result = await self._process_blocks(blocks["results"])
        return result
    
    async def get_block_children(self, block_id: str) -> List[Dict[str, Any]]:
        """Retrieve all child blocks of a given block."""
        url = f"{self.base_url}/blocks/{block_id}/children"
        return await self._make_requests("GET", url)
    
    async def _process_blocks(self, blocks: List[Dict]) -> Union[str, Dict]:
        """Process blocks and extract text content. Returns child_pages in separate array"""
        content = []
        child_pages = []
        
        async def process_block_children(block: Dict) -> None:
            """Helper function to process children of any block type"""
            if block.get("has_children"):
                try:
                    children = await self.get_block_children(block["id"])
                    child_result = await self._process_blocks(children["results"])

                    # Collect child pages from nested blocks
                    if isinstance(child_result, dict):
                        child_pages.extend(child_result["child_pages"])
                        if block["type"] != "child_page":
                            content.append(child_result["content"])
                except Exception as e:
                    self.logger.warning(f"Error processing children of block {block['id']}: {str(e)}")

        for block in blocks:
            try:
                block_type = block["type"]
                block_id = block["id"]
                parent = block.get("parent", {})
                parent_id = parent.get("page_id") or parent.get("database_id")

                # Generate block URL if we have a parent ID (page or database)
                block_url = None
                if parent_id:
                    # Format: https://www.notion.so/{workspace_name}/{page-id}?pvs=4#{block-id}
                    # We need to add a way to get workspace name dynamically or use parent ID
                    block_url = f"https://www.notion.so/post-office/{parent_id.replace('-', '')}?pvs=4#{block_id.replace('-', '')}"

                if block_type == "child_page":  #TODO handle child database
                    child_page_title = block.get(block_type, {}).get("title", "Untitled")
                    self.logger.debug(f"Found child page: ID={block['id']}, Title={child_page_title}")

                    page_url = f"https://www.notion.so/post-office/{block_id.replace('-', '')}?pvs=4"
                
                    child_page_data = {
                        "id": block["id"],
                        "type": "page",
                        "title": child_page_title,
                        "last_edited_time": block.get("last_edited_time"),
                        "created_time": block.get("created_time"),
                        "parent": block.get("parent"),
                        "url": page_url  
                    }

                    if "created_by" in block:
                        child_page_data["created_by"] = block["created_by"]
                    if "last_edited_by" in block:
                        child_page_data["last_edited_by"] = block["last_edited_by"]
                    
                    child_pages.append(child_page_data)
                    continue                    

                handler = self._block_handlers.get(block_type)
                if handler:
                    processed_text = handler(block)
                    # Check if the result is a coroutine (async function) and await it if necessary
                    if asyncio.iscoroutine(processed_text):
                        processed_text = await processed_text
                    if processed_text and block_url:
                        processed_text = f"{processed_text} [Block URL: {block_url}]"
                    if processed_text:
                        content.append(processed_text)

                await process_block_children(block)

            except Exception as e:
                self.logger.warning(f"Error processing block {block['id']}: {str(e)}. Skipping block.")
                continue

        return {
            "content": "\n".join(content) if content else "",
            "child_pages": child_pages
        }



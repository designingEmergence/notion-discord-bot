from typing import List, Dict, Any, Optional
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
    
    async def get_block_children(self, block_id: str) -> List[Dict[str, Any]]:
        """Retrieve all child blocks of a given block."""
        url = f"{self.base_url}/blocks/{block_id}/children"
        return await self._make_requests("GET", url)
    
    async def get_page_content(self, page_id: str) -> str:
        """Extract all text content from a page"""
        blocks = await self.get_block_children(page_id)
        return await self._process_blocks(blocks["results"])
    
    async def _process_blocks(self, blocks: List[Dict]) -> str:
        """Process blocks and extract text content."""
        content = []
        for block in blocks:
            try:
                block_type = block["type"]
                handler = self._block_handlers.get(block_type)
                if handler:
                    try:
                        if processed_text := handler(block):
                            content.append(processed_text)
                        self.logger.debug(f"Processed block type: {block_type}")
                    except Exception as e:
                        self.logger.warning(f"Error processing block type {block_type}: {str(e)}. Skipping block.")
                        continue
                else:
                    self.logger.debug(f"Unhandled block type: {block_type}")

                # Recursively process child blocks if they exist
                if block.get("has_children"):
                    try:
                        children = await self.get_block_children(block["id"])
                        if child_content := await self._process_blocks(children["results"]):
                            indented_content = "\n".join(f"    {line}" for line in child_content.split("\n"))
                            content.append(indented_content)

                    except Exception as e:
                        self.logger.warning(f"Error processing child blocks for block {block['id']}: {str(e)}. Skipping children")
                        continue
            except Exception as e:
                self.logger.warning(f"Error processing block {block['id']}: {str(e)}. Skipping block.")
                continue
        return "\n".join(content)

    
    async def get_all_pages(self, database_id: str) -> List[Dict[str, Any]]:
        """Get all pages from a database with pagination handling."""
        all_pages = []
        has_more = True
        next_cursor = None

        while has_more:
            response = await self.query_database(database_id, start_cursor=next_cursor)
            all_pages.extend(response["results"])
            has_more = response["has_more"]
            next_cursor = response.get("next_cursor")

        return all_pages
                
                    
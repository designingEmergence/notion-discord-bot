from typing import Dict, List, Any, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def get_page_title(page: Dict) -> str:
    """Extract title from Notion page"""
    title = ""
    try:
        if page.get("properties") and page["properties"].get("title"):
            title_property = page["properties"]["title"]
            if title_property.get("title"):
                title = "".join([t.get("plain_text", "") for t in title_property["title"]])
        elif page.get("title"):
            # Handle different title formats
            if isinstance(page["title"], list):
                title = "".join([t.get("plain_text", "") for t in page["title"]])
            elif isinstance(page["title"], dict) and page["title"].get("plain_text"):
                title = page["title"]["plain_text"]
            else:
                title = str(page["title"])
    except Exception as e:
        logger.error(f"Error extracting page title: {str(e)}")
    
    # Default title if none found
    return title or page.get("id", "Untitled")

def extract_page_metadata(page: Dict, resource_id: Optional[str] = None) -> Dict:
    """Extract metadata from Notion page"""
    last_edited_time = page.get("last_edited_time")
    
    # If still not found, use current time as last resort
    if not last_edited_time:
        logger.debug(f"No last_edited_time found for page {page.get('id', 'unknown')}, using current time")
        last_edited_time = datetime.now().isoformat()
    
    # Get page URL or construct it
    page_url = page.get("url", "")
    if not page_url and page.get("id"):
        page_url = f"https://www.notion.so/post-office/{page['id'].replace('-', '')}?pvs=4"

    metadata = {
        "id": page.get("id", ""),
        "title": get_page_title(page),
        "url": page_url,
        "source": "notion",
        "last_modified": last_edited_time,
        "created_time": page.get("created_time", ""),
    }
    
    # Add resource_id if provided
    if resource_id:
        metadata["resource_id"] = resource_id
        
    # Extract tags if available
    tags = []
    if page.get("properties"):
        for prop_name, prop_value in page["properties"].items():
            if prop_value.get("type") == "multi_select":
                for tag in prop_value.get("multi_select", []):
                    tags.append(tag.get("name", ""))
                    
    metadata["tags"] = tags if tags else None
    
    return metadata
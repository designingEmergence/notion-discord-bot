import logging
from typing import Dict

logger = logging.getLogger(__name__)

def get_page_title(page: Dict) -> str:
    """Extract title from a Notion page"""

    try:
        # For database pages - try Name property first
        if title := page.get('properties', {}).get('Name', {}).get('title', [{}])[0].get('plain_text'):
            return title

        # Try Title property for database pages
        if title := page.get('properties', {}).get('Title', {}).get('title', [{}])[0].get('plain_text'):
            return title

        # For standalone pages
        properties = page.get('properties', {})
        if 'title' in properties and isinstance(properties['title'], dict):
            title_array = properties['title'].get('title', [])
            if title_array and 'plain_text' in title_array[0]:
                return title_array[0]['plain_text']

        # Try icon + title combination
        icon = page.get('icon', {}).get('emoji', '')
        if title := page.get('properties', {}).get('title', [{}])[0].get('plain_text'):
            return f"{icon} {title}".strip()

        return "Untitled"

    except Exception as e:
        logger.warning(f"Error extracting title from page {page.get('id')}: {page.get('properties')}, Error: {str(e)}")
        return "Untitled"
    

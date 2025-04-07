from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

def _extract_rich_text(rich_text: List[Dict]) -> str:
    """Extract plain text from rich text objects."""
    return " ".join(text["plain_text"] for text in rich_text)

def _handle_paragraph(block: Dict) -> str:
    text = _extract_rich_text(block["paragraph"]["rich_text"])
    return text if text else ""

def _handle_heading(block: Dict) -> str:
    level = int(block["type"][-1])
    text = _extract_rich_text(block[block["type"]]["rich_text"])
    return f"{'#' * level} {text}" if text else ""

def _handle_list_item(block: Dict) -> str:
    text = _extract_rich_text(block["bulleted_list_item"]["rich_text"])
    return f"• {text}" if text else ""

def _handle_numbered(block: Dict) -> str:
    text = _extract_rich_text(block["numbered_list_item"]["rich_text"])
    return f"1. {text}" if text else ""

def _handle_to_do(block: Dict) -> str:
    text = _extract_rich_text(block["to_do"]["rich_text"])
    checked = "[x]" if block["to_do"]["is_checked"] else "[ ]"
    return f"[{checked}] {text}" if text else ""

def _handle_toggle(block: Dict) -> str:
    text = _extract_rich_text(block["toggle"]["rich_text"])
    return f"'>'{text}" if text else ""

def _handle_code(block: Dict) -> str:
    text = _extract_rich_text(block["code"]["rich_text"])
    language = block["code"].get("language", "")
    return f"```{language}\n{text}\n```" if text else ""

def _handle_quote(block: Dict) -> str:
    text = _extract_rich_text(block["quote"]["rich_text"])
    return f"> {text}" if text else ""

def _handle_callout(block: Dict) -> str:
    text = _extract_rich_text(block["callout"]["rich_text"])
    return f"```{text}```" if text else ""

async def _handle_child_page(block: Dict, notion_client=None) -> str:
    """Handle child_page blocks by recursively fetching their content"""
    if not notion_client:
        return f"## 📄 Child Page: {block.get('child_page', {}).get('title', 'Untitled')}"

    try:
        child_page_id = block["id"]
        child_page = await notion_client.retrieve_page(child_page_id)

        title = child_page.get('properties', {}).get('Name', {}).get('title', [{}])[0].get('plain_text', 'Untitled')
        if not title:
            title = child_page.get('properties', {}).get('title', [{}])[0].get('plain_text', 'Untitled')

        # Get the content of the child page
        child_content = await notion_client.get_page_content(child_page_id)

        return f"""## 📄 Child Page: {title}
---
{child_content}
---"""
    except Exception as e:
        logger.warning(f"Error processing child page {block.get('id')}: {str(e)}")
        return f"## 📄 Child Page: {block.get('child_page', {}).get('title', 'Untitled')} (content unavailable)"
    
async def _handle_child_database(block: Dict, notion_client=None) -> str:
    """Handle child_database blocks by fetching and formatting their content"""

    if not notion_client:
        return f"## 💾 Child Database: (content unavailable)"
    
    try: 
        database_id = block["id"]
        database = await notion_client.retrieve_database(database_id)
        database_pages = await notion_client.get_all_pages(database_id)

        title = database.get('title', [{}])[0].get('plain_text', 'Untitled Database')

        #Format database content
        content = [f"##💾 Database: {title}"]
        content.append("---")

        content.append(f"This database contains {len(database_pages)} pages.")
        content.append("---")

        return "\n".join(content)
    
    except Exception as e:
        logger.warning(f"Error processing child database {block.get('id')}: {str(e)}")
        return f"## 💾 Child Database: (content unavailable)"


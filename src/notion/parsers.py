from typing import List, Dict, Any, Optional

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


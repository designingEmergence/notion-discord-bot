import logging
from typing import List, Dict, Any, Optional, Tuple, Set, Union
from datetime import datetime

logger = logging.getLogger(__name__)

def convert_text_to_string(text: Any) -> str:
    """Convert any text input to a clean string format."""
    if isinstance(text, tuple):
        return "\n\n".join(str(item) for item in text if item)
    elif not isinstance(text, str):
        return str(text) if text is not None else ""
    return text.strip()

def convert_ids_to_string(id_value: Any) -> str:
    """Normalize document IDs to strings."""
    if isinstance(id_value, tuple):
        return str(id_value[0]) if id_value else ""
    return str(id_value)

def clean_metadata(metadata: Any) -> Dict[str, Any]:
    """Clean and normalize metadata dictionaries for ChromaDB compatibility"""
    if isinstance(metadata, tuple):
        if metadata and isinstance(metadata[0], dict):
            metadata_dict = metadata[0]
        else:
            metadata_dict = {"source": "unknown"}
    elif not isinstance(metadata, dict):
        metadata_dict = {"source": "unknown"}
    else:
        metadata_dict = metadata

    # Replace None values with empty strings and handle lists
    cleaned = {}
    for key, value in metadata_dict.items():
        if value is None:
            cleaned[key] = ""
        elif isinstance(value, list):
            cleaned[key] = ", ".join(str(v) for v in value)
        elif isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        else:
            cleaned[key] = str(value)
    
    return cleaned

def batch_process(items: List[Any], batch_size: int, process_fn: callable, description: str = "items") -> None:
    """Process items in batches for any given list of items and a function to process them with."""
    logger.info(f"Processing {len(items)} {description} in batches of {batch_size}")

    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        logger.debug(f"Processing batch {i//batch_size + 1}/{(len(items)-1)//batch_size + 1}")

        try:
            process_fn(batch)
            logger.debug(f"Processed batch {i//batch_size + 1} successfully")
        except Exception as e:
            logger.error(f"Error processing batch {i//batch_size + 1}: {str(e)}")
            raise

async def batch_process_async(
    items: List[Any],
    batch_size: int,
    process_fn: callable,
    description: str = "items",
    success_callback: Optional[callable] = None,
    continue_on_error: bool = False
) -> int:
    """Process items in async batches with tracking of succesful operations."""
    
    logger.info(f"Processing {len(items)} {description} in batches of {batch_size}")

    processed_count = 0
    batch_count = (len(items) - 1) // batch_size + 1

    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        batch_num = i//batch_size + 1

        logger.debug(f"Processing {description} batch {batch_num}/{batch_count}")

        try:
            await process_fn(batch)
            processed_count += len(batch)
            logger.debug(f"Processed {description} batch {batch_num} successfully")

            if success_callback:
                success_callback(batch)
        except Exception as e:
            logger.error(f"Error processing {description} batch {batch_num}: {str(e)}")
            if not continue_on_error:
                raise
            
    return processed_count

def map_chunks_by_parent(ids: List[str], metadatas: List[Dict]) -> Tuple[Dict[str, List[str]], Dict[str, Set[str]], Set[str]]:
    """Create mappings of chunks to parent documents."""
    chunk_parents = {}
    chunk_ids_by_parent = {}
    all_chunk_ids = set()

    for id, meta in zip(ids, metadatas):
        parent_id = meta.get("parent_id")
        if parent_id:
            #Track chunks by their parent document
            if parent_id not in chunk_parents:
                chunk_parents[parent_id] = []
                chunk_ids_by_parent[parent_id] = set()
            chunk_parents[parent_id].append(id)
            chunk_ids_by_parent[parent_id].add(id)
            all_chunk_ids.add(id)
    
    return chunk_parents, chunk_ids_by_parent, all_chunk_ids

def add_sync_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Add standard sync metadata to document metadata."""
    return {
        **metadata,
        "last_synced": datetime.now().isoformat()
    }
from typing import Optional, Dict, List
import logging
from notion.client import NotionClient
from rag.vectorstore import VectorStore
from notion.utils import get_page_title, extract_page_metadata


logger = logging.getLogger(__name__)


async def sync_notion_content(
    notion_client: NotionClient,
    vector_store: VectorStore,
    resource_id: str,
    progress_callback: Optional[callable] = None,
    test_mode: bool = False,
    max_pages: int = 2
) -> Dict[str, int]:
    """Sync Notion database content to vector store"""
    
    try:
        resource_type, pages = await _get_notion_page_ids(notion_client, resource_id, progress_callback)

        await _update_initial_progress(progress_callback, pages, resource_type)

        if test_mode:
            pages = pages[:max_pages]
            logger.info(f"Test mode enabled, max pages = {max_pages}")
        
        logger.info(f"Processing {len(pages)} pages for sync to vector store...")

        all_texts = []
        all_metadatas = []
        all_ids = []
        
        # Track document IDs to avoid duplicates within the same sync batch
        seen_doc_ids = set()

        for i, page in enumerate(pages, 1):
            try:
                logger.info(f"Processing page {page['id']}...")

                texts, ids, metadatas = await _process_page_content(notion_client, page, resource_id)

                # Filter out duplicates within this sync batch
                filtered_texts = []
                filtered_ids = []
                filtered_metadatas = []
                
                for text, doc_id, metadata in zip(texts, ids, metadatas):
                    if doc_id in seen_doc_ids:
                        logger.warning(f"Skipping duplicate document ID: {doc_id}")
                        continue
                    
                    seen_doc_ids.add(doc_id)
                    filtered_texts.append(text)
                    filtered_ids.append(doc_id)
                    filtered_metadatas.append(metadata)
                
                all_texts.extend(filtered_texts)
                all_metadatas.extend(filtered_metadatas)
                all_ids.extend(filtered_ids)      

                if progress_callback and i % 10 == 0:
                    await progress_callback(f"📑 Processed {i}/{len(pages)} pages...")

            except Exception as e:
                logger.error(f"Error processing page {page['id']}: {str(e)[:50]}...")
                continue

        if not all_texts:
            logger.error("No valid documents found to sync")
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}

        # Sync documents
        logger.debug(f"Total docs to sync to vector store: {len(all_ids)}")

        sync_results = await vector_store.sync_documents(
            ids=all_ids,
            texts=all_texts,
            metadatas=all_metadatas
        )
        return sync_results
    
    except Exception as e:
        logger.error(f"Error syncing Notion content: {str(e)[:100]}...")
        raise

async def _get_notion_page_ids(
        notion_client: NotionClient,
        resource_id: str,
        progress_callback: Optional[callable]
) -> tuple[str, List]:
    """Validate resources and return page ids from Notion resource"""

    if not resource_id:
        raise ValueError("Resource ID is required for syncing Notion content")
    
    resource_type = await notion_client.detect_resource_type(resource_id)
    if resource_type not in ["database", "page"]:
        raise ValueError(f"Invalid resource type: {resource_type}")
    
    if progress_callback:
        await progress_callback(f"🔄 Starting sync from {resource_id}...") #TODO change to name?

    pages = await notion_client.get_resource_pages(resource_id)
    
    return resource_type, pages

async def _update_initial_progress(
        progress_callback: callable,
        pages: List,
        resource_type: str
) -> None:
    """Update progress with initial sync information"""
    resource_name = "Untitled"
    if pages:
        resource_name = get_page_title(pages[0])

    if progress_callback:
        await progress_callback(
            f"🔑 Notion Resource Type: {resource_type.capitalize()}\n" +
            (f"📚 Pages in Database: {len(pages)}\n" if resource_type == "database" else "") +
            f"🪪 Resource Name: {resource_name}\n" +
            "🔄 Syncing..."
        )

async def _process_page_content(
    notion_client: NotionClient,
    page: Dict,
    resource_id: str
) -> tuple[List, List, List]:
    """Process a single page including child pages"""

    texts = []
    metadatas = []
    ids = []

    result = await notion_client.get_page_content(page["id"])
    content = result["content"]
    child_pages = result["child_pages"]

    # Ensure content is always a string
    if isinstance(content, tuple):
        content = "\n\n".join(str(item) for item in content if item)
    elif not isinstance(content, str):
        content = str(content) if content is not None else ""

    content = content.strip()
    if not content:
        return texts, ids, metadatas
    
    title = get_page_title(page)
    try:
        metadata = extract_page_metadata(page, resource_id=resource_id)
        combined_content = f"""Title: {title}
Tags: {metadata.get("tags", "None")}
Content: 
{content}"""
        
        texts.append(combined_content)
        # Ensure ID is a string
        page_id = str(page["id"]) if page["id"] else ""
        ids.append(f"notion_{page_id}")
        metadatas.append(metadata)

        # Process child pages separately
        for child_page in child_pages:
            try:
                child_result = await notion_client.get_page_content(child_page["id"])
                child_content = child_result["content"]
                
                # Ensure child_content is always a string
                if isinstance(child_content, tuple):
                    child_content = "\n\n".join(str(item) for item in child_content if item)
                elif not isinstance(child_content, str):
                    child_content = str(child_content) if child_content is not None else ""
                
                child_content = child_content.strip()
                if not child_content:
                    logger.warning(f"Empty content for child page {child_page['id']}, skipping")
                    continue
                
                child_metadata = extract_page_metadata(child_page, resource_id=resource_id)
                child_metadata["parent_id"] = page["id"]

                child_combined_content = f"""Title: {child_metadata['title']}
Tags: {child_metadata.get('tags', 'None')}
Content: 
{child_content}"""
                
                texts.append(child_combined_content)
                # Ensure child ID is a string
                child_id = str(child_page["id"]) if child_page["id"] else ""
                ids.append(f"notion_{child_id}")
                metadatas.append(child_metadata)
            except Exception as e:
                logger.error(f"Error processing child page {child_page['id']}: {str(e)}")
                # Continue processing other child pages even if there's an error with one
    
    except Exception as e:
        logger.error(f"Error processing page {page['id']}: {str(e)}")
        # Continue execution even if there's an error with metadata extraction
    
    return texts, ids, metadatas
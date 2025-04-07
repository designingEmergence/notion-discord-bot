from typing import Optional, Dict, List
import logging
from notion.client import NotionClient
from rag.vectorstore import VectorStore
from notion.utils import get_page_title


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

    if not resource_id:
        raise ValueError("Resource ID is required for syncing Notion content")
    
    try:
        resource_type = await notion_client.detect_resource_type(resource_id) #TODO move this to utils
        if resource_type not in ["database", "page"]:
            raise ValueError(f"Invalid resource type: {resource_type}")

        if progress_callback:
            try:
                await progress_callback(f"🔄 Starting sync from {resource_id}...")
            except Exception as e:
                logger.error(f"Error in progress callback: {str(e)}")
        
        # Get all pages ids from Notion
        logger.info(f"Fetching pages for resource {resource_id} (type: {resource_type})")
        pages = await notion_client.get_resource_pages(resource_id)
        logger.debug(f"page structure returned for get_resource_pages: {pages[0]}")  

        if progress_callback:
            logger.debug(f"Total pages found:  {len(pages)}")
            resource_name = "Untitled"
            if pages:
                first_page = pages[0]
                resource_name = get_page_title(first_page)

            await progress_callback(
                f"🔑 Notion Resource Type: {resource_type.capitalize()}\n" +
                (f"📚 Pages in Database: {len(pages)}\n" if resource_type == "database" else "") +
                f"🪪 Resource Name: {resource_name}\n" +
                "🔄 Syncing..."
            ) 

        page_info = [
            {
                'id': page.get('id'),
                'title': get_page_title(page),
                'parent': page.get('parent', {})
            }
            for page in pages
        ]     
        logger.debug(f"Retrieved pages structure: {page_info}")

        if test_mode:
            pages = pages[:max_pages]
            logger.info(f"Test mode enabled, syncing {len(pages)} pages...")
        
        # Extract text content from pages
        texts = []
        metadatas = []
        ids = []

        for i, page in enumerate(pages, 1):
            try:
                content = await notion_client.get_page_content(page["id"])

                if isinstance(content, tuple):
                    content = "\n\n".join(str(item) for item in content if item)
                elif not isinstance(content, str):
                    logger.warning(f"Content for page {page['id']} is not a string, converting...")
                    content = str(content) if content is not None else ""

                content = content.strip()

                if not content:
                    logger.warning(f"Empty content for page {page['id']}, skipping...")
                    continue
                
                # Log a preview of the content for debugging
                #content_preview = content[:50] + "..." if len(content) > 50 else content
                #logger.debug(f"Content preview for page {page['id']}: {content_preview}")           
                
                page_id = page.get("id")
                if not page_id:
                    logger.warning(f"Page ID not found for page {page}, skipping...")
                    continue
                
                last_modified = page.get("last_edited_time", "")

                title = ""
                try:
                    title_property = page.get("properties", {}).get("Name", {})
                    if title_property and title_property.get("type") == "title":
                        title_array = title_property.get("title", [])
                        if title_array:
                            title = title_array[0].get("text", {}).get("content", "")

                    if not title:
                        if page.get("parent", {}).get("type") == "page_id":
                            title = page.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "")
                        else:
                            title = (
                                page.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "") or
                                page.get("properties", {}).get("Title", {}).get("title", [{}])[0].get("plain_text", "") or
                                page.get("icon", {}).get("emoji", "") + " " + page.get("properties", {}).get("title", {}).get("title", [{}])[0].get("plain_text", "")
                            ).strip()

                    if not title:
                        logger.warning(f"Could not extract title for page {page['id']}")

                except Exception as e:
                    logger.error(f"Error extracting title from page: {str(e)}")
                    title = f"Untitled Page ({page['id']})"
                
                tags = []
                try:
                    tag_property = page.get("properties", {}).get("Tags", {})
                    if tag_property and tag_property.get("type") == "multi_select":
                        tags = [tag["name"] for tag in tag_property.get("multi_select", [])]
                except Exception as e:
                    logger.error(f"Error extracting tags from page: {str(e)}")

                metadata = {
                    "page_id": page["id"],
                    "title": title,
                    "last_modified": last_modified,
                    "url": page.get("url", ""),
                    "tags": ", ".join(tags) if tags else "",
                    "public_url": page.get("public_url", ""),
                    "created_time": page.get("created_time", ""),
                }

                properties = page.get("properties", {})
                if isinstance(properties, dict):
                    #Extract Link
                    link_prop = properties.get("Link", {})
                    if link_prop and isinstance(link_prop, dict):
                        metadata["link"] = link_prop.get("url")
                
                    rating_prop = properties.get("Star Rating", {})
                    if rating_prop and isinstance(rating_prop, dict):
                        select_prop = rating_prop.get("select")
                        if select_prop and isinstance(select_prop, dict):
                            metadata["rating"] = select_prop.get("name")
                
                combined_content = f"""Title: {title}
Tags: {' '.join(tags) if tags else 'None'}
Content: 
{content}"""
                
                # Debug log the final combined content length
                logger.debug(f"Combined content length: {len(combined_content)}")
                #logger.debug(f"Combined content preview: {combined_content[:200]}...")
                
                texts.append(combined_content)
                ids.append(f"notion_{page['id']}")
                metadatas.append(metadata)

                if progress_callback and i % 10 == 0:
                    await progress_callback(f"📑 Processed {i}/{len(pages)} pages...")

            except Exception as e:
                error_msg = str(e)
                preview_length = 50 
                if len(error_msg) > preview_length:
                    error_msg = f"{error_msg[:preview_length]}..."
                logger.error(f"Error processing page {page['id']}: {error_msg}")
                continue

        if not texts:
            logger.error("No valid documents found to sync")
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}
        
        if isinstance(ids, tuple):
            ids = list(ids)
        
        if isinstance(metadatas, tuple):
            metadatas = list(metadatas)

        logger.debug(f"Initial lengths - texts: {len(texts)}, ids: {len(ids)}, metadatas: {len(metadatas)}")
        
        final_texts = []
        final_ids = []
        final_metadatas = []

        for text, id, metadata in zip(texts, ids, metadatas):
            if isinstance(text, (str, bytes)):
                text = text.strip()
                if text:
                    final_texts.append(str(text).strip())
                    final_ids.append(id)
                    final_metadatas.append(metadata)
            else:
                logger.warning(f"Skipping invalid content type {type(text)} for ID {id}")
    
        # Debug log before syncing
        logger.debug(f"Final lengths - texts: {len(final_texts)}, ids: {len(final_ids)}, metadatas: {len(final_metadatas)}")
        #logger.debug(f"Sample document content: {texts[0][:100] if texts and len(texts) > 0 else 'No documents'}")

        if final_texts:
            logger.debug(f"First document type: {type(final_texts[0])}")
            #logger.debug(f"First document preview: {final_texts[0][:100]}")
        else:
            logger.error("No valid documents found to sync")
            return {"added": 0, "updated": 0, "deleted": 0, "total": 0}  

        # Sync documents
        sync_results = await vector_store.sync_documents(
            ids=final_ids,
            texts=final_texts,
            metadatas=final_metadatas
        )

        sync_results["total"] = len(pages)
        return sync_results
    
    except Exception as e:
        error_preview = str(e)[:100] + "..." if len(str(e)) > 100 else str(e)
        logger.error(f"Error syncing Notion content: {error_preview}")
        raise

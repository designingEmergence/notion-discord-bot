import asyncio
import os
import sys
# from dotenv import load_dotenv
from notion.client import NotionClient
from rag.vectorstore import VectorStore
from notion.sync import sync_notion_content
import logging

logging.basicConfig(
    level=logging.INFO,  # Change to INFO for production
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('sync.log')
    ]
)
logger = logging.getLogger(__name__)


if "NOTION_RESOURCE_ID" in os.environ:
            del os.environ["NOTION_RESOURCE_ID"]
            load_dotenv(override=True)  # Add override=True
logger = logging.getLogger(__name__)

async def main():
    # load_dotenv()

    notion_client = NotionClient(api_key=os.getenv("NOTION_TOKEN"))
    vector_store = VectorStore()

    try:
        async def print_progress(message: str):
            logger.info(message)

        results = await sync_notion_content(
            notion_client=notion_client,
            vector_store=vector_store,
            resource_id=os.getenv("NOTION_RESOURCE_ID"),
            progress_callback=print_progress,
            test_mode=False,
            max_pages=4
        )

        logger.info(
            f"\n✅ Sync completed!\n"
            f"📝 Added: {results['added']} pages\n"
            f"🔄 Updated: {results['updated']} pages\n"
            f"🗑️ Deleted: {results['deleted']} pages\n"
            f"📚 Total pages: {results['total']}"
        )
    finally:
        logger.info("Closing vector store...")

if __name__ == "__main__":
    asyncio.run(main())
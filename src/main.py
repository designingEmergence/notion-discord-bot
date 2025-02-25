import asyncio
import logging
import os
import sys
from dotenv import load_dotenv
from bot.bot import NotionBot

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change to DEBUG
    format='%(levelname)s: %(message)s',  # Simplified format
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

def validate_env():
    """Validate required environment variables"""
    required_vars = [
        'DISCORD_TOKEN',
        'NOTION_TOKEN',
        'NOTION_RESOURCE_ID',
        'OPENAI_API_KEY',
        'ADMIN_IDS'
    ]
    
    for var in required_vars:
        value = os.getenv(var)
        logger.debug(f"{var}: {value}")  # Debug log each value
        if not value:
            raise ValueError(f"Missing {var}")

async def main():
    # Load environment variables
    load_dotenv(verbose=True)  # Add verbose flag
    
    try:
        validate_env()
        token = os.getenv('DISCORD_TOKEN')
        logger.debug(f"Token loaded: {token}")  # Debug log token
        
        bot = NotionBot()
        logger.info("Starting Notion Discord Bot...")
        await bot.start(token)
        
    except Exception as e:
        logger.error(f"Error running bot: {str(e)}", exc_info=True)  # Add full traceback
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
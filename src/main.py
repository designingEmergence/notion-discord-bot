import asyncio
import logging
import os
import sys
# from dotenv import load_dotenv
from bot.bot import NotionBot
from aiohttp import web

app = web.Application()
routes = web.RouteTableDef()

@routes.get("/")
async def hello(request):
    return web.Response(text="Bot is running!")

app.add_routes(routes)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG
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
    # load_dotenv(verbose=True)  # Add verbose flag
    
    try:
        validate_env()
        token = os.getenv('DISCORD_TOKEN')
        logger.debug(f"Token loaded: {token}")  # Debug log token
        
        bot = NotionBot()

        # Start both the bot and web server
        web_task = web.TCPSite(
            runner=web.AppRunner(app),
            host='0.0.0.0',
            port=int(os.getenv("PORT", 8080))
        ).start()

        logger.info("Starting Notion Discord Bot...")
        
        await asyncio.gather(
            web_task,
            bot.start(token)
        )
        
    except Exception as e:
        logger.error(f"Error running bot: {str(e)}", exc_info=True)  # Add full traceback
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
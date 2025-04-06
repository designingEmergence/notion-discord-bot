import asyncio
import logging
import os
import sys
# from dotenv import load_dotenv
from bot.bot import NotionBot
from aiohttp import web
import argparse

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

def parse_args():
    parser = argparse.ArgumentParser(description='Notion Discord Bot')
    parser.add_argument('--use-public-db', action='store_true',
                        help= 'Use public railway database URL instead of private URL')
    return parser.parse_args()

async def main():    
    try:
        args = parse_args()
        validate_env()
        token = os.getenv('DISCORD_TOKEN')
        logger.debug(f"Token loaded: {token}")  # Debug log token
        
        bot = NotionBot(use_public_db=args.use_public_db)

        runner = web.AppRunner(app)
        await runner.setup()

        # Start both the bot and web server
        site = web.TCPSite(
            runner=runner,
            host='0.0.0.0',
            port=int(os.getenv("PORT", 8080))
        )

        logger.info("Starting Notion Discord Bot...")

        await asyncio.gather(
            site.start(),
            bot.start(token)
        )
        
    except Exception as e:
        logger.error(f"Error running bot: {str(e)}", exc_info=True)  # Add full traceback
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
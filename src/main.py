import asyncio
import logging
import os
import sys
import asyncpg
from dotenv import load_dotenv
from bot.bot import NotionBot
from aiohttp import web

app = web.Application()
routes = web.RouteTableDef()

@routes.get("/")
async def hello(request):
    return web.Response(text="Bot is running!")

app.add_routes(routes)

class LogFilter(logging.Filter):
    def filter(self, record):
        return not (
            'WebSocket' in record.msg or 
            'discord.gateway' in record.name or
            'Keeping gateway' in record.msg or
            'Shard ID' in record.msg
        )

# Configure logging
logging.basicConfig(
    level=logging.INFO,  # Change from DEBUG to INFO for production
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',  # More detailed format
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log')
    ]
)
# Add filter to discord logger specifically
discord_logger = logging.getLogger("discord")
discord_logger.addFilter(LogFilter())

# Set higher log levels for HTTP libraries to suppress connection logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("discord").setLevel(logging.INFO)
logging.getLogger("chromadb").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.addFilter(LogFilter())


async def wait_for_database(max_retries: int = 10, delay_seconds: int = 3) -> None:
    """Wait for PostgreSQL to accept connections before booting the bot."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("Missing DATABASE_URL")

    for attempt in range(1, max_retries + 1):
        try:
            conn = await asyncpg.connect(database_url)
            await conn.close()
            logger.info("Database is ready")
            return
        except Exception as e:
            if attempt == max_retries:
                raise RuntimeError("Database did not become ready in time") from e
            logger.warning(
                "Database not ready (attempt %s/%s): %s. Retrying in %ss...",
                attempt,
                max_retries,
                e,
                delay_seconds,
            )
            await asyncio.sleep(delay_seconds)

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
    try:
        load_dotenv()
        validate_env()
        await wait_for_database()

        token = os.getenv('DISCORD_TOKEN')
        logger.debug(f"Token loaded: {token}")  # Debug log token
        
        bot = NotionBot()

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
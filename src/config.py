import os
import asyncpg
import logging
import json
from typing import Any, Dict
import urllib

class ConfigManager:
    DEFAULT_CONFIG = {
        "welcome_message": "Hello! Ask me anything about your Notion content!",
        "system_prompt": "You are a helpful assistant answering questions based on the provided context.",
        "similarity_threshold": 0.7,
        "max_history": 3,
        "chunk_size": 2000,
        "message_history_limit": 3,
        "max_content_chars": 12000,
        "max_tokens": 3000,
        "num_retrieved_results": 5,
        "llm_model": "gpt-5-mini",
        "embedding_model": "text-embedding-3-small"
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            raise ValueError("DATABASE_URL environment variable not set")

        self.logger.debug("Using configured database URL")
        self.logger.debug(f"Database URL format check: postgresql://user:***@host:port/dbname")
        parsed_url = urllib.parse.urlparse(self.db_url)
        self.logger.debug(f"URL: {self.db_url}")
        self.logger.debug(f"Host: {parsed_url.hostname}")
        self.logger.debug(f"Port: {parsed_url.port}")
        self.logger.debug(f"Database: {parsed_url.path[1:]}")  # Remove leading /
        
    async def init_db(self):
        """Initialize database table"""
        try:
            conn = await asyncpg.connect(self.db_url)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bot_config (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL
                )
            ''')
            await conn.close()
        except Exception as e:
            self.logger.error(f"Error initializing database: {e}")
            raise
    
    async def get(self, key:str) -> Any:
        """Get configuration value and convert to appropriate type"""
        try:
            conn = await asyncpg.connect(self.db_url)
            row = await conn.fetchrow(
                'SELECT value FROM bot_config WHERE key = $1',
                key
            )
            await conn.close()

            value = row['value'] if row else self.DEFAULT_CONFIG.get(key)

            if value is None:
                raise ValueError(f"Configuration key '{key}' not found")
            
            default_value = self.DEFAULT_CONFIG.get(key)
            if default_value is not None:
                if isinstance(default_value, bool):
                    return str(value).lower() == 'true'
                elif isinstance(default_value, (int, float)):
                    return type(default_value)(value)
                elif isinstance(default_value, str) and isinstance(value, str):
                    normalized = value.strip()
                    if (
                        len(normalized) >= 2
                        and normalized[0] == normalized[-1]
                        and normalized[0] in {'"', "'"}
                    ):
                        return normalized[1:-1]
                    return normalized
                
            return value

        except Exception as e:
            self.logger.error(f"Error getting config: {e}")
            # Return default value if there's an error
            return self.DEFAULT_CONFIG.get(key)
    
    async def get_all(self) -> Dict[str, Any]:
        """Get all configuration values"""
        try:
            conn = await asyncpg.connect(self.db_url)
            rows = await conn.fetch('SELECT key, value FROM bot_config')
            await conn.close()

            all_configs = self.DEFAULT_CONFIG.copy()
            for row in rows:
                all_configs[row['key']] = row['value']
            return all_configs
        except Exception as e:
            self.logger.error(f"Error getting all configs: {e}")
            return self.DEFAULT_CONFIG
        
    async def set(self, key:str, value:Any) -> None:
        """Set configuration value"""
        if key not in self.DEFAULT_CONFIG:
            raise ValueError(f"Invalid configuration key: {key}")

        try:
            conn = await asyncpg.connect(self.db_url)
            await conn.execute('''
                INSERT INTO bot_config (key, value)
                VALUES ($1, $2::jsonb)
                ON CONFLICT (key) DO UPDATE SET value = $2::jsonb
            ''', key, json.dumps(value))
            await conn.close()
        except Exception as e:
            self.logger.error(f"Error setting config: {e}")
            raise
    
    async def reset(self, key:str = None) -> None:
        """Reset configuration to default"""
        try:
            conn = await asyncpg.connect(self.db_url)
            if key:
                if key not in self.DEFAULT_CONFIG:
                    raise ValueError(f"Invalid config key: {key}")
                await conn.execute('DELETE FROM bot_config WHERE key = $1', key)
            else:
                await conn.execute('DELETE FROM bot_config')
            await conn.close()
        except Exception as e:
            self.logger.error(f"Error resetting config: {e}")
            raise
    
    

                
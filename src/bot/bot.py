import discord
from discord import app_commands
from discord.ext import commands
import os
from notion.client import NotionClient
from notion.sync import sync_notion_content
from rag.vectorstore import VectorStore
from rag.retriever import Retriever
from config import ConfigManager
from openai import AsyncOpenAI
import numpy as np
from functools import wraps
from typing import List, Dict, Any, Optional, Callable
import logging
import chromadb


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        admin_ids = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]
        is_admin = interaction.user.id in admin_ids
        if not is_admin:
            await interaction.response.send_message("❌ You do not have permission to run this command.", ephemeral=True)
        return is_admin
    return app_commands.check(predicate)

class NotionBot(commands.Bot):
    def __init__(self, use_public_db: bool = False):
        
        # Validate OpenAI API key is present
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY environment variable is not set")

        self.logger = logging.getLogger(__name__)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds= True
        super().__init__(command_prefix="!", intents=intents)

        self.notion_client = NotionClient(api_key=os.getenv("NOTION_TOKEN"))
        self.vector_stores = {}
        self.default_collection = os.getenv("COLLECTION_NAME", "notion_docs")
        self.openai_client = AsyncOpenAI()
        self.config = ConfigManager(use_public_db=use_public_db)

        #Register discord commands

        #Sync command
        @self.tree.command(
            name="sync", 
            description="Sync Notion content to vector store"
        )
        @admin_only()
        @app_commands.describe(
            resource_id="Optional: Notion resource ID to sync (defaults to configured ID)",
            collection_name="Optional: Collection name for vector store (defaults to 'notion_docs')"
        )
        async def sync(
            interaction: discord.Interaction,
            resource_id: Optional[str] = None,
            collection_name: Optional[str] = None
        ):
            await sync_notion(interaction, self, resource_id, collection_name)
        
        #Set collection command
        @self.tree.command(
            name="set_collection",
            description="Set active collection for queries"
        )
        @admin_only()
        @app_commands.describe(
            collection_name="Name of the collection to use for queries"
        )
        async def set_collection(
            interaction: discord.Interaction,
            collection_name: str
        ):
            if collection_name not in self.vector_stores:
                await interaction.response.send_message(
                    f"❌ Collection '{collection_name}' not found. Available collections: {', '.join(self.vector_stores.keys())}", ephemeral=True
                )
                return
            self.vector_store = self.vector_stores[collection_name]
            self.retriever = Retriever(vector_store=self.vector_store)
            await interaction.response.send_message(f"✅ Active collection set to '{collection_name}'")

        #Get current collection command
        @self.tree.command(
            name="get_collection",
            description="Get active collection for queries"
        )
        @admin_only()
        async def get_collection(
            interaction: discord.Interaction
        ):
            await interaction.response.send_message(
                 f"📚 Currently active collection: `{self.vector_store.collection_name}`\n"
                f"Available collections: {', '.join(f'`{name}`' for name in self.vector_stores.keys())}"
            )
        
        #Get configuration value(s)
        @self.tree.command(
            name="get_config",
            description = "Get configuration value(s). Shows all configs if no key specified."
        )
        @admin_only()
        @app_commands.describe(key="Configuration key name to get (optional)")
        async def get_config(
            interaction: discord.Interaction,
            key: Optional[str] = None
        ):
            await interaction.response.defer()
            try:
                if key:
                    value = await self.config.get(key)
                    await interaction.followup.send(
                        f"📝 Config `{key}` = `{value}`"
                    )
                else:
                    # get all config values
                    all_configs = await self.config.get_all()
                    config_values = [f"`{k}` = `{v}`" for k, v in all_configs.items()]

                    # Split into chunks to avoid Discord's 2000 character limit
                    header = "📝 Current Configuration:\n"
                    chunks = [header]
                    current_chunk = header

                    for i, config_line in enumerate(config_values):
                        formatted_line = f"\n`{config_line.split('`')[1]}` = `{config_line.split('`')[3]}`"

                        if len(current_chunk) + len(formatted_line) > 1900:
                            chunks.append(current_chunk)
                            current_chunk = f"📝 Configuration (continued):{formatted_line}"
                        else:
                            current_chunk += formatted_line

                    if current_chunk and current_chunk not in chunks:
                        chunks.append(current_chunk)

                    for chunk in chunks:
                        try:
                            await interaction.followup.send(chunk)
                        except Exception as e:
                            self.logger.error(f"Error sending config chunk: {e}")
                            await interaction.followup.send(
                                f"❌ Error displaying all configuration values: {str(e)}",
                                ephemeral=True
                            )
                            break
                        
            except ValueError as e:
                await interaction.followup.send(
                    f"❌ {str(e)}", ephemeral=True
                )
        
        #Set configuration value
        @self.tree.command(
            name="set_config",
            description= "Set a configuration value"
        )
        @admin_only()
        @app_commands.describe(
            key="Configuration key to set",
            value="New value for the configuration"
        )
        async def set_config(
            interaction: discord.Interaction,
            key: str,
            value: str
        ):
            await interaction.response.defer()
            try:
                #Get default value to determine type
                default_value = self.config.DEFAULT_CONFIG.get(key)
                if default_value is None:
                    raise ValueError(f"Invalid configuration key. Valid keys are: {', '.join(self.config.DEFAULT_CONFIG.keys())}")
                
                try:
                    if isinstance(default_value, bool):
                        converted_value = value.lower() == "true"
                    elif isinstance(default_value, (int, float)):
                        converted_value = type(default_value)(value)
                    else:
                        converted_value = value
                except ValueError:
                    raise ValueError(f"Invalid value type. Expected {type(default_value).__name__}, got '{value}'")
                
                await self.config.set(key, converted_value)
                # Update retriever config if it exists
                if hasattr(self, 'retriever'):
                    await self.retriever.update_config()

                await interaction.followup.send(
                    f"✅ Successfully set `{key}` to `{converted_value}`"
                )
            
            except ValueError as e:
                await interaction.followup.send(
                     f"❌ {str(e)}", ephemeral=True
                )
            except Exception as e:
                self.logger.error(f"Error setting config: {e}")
                await interaction.followup.send(
                    "❌ An error occurred while setting the configuration",
                    ephemeral=True
                )

        #Reset Configuration
        @self.tree.command(
            name="reset_config",
            description="Reset configuration to default value. Optionally specify a key to reset only that setting.",
        )
        @admin_only()
        @app_commands.describe(
            key="Optional: Configuration key to reset. If not provided, resets all settings."
        )
        async def reset_config(
            interaction: discord.Interaction,
            key: Optional[str] = None
        ):
            try:
                await self.config.reset(key)
                if key:
                    new_value = self.config.get(key)
                    await interaction.response.send_message(
                        f"✅ Reset `{key}` to default value: `{new_value}`"
                    )
                else:
                    await interaction.response.send_message(   
                        "✅ All configuration values have been reset to defaults"
                    )
            except ValueError as e:
                await interaction.response.send_message(
                    f"❌ {str(e)}", ephemeral=True
                )
            except Exception as e:
                self.logger.error(f"Error resetting config: {e}")
                await interaction.response.send_message(
                    "❌ An error occurred while resetting the configuration",
                    ephemeral=True
                )
        
        #Reset collection
        @self.tree.command(
            name="clear_collection",
            description="Clear all documents from a vector store collection"
        )
        @admin_only()
        @app_commands.describe(
            collection_name="Name of the collection to clear (defaults to active collection)",
            confirm="Type 'confirm' to proceed with clearing the collection"
        )

        async def clear_collection(
            interaction: discord.Interaction,
            collection_name: Optional[str] = None,
            confirm: str = None
        ):
            await interaction.response.defer()
            try:
                # Validate confirmation
                if not confirm or confirm.lower() != "confirm":
                    await interaction.followup.send(
                        "⚠️ You must type 'confirm' to proceed with clearing the collection.",
                        ephemeral=True
                    )
                    return
                
                # Use current collection if none specified
                target_collection = collection_name or self.vector_store.collection_name

                #Check if collection exists
                if target_collection not in self.vector_stores:
                    await interaction.followup.send(
                        f"❌ Collection '{target_collection}' not found. Available collections: {', '.join(self.vector_stores.keys())}",
                        ephemeral=True
                    )
                    return

                #Clear the collection
                vector_store = self.vector_stores[target_collection]
                await vector_store.clear_collection()

                await interaction.followup.send(
                    f"✅ Successfully cleared all documents from collection '{target_collection}'",
                    ephemeral=True
                )

            except Exception as e:
                self.logger.error(f"Error clearing collection: {str(e)}", exc_info=True)
                await interaction.followup.send(
                    f"❌ Error clearing collection: {str(e)}",
                    ephemeral=True
                )
                    
    async def setup_hook(self):
        """Async Initialization"""
        self.logger.info("Initializing Notion Bot components...")

        # Log ChromaDB version
        import chromadb
        self.logger.info(f"Running ChromaDB version: {chromadb.__version__}")

        #Initialize config database
        await self.config.init_db()

        try:
            self.logger.debug("Creating ChromaDB client...")
            chroma_client = chroma_client = chromadb.PersistentClient(
                path="chroma_db",
                settings=chromadb.Settings(
                    allow_reset=True,
                    is_persistent=True
                )
            )
            self.logger.debug("Getting collection list...")
            collection_list = chroma_client.list_collections()
            self.logger.debug(f"Found collections: {collection_list}")
            
            for collection in collection_list:
                if hasattr(collection, 'name'):
                    name = collection.name
                    self.logger.info(f"Found existing collection: {name}")
                    chunk_size = await self.config.get("chunk_size")

                    try:
                        self.vector_stores[name] = VectorStore(
                            collection_name=name,
                            chunk_size=chunk_size
                        )
                        self.logger.info(f"Successfully initialized vector store for collection: {name}")
                    except Exception as e:
                        self.logger.error(f"Failed to initialize vector store for collection {name}: {str(e)}")
            
            
            # If default collection doesn't exist, create it
            self.logger.debug(f"Checking if default collection exists: {self.default_collection}")
            if self.default_collection not in self.vector_stores:
                self.logger.debug(f"Creating default collection: {self.default_collection}")
                chunk_size = await self.config.get("chunk_size")
                self.vector_stores[self.default_collection] = VectorStore(
                    collection_name=self.default_collection,
                    chunk_size=chunk_size
                )

            self.vector_store = self.vector_stores[self.default_collection]
            self.retriever = Retriever(vector_store=self.vector_store, config_manager=self.config)
            await self.retriever.initialize()
            
        except Exception as e:
            self.logger.error(f"Error initializing vector stores: {e}")
            raise e

        self.logger.info("syncing commands...")
        try:
            await self.tree.sync()
            self.logger.info("commands synced")
        except Exception as e:
            self.logger.error(f"Error syncing commands: {e}")
            raise e
    
        self.logger.info("NotionBot initialization complete")


    async def get_conversation_history(self, channel, limit=None):
        """Get recent conversation history from Discord channel"""
        if limit is None:
            limit = await self.config.get("message_history_limit")

        messages = []
        async for msg in channel.history(limit=limit):
            # Skip bot messages that don't have content
            if msg.author.bot and not msg.content:
                continue
            messages.append({
                "role": "assistant" if msg.author.bot else "user",
                "content": msg.content
            })
        return list(reversed(messages))

    async def get_conversation_context(
            self, 
            query: str, 
            conversation_history: Optional[List[Dict[str, str]]] = None
            ) -> tuple[str, list]:
        """Get relevant context from both chat history and vector store"""
        # combine current query with relevant history
        conversation = []
        #REFACTOR: Below (query and message history similarity) into separate function
        try:
            if conversation_history:                
                #Calculate embeddings for history and current query
                history_texts = [msg["content"] for msg in conversation_history]
                all_embeddings = await self.vector_store.embed_texts(history_texts + [query])

                #Calculate similarity between current query and message history
                if all_embeddings is not None:
                    query_embedding = all_embeddings[-1]
                    history_embeddings = all_embeddings[:-1]

                    similarities = [
                        np.dot(query_embedding, hist_embed)
                        for hist_embed in history_embeddings
                    ]

                    # Filter relevant history based on similarity threshold
                    similarity_threshold = await self.config.get("similarity_threshold")
                    max_history = await self.config.get("max_history")

                    relevant_history = [
                        msg for msg, sim in zip(conversation_history, similarities)
                        if sim > similarity_threshold
                    ]
                    conversation.extend(relevant_history[-max_history:])
            else: 
                max_history = await self.config.get("max_history")
                conversation.extend(conversation_history[-max_history:])
       
            self.logger.debug(f"Coversation History: {conversation}")
            # Get relevant documents for both history and current query
            relevant_doc_context = await self.retriever.get_context_for_query(
                query,
                conversation_history=conversation
            )

            # Truncate context if too long (approximately 4000 tokens)
            max_context_chars = await self.config.get("max_content_chars")
            if len(relevant_doc_context) > max_context_chars:
                self.logger.warning(f"Truncating context from {len(relevant_doc_context)} to {max_context_chars} characters")
                relevant_doc_context = relevant_doc_context[:max_context_chars] + "..."

            self.logger.debug(f"Conversation: {conversation}")
            self.logger.debug(f"Relevant docs: {relevant_doc_context}")

            return relevant_doc_context, conversation
        
        except Exception as e:
            self.logger.warning(f"Error processing conversation history: {str(e)}")
            # Fallback: use recent history without similarity filtering
            relevant_docs = await self.retriever.get_context_for_query(query)
            return relevant_doc_context, []
    

    async def on_message(self, message):
        #Ignore messages from the bot
        if message.author == self.user:
            return
        
        #Check if bot is mentioned
        if self.user in message.mentions:
            # Remove the mention and extract the questions
            question = message.content.replace(f'<@{self.user.id}>', '').strip()

            if question:
                # send typing indicator
                async with message.channel.typing():
                    try:
                        # Get recent conversation history
                        message_history_limit = await self.config.get("message_history_limit")
                        conversation = await self.get_conversation_history(channel=message.channel, limit=message_history_limit) 

                        # Get context and generate response using existing logic
                        context = await self.get_conversation_context(
                            query=question,
                            conversation_history=conversation
                        )
                        system_prompt = await self.config.get("system_prompt")
                        messages = [
                            {"role": "system", "content": f"Role: {system_prompt}"},
                            {"role": "system", "content": f"Context: {context}"},
                            *conversation, 
                            {"role": "user", "content": question}
                        ]

                        llm_model = await self.config.get("llm_model")
                        response = await self.openai_client.chat.completions.create(
                            model=llm_model,
                            messages=messages
                        )

                        await message.reply(response.choices[0].message.content)
                    except Exception as e:
                        await message.reply(f"❌ Error: {str(e)}")
            
            else:
                welcome_message = await self.config.get("welcome_message")
                await message.reply(welcome_message) 


async def sync_notion(
    interaction: discord.Interaction, 
    bot : NotionBot, 
    resource_id: Optional[str]= None,
    collection_name: Optional[str] = None):
    """Sync Notion content to vector store (Admin only)"""
    await interaction.response.defer()

    try:
        if not resource_id:
            resource_id = os.getenv("NOTION_RESOURCE_ID")

        collection_name = collection_name or bot.default_collection
        if collection_name not in bot.vector_stores:
            bot.vector_stores[collection_name] = VectorStore(collection_name=collection_name)
        
        vector_store = bot.vector_stores[collection_name]

        async def progress_callback(msg: str):
            await interaction.followup.send(msg, ephemeral=True)
        try:
            sync_results = await sync_notion_content(
                notion_client=bot.notion_client,
                vector_store=vector_store,
                resource_id=resource_id,
                progress_callback=progress_callback
            )

            # Show detailed results
            collection_info = f" to collection '{vector_store.collection_name}'" if collection_name else ""

            # Fix grammar for singular/plural counts
            added_text = f"{sync_results['added']} page" + ("s" if sync_results['added'] != 1 else "")
            updated_text = f"{sync_results['updated']} page" + ("s" if sync_results['updated'] != 1 else "")
            deleted_text = f"{sync_results['deleted']} page" + ("s" if sync_results['deleted'] != 1 else "")

            # Calculate total as the sum of documents processed
            total_processed = sync_results['added'] + sync_results['updated']
            total_text = f"{total_processed} page" + ("s" if total_processed != 1 else "")


            result_message = (
                f"✅ Sync completed{collection_info}!\n"
                f"📝 Added: {added_text}\n"
                f"🔄 Updated: {updated_text}\n"
                f"🗑️ Deleted: {deleted_text}\n"
                f"📚 Total pages: {total_text}"
            )
            
            await interaction.followup.send(content=result_message)
        
        except Exception as e:
            bot.logger.error(f"Error during sync: {str(e)}", exc_info=True)
            if "APIStatusError" in str(e):
                await interaction.followup.send("❌ Error with OpenAI API. Please check your API key and permissions.", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ Error syncing content: {str(e)}", ephemeral=True)

    except ValueError as e:
        await interaction.followup.send(f"❌ Invalid resource ID: {str(e)}", ephemeral=True)    
    except RuntimeError as e:
        await interaction.followup.send(f"❌ Error with embeddings: {str(e)}", ephemeral=True)
    except Exception as e:
        bot.logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        await interaction.followup.send(f"❌ Unexpected error: {str(e)}", ephemeral=True)
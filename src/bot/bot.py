import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
from notion.client import NotionClient
from notion.sync import sync_notion_content
from rag.vectorstore import VectorStore
from rag.retriever import Retriever
from openai import AsyncOpenAI
import numpy as np
from functools import wraps
from typing import List, Dict, Any, Optional, Callable
import logging

load_dotenv()

def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        admin_ids = [int(id) for id in os.getenv("ADMIN_IDS", "").split(",") if id]
        return interaction.user.id in admin_ids
    return app_commands.check(predicate)

class NotionBot(commands.Bot):
    def __init__(self):

        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
            load_dotenv(override=True)  # Add override=True

        self.max_history = 5
        self.logger = logging.getLogger(__name__)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds= True
        super().__init__(command_prefix="!", intents=intents)

        #Initialize components
        self.logger.info("Initializing Notion Bot components...")
        self.notion_client = NotionClient(api_key=os.getenv("NOTION_TOKEN"))
        self.vector_store = VectorStore()
        self.retriever = Retriever(vector_store=self.vector_store)
        self.openai_client = AsyncOpenAI()
        self.logger.info("NotionBot initialization complete")

        @self.tree.command(name="sync", description="Sync Notion content to vector store")
        async def sync(interaction: discord.Interaction):
            await sync_notion(interaction, self)

    async def setup_hook(self):
        self.logger.info("syncing commands...")
        try:
            await self.tree.sync()
            self.logger.info("commands synced")
        except Exception as e:
            self.logger.error(f"Error syncing commands: {e}")

    async def get_conversation_history(self, channel, limit=5):
        """Get recent conversation history from Discord channel"""
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
                similarity_threshold = 0.7 #TODO make this configurable
                
                relevant_history = [
                    msg for msg, sim in zip(conversation_history, similarities)
                    if sim > similarity_threshold
                ]
                conversation.extend(relevant_history[-self.max_history:])
            else: 
                conversation.extend(conversation_history[-self.max_history:])
        except Exception as e:
            self.logger.warning(f"Error processing conversation history: {str(e)}")
            # Fallback: use recent history without similarity filtering
            if conversation_history:
                conversation.extend(conversation_history[-self.max_history:])
        
        # Get relevant documents for both history and current query
        relevant_docs = await self.retriever.get_context_for_query(
            query,
            conversation_history=conversation
        )

        self.logger.debug(f"Conversation: {conversation}")
        self.logger.debug(f"Relevant docs: {relevant_docs}")
        return relevant_docs, conversation
    

        

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
                        conversation = await self.get_conversation_history(channel=message.channel, limit=5) #TODO configurable message history

                        # Get context and generate response using existing logic
                        context = await self.get_conversation_context(
                            query=question,
                            conversation_history=conversation
                        )

                        messages = [
                            {"role": "system", "content": "You are a helpful assistant answering questions based on the provided context."},
                            {"role": "system", "content": f"Context: {context}"},
                            *conversation, 
                            {"role": "user", "content": question}
                        ]

                        #TODO move below options to a config file/writeable db and create discord command to update this config
                        response = await self.openai_client.chat.completions.create(
                            model="gpt-4",
                            messages=messages
                        )

                        await message.reply(response.choices[0].message.content)
                    except Exception as e:
                        await message.reply(f"❌ Error: {str(e)}")
            
            else:
                await message.reply(("Hello! Ask me anything about your Notion content!")) #TODO make configurable



# Move sync command outside of Bot Class

async def sync_notion(interaction: discord.Interaction, bot : NotionBot, resource_id: Optional[str]= None):
    """Sync Notion content to vector store (Admin only)"""
    await interaction.response.defer()

    try:

        if not resource_id:
            resource_id = os.getenv("NOTION_RESOURCE_ID")

        async def progress_callback(msg: str):
            await interaction.followup.send(msg)

        sync_results = await sync_notion_content(
            notion_client=bot.notion_client,
            vector_store=bot.vector_store,
            resource_id=resource_id,
            progress_callback=progress_callback
        )

        # Show detailed results
        result_message = (
            f"✅ Sync completed!\n"
            f"📝 Added: {sync_results['added']} pages\n"
            f"🔄 Updated: {sync_results['updated']} pages\n"
            f"🗑️ Deleted: {sync_results['deleted']} pages\n"
            f"📚 Total pages: {sync_results['total']}"
        )
        
        await interaction.followup.send(content=result_message)

    except ValueError as e:
        await interaction.followup.send(f"❌ Invalid resource ID: {str(e)}")    
    except Exception as e:
        await interaction.followup.send(f"❌ Error syncing content: {str(e)}")
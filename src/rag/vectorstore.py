from typing import List, Dict, Any, Optional
from chromadb.api.types import QueryResult
import chromadb 
import numpy as np
import logging
import os
from datetime import datetime

class VectorStore:
    def __init__(
        self,
        persist_directory: str = "chroma_db",
        embedding_function: Optional[Any] = None,
        collection_name: str = "notion_docs" #TODO make this an env variable
    ):
        try:
            self.logger = logging.getLogger(__name__)
            # Initialize ChromaDB client
            self.client = chromadb.PersistentClient(path=persist_directory)

            # Set default embedding function if none provided

            if embedding_function is None:
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                self.embedding_function = OpenAIEmbeddingFunction(
                    api_key=os.getenv("OPENAI_API_KEY"),
                    model_name="text-embedding-3-small"  # Updated model name
                )
            else:
                self.embedding_function = embedding_function

            # Get or create collection
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"}  # Use cosine similarity
            )
        except Exception as e:
            if "deprecated configuration" in str(e):
                raise RuntimeError(
                    "ChromaDB needs migration. Please run:\n"
                    "pip install chroma-migrate\n"
                    "chroma-migrate"
                ) from e
            raise

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Helper method to embed texts"""
        try:
            return self.embedding_function(texts)
        except Exception as e:
            self.logger.error(f"Error embedding texts: {str(e)}")
            return None

    async def add_documents(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None
    ) -> None:
        """Add Documents to Vector Store"""
        if not ids:
            # Generate timestamps as IDs if not provided
            ids = [f"doc_{datetime.now().timestamp()}_{i}" for i in range(len(texts))]

        if not metadatas:
            metadatas = [{"source": "notion"} for _ in texts]

        if isinstance(ids, tuple):
            ids = list(ids)

        if isinstance(metadatas, tuple):
            metadatas = list(metadatas)

        if isinstance(texts, tuple):
            texts = list(texts)

        min_length = min(len(texts), len(ids), len(metadatas))
        texts = texts[:min_length]
        ids = ids[:min_length]
        metadatas = metadatas[:min_length]

        processed_texts = []
        processed_ids = []
        final_processed_metadatas = []

        # remove any lists from metadata (not supported by chromadb)
        safe_metadatas = []
        for metadata in metadatas:
            processed_metadata = {}
            for key, value in metadata.items():
                if isinstance(value, list):
                    processed_metadata[key] = ", ".join(str(v) for v in value)
                elif isinstance(value, (str, int, float, bool)):
                    processed_metadata[key] = value
                else:
                    processed_metadata[key] = str(value)
            safe_metadatas.append(processed_metadata)

        for i, (text, doc_id, meta) in enumerate(zip(texts, ids, safe_metadatas)):
            if isinstance(text, tuple):
                text = "\n\n".join(str(item) for item in text if item)
            elif not isinstance(text, str):
                text = str(text) if text is not None else ""

            text = text.strip()
            if text:
                processed_texts.append(text)
                processed_ids.append(doc_id)
                final_processed_metadatas.append(meta)

            preview = text[:50] + '...' if len(text) > 50 else text
            self.logger.info(f"Adding document {doc_id} with content: {preview}")
        
            
        self.logger.debug(f"Number of processed texts: {len(processed_texts)}")
        self.logger.debug(f"Number of processed IDs: {len(processed_ids)}")
        self.logger.debug(f"Number of processed metadatas: {len(final_processed_metadatas)}")

        # Add documents to collection
        self.collection.add(
            documents=processed_texts,
            metadatas=final_processed_metadatas,
            ids=processed_ids
        )

    async def query(
        self,
        query_text: str,
        n_results: int = 3,
        where: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Query the vector store for similar documents"""
        return self.collection.query(
            query_texts=[query_text],
            n_results=n_results,
            where=where
        )
    
    async def update(
        self,
        ids: List[str],
        texts: List[str],
        metadatas: Optional[List[Dict]] = None
    ) -> None:
        """Update existing documents"""
        self.collection.update(
            ids=ids,
            documents=texts,
            metadatas=metadatas
        )

    async def delete(self, ids: List[str]) -> None:
        """Delete documents by their ids"""
        self.collection.delete(ids=ids)

    def peek(self, n: int =5) -> Dict[str, Any]:
        """"Peek at the first n documents in the store."""
        return self.collection.peek(n)
    
    async def clear_collection(self) -> None:
        """Clear entire collection"""
        try:
            self.collection.delete(ids=self.collection.get()["ids"])
        except Exception as e:
            if "no documents" not in str(e).lower():
                raise
    
    async def get_all_documents(self) -> Dict[str, Any]:
        """Get all documents and their metadata"""
        try:
            return self.collection.get()
        except Exception as e:
            if "no documents" in str(e).lower():
                return {"ids": [], "metadatas": [], "documents": []}
            raise


    async def sync_documents(
        self,
        ids: List[str],
        texts: List[str],
        metadatas: List[Dict]
    ) -> Dict[str, Any]:
        """Smart sync that only updates changed documents since last sync"""
        existing_docs = await self.get_all_documents()
        existing_ids = {id: (doc, meta) for id, doc, meta in zip(
            existing_docs["ids"],
            existing_docs["documents"],
            existing_docs["metadatas"]
        )}

        for i, (id, text) in enumerate(zip(ids, texts)):
            self.logger.debug(f"Document {id} size: {len(text)} characters")
            if len(text) > 500:  # Log preview for longer documents
                self.logger.debug(f"Preview: {text[:500]}...")


        # Separate documents into new, updated, and deleted
        to_add = []
        to_update = []
        to_delete = []

        for id, text, metadata in zip(ids, texts, metadatas):
            notion_last_modified = metadata.get("last_modified")

            if id not in existing_ids:
                to_add.append((id, text, {
                    **metadata,
                    "last_synced": datetime.now().isoformat()
                    }))
            else:
                old_doc, old_meta = existing_ids[id]
                vector_last_modified = old_meta.get("last_modified")
                
                # Compare last_modified dates
                if (notion_last_modified and vector_last_modified and 
                    notion_last_modified > vector_last_modified):
                    to_update.append((id, text, {
                        **metadata,
                        "last_synced": datetime.now().isoformat()
                    }))
            existing_ids.pop(id, None)

        #delete existing docs in vector db that are no longer in notion
        to_delete = list(existing_ids.keys())

        if to_delete:
            await self.delete(to_delete)
        if to_update:
            update_ids, update_texts, update_metas = zip(*to_update)
            await self.update(update_ids, update_texts, update_metas)
        if to_add:
            add_ids, add_texts, add_metas = zip(*to_add)
            await self.add_documents(add_texts, add_metas, add_ids)

        return {
            "added": len(to_add),
            "updated": len(to_update),
            "deleted": len(to_delete)
        }
        
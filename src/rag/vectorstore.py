from typing import List, Dict, Any, Optional
from chromadb.api.types import QueryResult
import chromadb 
import numpy as np
import logging
import os
from datetime import datetime
import time

class VectorStore:
    def __init__(
        self,
        persist_directory: str = "chroma_db",
        embedding_function: Optional[Any] = None,
        collection_name: Optional[str] = None,
        chunk_size: Optional[int] = 2000
    ):
        try:
            self.logger = logging.getLogger(__name__)
            # Initialize ChromaDB client
            os.makedirs(persist_directory, exist_ok=True)
            self.client = chromadb.PersistentClient(
                path=persist_directory,
                settings=chromadb.Settings(
                    allow_reset=True,
                    is_persistent=True
                )
            )

            # Set default embedding function if none provided
            if embedding_function is None:
                from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
                self.embedding_function = OpenAIEmbeddingFunction(
                    api_key=os.getenv("OPENAI_API_KEY"),
                    model_name="text-embedding-3-small", 
                )
            else:
                self.embedding_function = embedding_function

            self.collection_name = collection_name or "notion_docs"
            
            self.chunk_size = min(chunk_size, 6000)

            # Get or create collection
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self.collection = self.client.get_or_create_collection(
                        name=self.collection_name,
                        embedding_function=self.embedding_function,
                        metadata={"hnsw:space": "cosine"}
                    )
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    self.logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
                    time.sleep(1)
                    
        except Exception as e:
            if "deprecated configuration" in str(e):
                raise RuntimeError(
                    "ChromaDB needs migration. Please run:\n"
                    "pip install chroma-migrate\n"
                    "chroma-migrate"
                ) from e
            raise RuntimeError(f"Failed to initialize vector store: {str(e)}") from e

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Helper method to embed texts"""
        try:
            return self.embedding_function(texts)
        except Exception as e:
            self.logger.error(f"Error embedding texts: {str(e)}")
            raise RuntimeError(f"Failed to create embeddings: {str(e)}") from e
    
    def chunk_text(self, text: str, max_chars: int = 6000) -> List[str]:
        """Split text into chunks that won't exceed token limit"""
        chunks = []
        current_chunk = ""

        max_chars = min(max_chars, 6000)
        
        # Split by paragraphs first
        paragraphs = text.split("\n\n")
        
        for paragraph in paragraphs:
            # If single paragraph exceeds limit, split by sentences
            if len(paragraph) > max_chars:
                sentences = paragraph.split(". ")
                for sentence in sentences:
                    if len(current_chunk) + len(sentence) < max_chars:
                        current_chunk += sentence + ". "
                    else:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence + ". "
            # Otherwise handle paragraph normally
            elif len(current_chunk) + len(paragraph) < max_chars:
                current_chunk += paragraph + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph + "\n\n"
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks

    async def add_documents(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
        skip_existing: bool = False 
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

        try:
            existing_docs = await self.get_all_documents()
            existing_ids = set(existing_docs["ids"])
        except Exception as e:
            self.logger.warning(f"Could not fetch existing documents: {e}")
            existing_ids = set()

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
            if skip_existing and doc_id in existing_ids:
                self.logger.debug(f"Skipping existing document {doc_id}")
                continue

            if isinstance(text, tuple):
                text = "\n\n".join(str(item) for item in text if item)
            elif not isinstance(text, str):
                text = str(text) if text is not None else ""

            text = text.strip()

            # Split into chunks if text is too long
            if len(text) > self.chunk_size :
                chunks = self.chunk_text(text, max_chars=self.chunk_size)
                for j, chunk in enumerate(chunks):
                    chunk_id = f"{doc_id}_chunk_{j}"
                    if skip_existing and chunk_id in existing_ids:
                        continue
                    chunk_meta = {**meta, "chunk": j, "parent_id": doc_id}
                    processed_texts.append(chunk)
                    processed_ids.append(chunk_id)
                    final_processed_metadatas.append(chunk_meta)
            else:
                processed_texts.append(text)
                processed_ids.append(doc_id)
                final_processed_metadatas.append(meta)

            preview = text[:50] + '...' if len(text) > 50 else text
            self.logger.info(f"Adding document {doc_id} with content: {preview}")
        
        if processed_texts:
            try:
                batch_size =10
                for i in range(0, len(processed_texts), batch_size):
                    batch_texts = processed_texts[i:i+batch_size]
                    batch_ids = processed_ids[i:i+batch_size]
                    batch_metadatas = final_processed_metadatas[i:i+batch_size]
                    
                    self.collection.add(
                        documents=batch_texts,
                        ids=batch_ids,
                        metadatas=batch_metadatas                    
                    )
                    self.logger.debug(f"Added batch {i//batch_size + 1}")
            
                self.logger.debug(f"Number of processed texts: {len(processed_texts)}")
                self.logger.debug(f"Number of processed IDs: {len(processed_ids)}")
                self.logger.debug(f"Number of processed metadatas: {len(final_processed_metadatas)}")

            except Exception as e:
                self.logger.error(f"Error adding documents: {str(e)}", exc_info=True)
                raise RuntimeError(f"Failed to add documents: {str(e)}") from e
            
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

        # Track documents by resource
        resource_ids = {meta.get("resource_id") for meta in metadatas if meta.get("resource_id")}
        
        # Find all chunks of existing documents
        chunk_parents = {
            meta.get("parent_id"): [] 
            for meta in existing_docs["metadatas"] 
            if meta.get("parent_id")
        }
        for id, (_, meta) in existing_ids.items():
            if meta.get("parent_id"):
                chunk_parents[meta.get("parent_id")].append(id)

        # Separate documents into new, updated, and deleted
        to_add = []
        to_update = []
        to_delete = []

        for id, text, metadata in zip(ids, texts, metadatas):
            notion_last_modified = metadata.get("last_modified")

            # Handle main document and its chunks
            if id in existing_ids:
                old_doc, old_meta = existing_ids[id]
                vector_last_modified = old_meta.get("last_modified")
                
                # Check if document needs update
                if (notion_last_modified and vector_last_modified and 
                    notion_last_modified > vector_last_modified):
                    to_update.append((id, text, {
                        **metadata,
                        "last_synced": datetime.now().isoformat()
                    }))
                    # Delete old chunks if they exist
                    if id in chunk_parents:
                        to_delete.extend(chunk_parents[id])
            else:
                to_add.append((id, text, {
                    **metadata,
                    "last_synced": datetime.now().isoformat()
                }))
            
            # Remove from existing_ids to track what's deleted
            existing_ids.pop(id, None)

        #delete existing docs in vector db that are no longer in notion
        to_delete.extend([
            id for id, (_, meta) in existing_ids.items()
            if meta.get("resource_id") in resource_ids and
            not meta.get("parent_id")  
        ])

        if to_delete:
            await self.delete(to_delete)
        if to_update:
            update_ids, update_texts, update_metas = zip(*to_update)
            await self.update(update_ids, update_texts, update_metas)
        if to_add:
            add_ids, add_texts, add_metas = zip(*to_add)
            await self.add_documents(
                texts=add_texts,
                metadatas=add_metas,
                ids=add_ids,
                skip_existing=False  # Don't skip as we want to update existing docs
            )

        return {
            "added": len(to_add),
            "updated": len(to_update),
            "deleted": len(to_delete)
        }
        
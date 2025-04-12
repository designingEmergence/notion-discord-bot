from typing import List, Dict, Any, Optional
from chromadb.api.types import QueryResult
from rag.utils import (
    convert_ids_to_string, convert_text_to_string, clean_metadata,
    map_chunks_by_parent, add_sync_metadata, batch_process_async
)

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
            
            # Debug logging for environment variables
            api_key = os.getenv("OPENAI_API_KEY")
                
            if not api_key:
                raise ValueError("OPENAI_API_KEY not found in environment")

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
                    api_key=api_key,
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

    async def _process_document_with_chunking(
        self,
        doc_id: str,
        doc_text: str,
        doc_meta: Dict[str, Any],
        operation: str
    ) -> bool:
        """Add a doc that requires chunking to the vector store."""

        try:
            chunks = self.chunk_text(doc_text, max_chars=self.chunk_size)
            chunk_texts = []
            chunk_ids = []
            chunk_metas = []

            # Generate metadata and IDs for each chunk
            for j, chunk in enumerate(chunks):
                chunk_id = f"{doc_id}_chunk_{j}"
                chunk_meta = {**doc_meta, "chunk": j, "parent_id": doc_id, "chunk_id": chunk_id}
                
                chunk_texts.append(chunk)
                chunk_ids.append(chunk_id)
                chunk_metas.append(chunk_meta)

            #Add chunks to vector store
            try:
                self.collection.add(
                    documents=chunk_texts,
                    ids=chunk_ids,
                    metadatas=chunk_metas
                )
                self.logger.debug(f"{operation.capitalize()}ed {len(chunks)} chunks for document {doc_id}")
                return True

            except Exception as e:
                if "already exists" in str(e).lower():
                    #Try individual additions with timestamp fallback
                    added_count = 0
                    self.logger.warning(f"Some chunks already exist for {doc_id}, trying individual additions")

                    for j, (chunk_text, chunk_id, chunk_meta) in enumerate(zip(chunk_texts, chunk_ids, chunk_metas)):
                        success = self._add_single_chunk_with_fallback(chunk_text, chunk_id, chunk_meta)
                        if success:
                            added_count += 1
                    
                    if added_count > 0:
                        self.logger.debug(f"{operation.capitalize()}ed {added_count}/{len(chunks)} chunks with fallback")
                        return True
                    else:
                        self.logger.error(f"Failed to add any chunks for document {doc_id}")
                        return False
                else:
                    self.logger.error(f"Failed to {operation} chunked document {doc_id}: {str(e)}")
                    return False
        except Exception as e:
            self.logger.error(f"Error processing chunked document {doc_id}: {str(e)}")
            return False

    async def add_documents(
        self,
        texts: List[str],
        metadatas: Optional[List[Dict]] = None,
        ids: Optional[List[str]] = None,
        skip_existing: bool = False 
    ) -> int:
        """Add Documents to Vector Store"""
        if not ids:
            # Generate timestamps as IDs if not provided
            ids = [f"doc_{datetime.now().timestamp()}_{i}" for i in range(len(texts))]

        if not metadatas:
            metadatas = [{"source": "notion"} for _ in texts]

        # Use utility functions for consistent processing
        processed_ids = [convert_ids_to_string(id) for id in ids]
        processed_texts = [convert_text_to_string(text) for text in texts]
        processed_metadatas = [clean_metadata(meta) for meta in metadatas]

        min_length = min(len(processed_texts), len(processed_ids), len(processed_metadatas))
        processed_texts = processed_texts[:min_length]
        processed_ids = processed_ids[:min_length]
        processed_metadatas = processed_metadatas[:min_length]

        # Early check for empty batch
        if not processed_texts:
            self.logger.info("No documents to add, skipping.")
            return 0

        if skip_existing:
            try:
                # Get all existing IDs - we'll check this to avoid duplicates
                existing_docs = await self.get_all_documents()
                existing_ids = set(existing_docs["ids"])
            
                # Pre-filter ids to avoid duplicates
                filtered_indices = []
                for i, doc_id in enumerate(processed_ids):
                    if doc_id in existing_ids:
                        self.logger.debug(f"Pre-filtering duplicate document ID: {doc_id}")
                    else:
                        filtered_indices.append(i)
                
                if not filtered_indices:
                    self.logger.info("All documents already exist, skipping batch.")
                    return
                
                # Only keep non-duplicate documents in processed arrays to match the filtered_indices
                if len(filtered_indices) < len(processed_ids):
                    processed_texts = [processed_texts[i] for i in filtered_indices]
                    processed_ids = [processed_ids[i] for i in filtered_indices]
                    processed_metadatas = [processed_ids[i] for i in filtered_indices]
                    self.logger.info(f"Filtered batch from {min_length} to {len(filtered_indices)}")
            except Exception as e:
                self.logger.warning(f"Could not pre-filter duplicates: {e}")

        
        # Process each document individually using the appropriate method
        successfully_added = 0
        for i, (doc_id, doc_text, doc_meta) in enumerate(zip(processed_ids, processed_texts, processed_metadatas)):
            try:
                if len(doc_text) > self.chunk_size:
                    success = await self._process_document_with_chunking(doc_id, doc_text, doc_meta, "add")
                else:
                    success = await self._process_add_document(doc_id, doc_text, doc_meta)
                
                if success:
                    successfully_added +=1
            
            except Exception as e:
                self.logger.error(f"Error processing document {doc_id}: {str(e)}")
        
        self.logger.info(f"Successfully added {successfully_added} documents")

        return successfully_added

    async def _process_update_document(
        self,
        doc_id: str,
        doc_text: str,
        doc_meta: Dict[str, Any]
    ) -> bool:
        """Process update for a simple (non chunked) document"""

        try:
            self.collection.update(
                ids = [doc_id],
                documents=[doc_text],
                metadatas=[doc_meta]
            )
            self.logger.debug(f"Updated document {doc_id}")
            return True
        except Exception as e:
            # If document doesn't exist for update, fall back to add
            if "not found" in str(e).lower() or "nonexisting" in str(e).lower():
                self.logger.debug(f"Document {doc_id} not found for update, adding as new")
                return await self._process_add_document(doc_id, doc_text, doc_meta)
            else:
                self.logger.error(f"Failed to update document {doc_id}: {str(e)}")
                return False
    
    async def _process_add_document(
        self,
        doc_id: str,
        doc_text: str,
        doc_meta: Dict[str, Any]
    ) -> bool:
        """Process addition of a simple (non-chunked) document."""

        try:
            self.collection.add(
                documents=[doc_text],
                ids=[doc_id],
                metadatas=[doc_meta]
            )
            self.logger.debug(f"Adding document {doc_id}")
            return True
        except Exception as e:
            if "already exists" in str(e).lower():
                self.logger.debug(f"Document {doc_id} already exists")
                # Optionally attempt an update instead?
                return False
            else:
                self.logger.error(f"Failed to add document {doc_id}: {str(e)}")
                return False

    def _add_single_chunk_with_fallback(
        self, 
        chunk_text: str,
        chunk_id: str,
        chunk_meta: Dict[str, Any]
    ) -> bool:
        """Add a single chunk with timestamp fallback if ID conflict occurs."""

        try:
            # First try with original ID
            self.collection.add(
                documents=[chunk_text],
                ids=[chunk_id],
                metadatas=[chunk_meta]
            )
            return True
        except Exception as e:
            # If ID conflict, try with timestamped ID
            if "already exists" in str(e).lower():
                alt_chunk_id = f"{chunk_id}_{int(time.time())}"
                try:
                    self.collection.add(
                        documents=[chunk_text],
                        ids=[alt_chunk_id],
                        metadatas=[chunk_meta]
                    )
                    return True
                except Exception as alt_e:
                    self.logger.error(f"Failed to add chunk with alternate ID: {str(alt_e)}")
                    return False
            else:
                self.logger.error(f"Failed to add chunk: {str(e)}")
                return False

    async def _update_chunked_document(
        self,
        doc_id: str,
        doc_text: str,
        doc_meta: Dict[str, Any]
    ) -> bool:
        """Update a document that requires chunking"""
        try:
            existing_docs = await self.get_all_documents() #TODO should we do this outside the function and pass it in  so we don't make repeat calls
            chunks_to_delete = []
            
            for i, meta in enumerate(existing_docs["metadatas"]):
                if meta.get("parent_id") == doc_id:
                    chunks_to_delete.append(existing_docs["ids"][i])

            if chunks_to_delete:
                self.logger.debug(f"Deleting {len(chunks_to_delete)} existing chunks for document {doc_id}")
                self.collection.delete(ids=chunks_to_delete)
            
            # Now process the document with chunking as an add operation
            return await self._process_document_with_chunking(doc_id, doc_text, doc_meta, "update")
        
        except Exception as e:
            self.logger.error(f"Error updating chunked document {doc_id}: {str(e)}")
            return False
        
    async def _update_simple_document( #TODO combine with _update_chunked_document
        self,
        doc_id: str,
        doc_text: str,
        doc_meta: Dict[str, Any]
    ) -> bool:
        """Update a simple document that doesn't require chunking."""
        try:
            # First check if the document exists as a parent with chunks
            existing_docs = await self.get_all_documents()
            chunks_to_delete = []
            
            for i, meta in enumerate(existing_docs["metadatas"]):
                if meta.get("parent_id") == doc_id:
                    chunks_to_delete.append(existing_docs["ids"][i])
            
            if chunks_to_delete:
                self.logger.debug(f"Deleting {len(chunks_to_delete)} existing chunks for document {doc_id} being converted to simple document")
                self.collection.delete(ids=chunks_to_delete)
            
            # Now process the document as an update (with add fallback)
            return await self._process_update_document(doc_id, doc_text, doc_meta)
        
        except Exception as e:
            self.logger.error(f"Error updating simple document {doc_id}: {str(e)}")
            return False


    async def update(
        self,
        ids: List[str],
        texts: List[str],
        metadatas: Optional[List[Dict]] = None
    ) -> int:
        """Update existing documents"""
        processed_texts = [convert_text_to_string(text) for text in texts]
        processed_ids = [convert_ids_to_string(id) for id in ids]
        
        if metadatas is not None:
            processed_metadatas = [clean_metadata(meta) for meta in metadatas]
        else:
            processed_metadatas = [{"source": "notion"} for _ in processed_ids]

        successfully_updated = 0

        for i, (doc_id, doc_text, doc_meta) in enumerate(zip(processed_ids, processed_texts, processed_metadatas)):
            try:
                if len(doc_text) > self.chunk_size:
                    # Use the specialized chunked document update function
                    success = await self._update_chunked_document(doc_id, doc_text, doc_meta)
                else:
                    # Use the specialized simple document update function
                    success = await self._update_simple_document(doc_id, doc_text, doc_meta)
                    
                if success:
                    successfully_updated += 1
                    
            except Exception as e:
                self.logger.error(f"Error updating document {doc_id}: {str(e)}")

        self.logger.debug(f"Updated {successfully_updated} documents")
        return successfully_updated

    async def filter_deletions(
        self,
        to_delete: set,
        processed_ids: List[str],
        existing_docs: Dict[str, Any],
        to_update: List[tuple] = None
    ) -> List[str]:
        """Filter the deletion list to prevent unwanted deletions."""
        # Create a lookup for chunks in deletion list and their parent IDs
        chunk_parents_to_protect = set()
        deletion_chunks = {}

        # Map document IDs to their metadata for easier lookup
        id_to_meta = {meta.get("id", ""): meta for meta in existing_docs["metadatas"]}

        # Collect all chunks in to_delete and their parent IDs
        for item_id in to_delete:
            # Get metadata either by direct ID match or via the metadata's ID field
            meta = None
            for i, doc_id in enumerate(existing_docs["ids"]):
                if doc_id == item_id:
                    meta = existing_docs["metadatas"][i]
                    break
                if not meta:
                    continue

                parent_id = meta.get("parent_id")
                if parent_id and parent_id in processed_ids:
                    chunk_parents_to_protect.add(parent_id)
                    if parent_id not in deletion_chunks:
                        deletion_chunks[parent_id] = []
                    deletion_chunks[parent_id].append(item_id)

        #Get list of documents being updated (their chunks should be deleted)
        updating_docs = []
        if to_update:
            updating_docs = [item[0] for item in to_update]
        elif hasattr(self, 'to_update') and self.to_update:
            updating_docs = [item[0] for item in self.to_update]

        # Protect chunks whose parents are in the current sync batch but are not being updated
        for parent_id in chunk_parents_to_protect:
            if parent_id not in updating_docs:
                self.logger.debug(f"Protecting chunks for document {parent_id} in current sync batch (not being updated)")
                for chunk_id in deletion_chunks.get(parent_id, []):
                    to_delete.discard(chunk_id)
        
        return list(to_delete)

    async def delete(self, ids: List[str]) -> None:
        """Delete documents by their ids"""
        self.collection.delete(ids=ids)

    def peek(self, n: int =5) -> Dict[str, Any]:
        """"Peek at the first n documents in the store."""
        return self.collection.peek(n)
    
    async def clear_collection(self) -> None:
        """Clear entire collection"""
        try:
            # Get all document IDs
            result = self.collection.get()
            ids = result.get("ids", [])
            
            # Only attempt to delete if there are IDs
            if ids:
                self.collection.delete(ids=ids)
                self.logger.info(f"Successfully cleared {len(ids)} documents from collection '{self.collection_name}'")
            else:
                self.logger.info(f"Collection '{self.collection_name}' is already empty, nothing to clear")
        except Exception as e:
            self.logger.error(f"Error clearing collection: {str(e)}", exc_info=True)
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
        # Ensure all texts are strings
        processed_texts = [convert_text_to_string(text) for text in texts]
        processed_ids = [convert_ids_to_string(id) for id in ids]
        processed_metadatas = [clean_metadata(meta) for meta in metadatas]
        
        #Preview all documents
        # for i, (id, text) in enumerate(zip(processed_ids, processed_texts)):
        #     self.logger.debug(f"Document {id} Preview: {text[:500]}...")

        #Return existing documents in vectorDB
        existing_docs = await self.get_all_documents()
        existing_ids = {id: (doc, meta) for id, doc, meta in zip(
            existing_docs["ids"],
            existing_docs["documents"],
            existing_docs["metadatas"]
        )}

        # Track existing documents by which Notion resource they are from
        existing_resource_ids = {meta.get("resource_id") for meta in existing_docs.get("metadatas") if meta.get("resource_id")}
        
        # Build a map of all existing chunks by parent ID
        existing_chunk_parents, parent_chunk_ids, all_existing_chunk_ids = map_chunks_by_parent(
        existing_docs["ids"], existing_docs["metadatas"]
    )
        
        # Tracking collections for add, update and delete
        to_add = []
        to_update = []
        to_delete = set()  # Use a set to avoid duplicate deletions
        tracked_doc_ids = set()  # Track which document IDs we've processed

        # Debug logging of document IDs
        self.logger.debug("Existing document IDs in vector store:")
        for existing_id in existing_ids.keys():
            self.logger.debug(f"  {existing_id}")

        self.logger.debug("Processed document IDs from input:")
        for proc_id in processed_ids:
            self.logger.debug(f"  {proc_id}")

        #Process each incoming document
        for doc_id, doc_text, doc_metadata in zip(processed_ids, processed_texts, processed_metadatas):
            tracked_doc_ids.add(doc_id)

            #If incoming id is already in existing ids, check if its been updated since last sync
            if doc_id in existing_ids or doc_id in parent_chunk_ids:
                # Get the vector document data - either from direct match or from the first chunk
                if doc_id in existing_ids:
                    vector_doc, vector_meta = existing_ids[doc_id]
                else:
                    #Document exists as chunks, get metadata from first chunk
                    first_chunk_id = next(iter(parent_chunk_ids[doc_id]))
                    vector_doc, vector_meta = existing_ids[first_chunk_id]
                    #Exract the parent metadata which should be common across all chunks
                    for key in ['last_modified', 'title', 'resource_id']:
                        if key in vector_meta:
                            vector_meta[key] = vector_meta[key]

                notion_last_modified = doc_metadata.get("last_modified")
                vector_last_modified = vector_meta.get("last_modified")
                self.logger.debug(f"Incoming document {doc_id} last modified on {notion_last_modified}")
                self.logger.debug(f"Vector document {doc_id} last modified on {vector_last_modified}")

                # Check if document needs update and add to to_update if yes
                if (notion_last_modified and vector_last_modified and 
                    notion_last_modified > vector_last_modified):
                    #Mark all old chunks for this document for deletion
                    if doc_id in existing_chunk_parents:
                        chunk_count = len(existing_chunk_parents[doc_id])
                        to_delete.update(existing_chunk_parents[doc_id])
                        self.logger.info(f"Marking {chunk_count} old chunks for deletion for document {doc_metadata.get('title')} due to update")
                    
                    # Add new document to update list 
                    update_metadata = add_sync_metadata(doc_metadata)
                    self.logger.debug(f"Marking document for update: {doc_metadata.get('title')}")
                    to_update.append((doc_id, doc_text, update_metadata))
                    # Remove this document from existing_ids to prevent it from being deleted
                    existing_ids.pop(doc_id, None)
                else:
                    # Document hasn't changed, remove from candidates for deletion
                    existing_ids.pop(doc_id, None)
            else:
                # Check if this is a duplicate chunk 
                # QUESTION why are we checking for chunks here???
                self.logger.debug(f"Document id check (vectorstore) {doc_id}")
                is_db_chunk = "_chunk_" in doc_id 
                if is_db_chunk and doc_id in all_existing_chunk_ids:
                    self.logger.debug(f"Skipping duplicate chunk ID: {doc_id}") #TODO check if duplicate IDs are not being created for different chunks
                    continue

                # if id in existing_ids.keys():
                #     self.logger.debug(f"Document with ID {id} exists but wasn't found in processed docs. Skipping.")
                #     existing_ids.pop(id, None)
                #     continue
                
                # Create metadata with no None values
                add_metadata = add_sync_metadata(doc_metadata)
                page_title = doc_metadata.get("title", "Untitled")
                self.logger.info(f"Marking new document for addition: {page_title} (ID: {doc_id})")
                to_add.append((doc_id, doc_text, add_metadata))

        
        # Find documents to delete (those not in the current sync)
        for doc_id, (_, meta) in existing_ids.items():
            resource_id = meta.get("resource_id")
            is_chunk = meta.get("parent_id") is not None

            #  Only process parent documents
            if not is_chunk and resource_id in existing_resource_ids and doc_id not in tracked_doc_ids:
                    self.logger.info(f"Marking document {doc_id}: {meta.get('title')} for deletion")
                    to_delete.add(doc_id)
                    
                    # Also delete all associated chunks
                    if doc_id in parent_chunk_ids:
                        chunk_count = len(parent_chunk_ids[doc_id])
                        to_delete.update(parent_chunk_ids[doc_id])
                        self.logger.info(f"Marking {chunk_count} orphaned chunks for deletion for document {doc_id}")

        
        #Inititalize counters to track operations
        added_count = 0
        updated_count = 0
        deleted_count = 0

        # Track actual page deletions (not chunks or update-related deletions)
        true_page_deletions = 0
        deleted_page_ids = set()        

        # Process deletions
        if to_delete:
            to_delete_list = await self.filter_deletions(
                to_delete=to_delete,
                processed_ids=processed_ids,
                existing_docs=existing_docs,
                to_update=to_update
            )

            if to_delete_list:
                self.logger.info(f"Deleting {len(to_delete_list)} documents/fragments")

                #Identify and count true page deletions (not chunks)
                for item_id in to_delete_list:
                    # Check if this is a parent document (not a chunk)
                    is_chunk = False
                    for meta in existing_docs["metadatas"]:
                        if meta.get("chunk_id") == item_id or meta.get("parent_id") is not None:
                            is_chunk = True
                            break
                            
                    # If it's not a chunk and not part of an update operation
                    if not is_chunk and (not to_update or item_id not in [u[0] for u in to_update]):
                        # Extract the base ID without the "notion_" prefix
                        base_id = item_id.replace("notion_", "") if item_id.startswith("notion_") else item_id
                        deleted_page_ids.add(base_id)
                
                # Count unique deleted pages
                true_page_deletions = len(deleted_page_ids)

                async def delete_batch(batch):
                    await self.delete(batch) 
                try:
                    deleted_count = await batch_process_async(
                        items = to_delete_list,
                        batch_size=100,
                        process_fn=delete_batch,
                        description="deletions",
                        continue_on_error=True
                    )
                    self.logger.info(f"Successfully deleted {deleted_count} documents/fragments")
                except Exception as e:
                    self.logger.error(f"Error during deletion: {str(e)}")
        
        # Process additions
        if to_add:
            add_ids, add_texts, add_metas = zip(*to_add) if to_add else ([], [], []) 
            add_ids = [convert_ids_to_string(id) for id in add_ids]
            add_metas = [clean_metadata(meta) for meta in add_metas]
            
            try:    
                added_count = await self.add_documents( #Set added_count to docs that were actually updated
                    texts=add_texts,
                    metadatas=add_metas,
                    ids=add_ids,
                    skip_existing=True  # Skip existing to avoid duplicates
                )
                
                self.logger.info(f"Successfully added {added_count} documents")
            except Exception as e:
                self.logger.error(f"Error during document addition: {str(e)}")
        
        if to_update:
            update_ids, update_texts, update_metas = zip(*to_update)
            update_ids = [convert_ids_to_string(id) for id in update_ids]
            update_metas = [clean_metadata(meta) for meta in update_metas]
            self.logger.info(f"Updating {len(to_update)} documents")

            update_data = list(zip(update_ids, update_texts, update_metas))

            async def process_update_batch(batch):
                batch_ids = [item[0] for item in batch]
                batch_texts = [item[1] for item in batch]
                batch_metas = [item[2] for item in batch]

                await self.update(
                    ids=batch_ids,
                    texts=batch_texts,
                    metadatas=batch_metas
                )

            try:
                updated_count = await batch_process_async(
                   items = update_data,
                   batch_size=20,
                   process_fn=process_update_batch,
                   description="updates",
                   continue_on_error=True
               )
                self.logger.info(f"Succesfully updated {updated_count} documents")
                
            except Exception as e:
                self.logger.error(f"Error updating documents: {str(e)}", exc_info=True)

        self.logger.info(f"Sync complete: {added_count} added, {updated_count} updated, {deleted_count} deleted")

        return {
            "added": added_count,
            "updated": updated_count,
            "deleted": true_page_deletions
        }

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

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Helper method to embed texts"""
        try:
            return self.embedding_function(texts)
        except Exception as e:
            self.logger.error(f"Error embedding texts: {str(e)}")
            raise RuntimeError(f"Failed to create embeddings: {str(e)}") from e

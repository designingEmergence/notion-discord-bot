from typing import List, Dict, Any, Optional
from .vectorstore import VectorStore
from config import ConfigManager
import logging

class Retriever:
    def __init__(
        self,
        vector_store: VectorStore,
        config_manager: ConfigManager
    ):
        self.vector_store = vector_store
        self.config = config_manager
        self.logger = logging.getLogger(__name__)
    
    async def initialize(self):
        """Initialize configuration values"""
        self.max_tokens = await self.config.get("max_tokens")  # TODO: These only change on initiaiize, what happens if config changes via set_config
        self.num_results = await self.config.get("num_retrieved_results")    # T
        self.similarity_threshold = await self.config.get("similarity_threshold")
        self.logger.debug(f"Initializing retriever with max_tokens={self.max_tokens}, num_results={self.num_results}, similarity_threshold={self.similarity_threshold}")

    async def update_config(self):
        """Update configuration values from config manager"""
        self.max_tokens = await self.config.get("max_tokens")
        self.num_results = await self.config.get("num_retrieved_results")
        self.similarity_threshold = await self.config.get("similarity_threshold")
        self.logger.debug(f"Updated retriever config: max_tokens={self.max_tokens}, num_results={self.num_results}, similarity_threshold={self.similarity_threshold}")

    async def get_relevant_documents(
        self,
        query: str,
        filter_dict: Optional[Dict] = None
    ) -> List[Dict[str, Any]]:
        """Get relevant documents from vector store"""

        #TODO get the llm to write its own filter
        where_filter = filter_dict

        results = await self.vector_store.query(
            query_text=query,
            n_results=self.num_results,
            where=where_filter
        )
        return results

    def format_context(self, documents: List[Dict[str, Any]]) -> str:
        """Format retrieved documents into context string"""
        contexts = []
        # Track seen titles to avoid duplicates
        seen_titles = set()
        
        for doc, metadata in zip(
            documents.get("documents", [[]])[0],
            documents.get("metadatas", [[]])[0]
        ):
            title = metadata.get("title", "Untitled")
            url = metadata.get("url", "")
            
            # Skip duplicates with the same title
            # if title in seen_titles:
            #     continue
            
            #seen_titles.add(title)
            contexts.append(f"Title: {title}\nURL: {url}\nContent: {doc}")

        return "\n\n---\n\n".join(contexts)
    
    def _merge_and_rerank_results(
        self,
        current_results: Dict[str, Any],
        conversation_results: Dict[str, Any],
        query: str
    ) -> Dict[str, Any]:
        """Merge and rerank results from current query and conversation history"""
        # Get unique documents by ID
        seen_ids = set()
        merged_docs = []
        merged_metadatas = []
        merged_distances = []

        # Helper functions to process results
        def process_results(results: Dict[str, Any], boost: float = 1.0):
            for doc, metadata, distance, doc_id in zip(
                results.get("documents", [[]])[0],
                results.get("metadatas", [[]])[0],
                results.get("distances", [[]])[0],
                results.get("ids", [[]])[0]
            ):
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    merged_docs.append(doc)
                    merged_metadatas.append(metadata)
                    merged_distances.append(distance * boost)
        
        # Process current results with higher weight
        process_results(current_results, boost=0.7) #TODO make configurable
        # Process conversation results with lower weight
        process_results(conversation_results, boost=1.0) #TODO make configurable

        #Sort by distance
        sorted_items = sorted(
            zip(merged_docs, merged_metadatas, merged_distances),
            key=lambda x: x[2]
        )

        #Unzip sorted items
        docs, metas, distances = zip(*sorted_items) if sorted_items else ([], [], [])

        return {
            "documents": [list(docs)],
            "metadatas": [list(metas)],
            "distances": [list(distances)]
        }
    
    async def get_context_for_query(
        self,
        query: str,
        conversation_history: Optional[List[Dict]] = None,
        filter_dict: Optional[Dict] = None
    ) -> str:
        """Main method to get formatted context for a query and conversation history"""
        # Get relevant documents in vector store for current query
        retrieved_docs = await self.get_relevant_documents(query, filter_dict)

        if not conversation_history or len(conversation_history) <= 1:
            return self.format_context(retrieved_docs)

        #Get additional context from conversation if relevant
        conversation_text = " ".join([msg["content"] for msg in conversation_history[:-1]])

        if conversation_text.strip():
            conversation_results = await self.get_relevant_documents(conversation_text, filter_dict)
            all_docs = self._merge_and_rerank_results(
                retrieved_docs,
                conversation_results,
                query
            )
            return self.format_context(all_docs)
        
        return self.format_context(retrieved_docs)

    
    def _rerank_results(self, documents: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Optional: Rerank results using a cross-encoder or other method"""
        # TODO: Implement reranking if needed
        return documents
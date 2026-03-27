"""
Supabase Vector Store wrapper for LangChain compatibility.

This provides a LangChain-compatible vector store backed by 
Supabase pgvector for school+class-scoped similarity search.
"""
from typing import Any, Optional
from uuid import UUID

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.db.supabase import SupabaseClient
from app.db.models import EmbeddingCreate


class SupabaseVectorStore(VectorStore):
    """
    LangChain-compatible vector store using Supabase pgvector.
    
    This store is scoped to a specific school, subject, and class,
    ensuring that all queries only retrieve documents from that scope.
    """
    
    def __init__(
        self,
        supabase: SupabaseClient,
        school_id: UUID,
        subject_id: UUID,
        class_id: UUID,
        embeddings: Embeddings,
        similarity_threshold: float = 0.2,
    ):
        self._supabase = supabase
        self._school_id = school_id
        self._subject_id = subject_id
        self._class_id = class_id
        self._embeddings = embeddings
        self._similarity_threshold = similarity_threshold
    
    @property
    def embeddings(self) -> Embeddings:
        """Return the embeddings model."""
        return self._embeddings
    
    def add_texts(
        self,
        texts: list[str],
        metadatas: Optional[list[dict]] = None,
        document_id: Optional[UUID] = None,
        **kwargs
    ) -> list[str]:
        """
        Add texts to the vector store.
        
        Args:
            texts: List of text chunks to add
            metadatas: Optional metadata for each text
            document_id: ID of the parent document
        
        Returns:
            List of IDs for the added embeddings
        """
        if not texts:
            return []
        
        # Generate embeddings
        vectors = self._embeddings.embed_documents(texts)
        
        # Prepare embedding records
        embeddings_to_save = []
        for i, (text, vector) in enumerate(zip(texts, vectors)):
            metadata = metadatas[i] if metadatas and i < len(metadatas) else {}
            
            embeddings_to_save.append(EmbeddingCreate(
                document_id=document_id or UUID("00000000-0000-0000-0000-000000000000"),
                school_id=self._school_id,
                subject_id=self._subject_id,
                class_id=self._class_id,
                content=text,
                embedding=vector,
                page_number=metadata.get("page_number"),
                chunk_index=i,
                metadata=metadata,
            ))
        
        # Save to Supabase
        self._supabase.save_embeddings(embeddings_to_save)
        
        # Return placeholder IDs (Supabase generates actual UUIDs)
        return [f"emb_{i}" for i in range(len(texts))]
    
    def similarity_search(
        self,
        query: str,
        k: int = 5,
        **kwargs
    ) -> list[Document]:
        """
        Perform similarity search scoped to school+subject+class.
        
        Args:
            query: Query text
            k: Number of results to return
        
        Returns:
            List of matching documents
        """
        # Generate query embedding
        query_vector = self._embeddings.embed_query(query)
        
        # Search in Supabase (school-scoped)
        results = self._supabase.similarity_search(
            query_embedding=query_vector,
            subject_id=self._subject_id,
            class_id=self._class_id,
            school_id=self._school_id,
            limit=k,
            threshold=self._similarity_threshold,
        )
        
        # Convert to LangChain documents
        documents = []
        for result in results:
            doc = Document(
                page_content=result.content,
                metadata={
                    "id": str(result.id),
                    "page_number": result.page_number,
                    "chunk_index": result.chunk_index,
                    "similarity": result.similarity,
                    **result.metadata,
                }
            )
            documents.append(doc)
        
        return documents
    
    def similarity_search_with_score(
        self,
        query: str,
        k: int = 5,
        **kwargs
    ) -> list[tuple[Document, float]]:
        """
        Perform similarity search with scores.
        
        Returns:
            List of (document, score) tuples
        """
        query_vector = self._embeddings.embed_query(query)
        
        results = self._supabase.similarity_search(
            query_embedding=query_vector,
            subject_id=self._subject_id,
            class_id=self._class_id,
            school_id=self._school_id,
            limit=k,
            threshold=self._similarity_threshold,
        )
        
        docs_with_scores = []
        for result in results:
            doc = Document(
                page_content=result.content,
                metadata={
                    "id": str(result.id),
                    "page_number": result.page_number,
                    "chunk_index": result.chunk_index,
                    **result.metadata,
                }
            )
            docs_with_scores.append((doc, result.similarity))
        
        return docs_with_scores
    
    def as_retriever(self, **kwargs) -> "SupabaseRetriever":
        """Get a retriever for this vector store."""
        from langchain_core.retrievers import BaseRetriever
        from langchain_core.callbacks import CallbackManagerForRetrieverRun
        
        search_kwargs = kwargs.get("search_kwargs", {})
        k = search_kwargs.get("k", 5)
        
        class SupabaseRetriever(BaseRetriever):
            vectorstore: SupabaseVectorStore
            k: int = 5
            
            class Config:
                arbitrary_types_allowed = True
            
            def _get_relevant_documents(
                self, 
                query: str, 
                *, 
                run_manager: CallbackManagerForRetrieverRun = None
            ) -> list[Document]:
                return self.vectorstore.similarity_search(query, k=self.k)
        
        return SupabaseRetriever(vectorstore=self, k=k)
    
    @classmethod
    def from_texts(
        cls,
        texts: list[str],
        embedding: Embeddings,
        metadatas: Optional[list[dict]] = None,
        **kwargs
    ) -> "SupabaseVectorStore":
        """Create vector store from texts (required by base class)."""
        supabase = kwargs.get("supabase")
        school_id = kwargs.get("school_id")
        subject_id = kwargs.get("subject_id")
        class_id = kwargs.get("class_id")
        
        if not all([supabase, school_id, subject_id, class_id]):
            raise ValueError(
                "supabase, school_id, subject_id, and class_id are required kwargs"
            )
        
        store = cls(
            supabase=supabase,
            school_id=school_id,
            subject_id=subject_id,
            class_id=class_id,
            embeddings=embedding,
        )
        
        store.add_texts(texts, metadatas)
        return store

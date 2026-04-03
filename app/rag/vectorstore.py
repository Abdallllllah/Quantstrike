"""
Supabase Vector Store with Hybrid Retrieval.

Industry-standard retrieval pipeline:
1. Multi-query expansion: generates 3 query variations for better recall
2. Vector similarity search: semantic matching via pgvector
3. Keyword text search: exact term matching for precision
4. Reciprocal Rank Fusion (RRF): merges and ranks results from all sources
"""
from typing import Any, Optional
from uuid import UUID
from collections import defaultdict

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.db.supabase import SupabaseClient
from app.db.models import EmbeddingCreate


def _reciprocal_rank_fusion(
    result_lists: list[list[Document]],
    k: int = 60,
) -> list[Document]:
    """
    Reciprocal Rank Fusion (RRF) — industry standard for merging ranked lists.
    
    Combines multiple ranked result lists into a single ranking.
    Each document gets a score of 1/(k + rank) from each list it appears in.
    Documents appearing in multiple lists get boosted.
    
    Args:
        result_lists: List of ranked document lists
        k: RRF constant (default 60, from the original paper)
    
    Returns:
        Merged and re-ranked list of documents
    """
    scores: dict[str, float] = defaultdict(float)
    doc_map: dict[str, Document] = {}
    
    for result_list in result_lists:
        for rank, doc in enumerate(result_list):
            # Use content hash as ID since not all docs have metadata IDs
            doc_id = doc.metadata.get('id', str(hash(doc.page_content[:100])))
            scores[doc_id] += 1.0 / (k + rank + 1)
            doc_map[doc_id] = doc
    
    # Sort by RRF score (highest first)
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_map[doc_id] for doc_id in sorted_ids]


class SupabaseVectorStore(VectorStore):
    """
    LangChain-compatible vector store with hybrid retrieval.
    
    Scoped to a specific school, subject, and class.
    Uses multi-query + vector + keyword search with RRF ranking.
    """
    
    def __init__(
        self,
        supabase: SupabaseClient,
        school_id: UUID,
        subject_id: UUID,
        class_id: UUID,
        embeddings: Embeddings,
        similarity_threshold: float = 0.15,
    ):
        self._supabase = supabase
        self._school_id = school_id
        self._subject_id = subject_id
        self._class_id = class_id
        self._embeddings = embeddings
        self._similarity_threshold = similarity_threshold
    
    @property
    def embeddings(self) -> Embeddings:
        return self._embeddings
    
    def add_texts(
        self,
        texts: list[str],
        metadatas: Optional[list[dict]] = None,
        document_id: Optional[UUID] = None,
        **kwargs
    ) -> list[str]:
        """Add texts to the vector store."""
        if not texts:
            return []
        
        vectors = self._embeddings.embed_documents(texts)
        
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
        
        self._supabase.save_embeddings(embeddings_to_save)
        return [f"emb_{i}" for i in range(len(texts))]
    
    # ──────────────────────────────────────────────────────────────
    # RETRIEVAL: Multi-Query + Hybrid Search + RRF
    # ──────────────────────────────────────────────────────────────
    
    def _generate_query_variations(self, query: str) -> list[str]:
        """
        Multi-query expansion: generate alternative phrasings.
        
        Uses simple rule-based expansion (no LLM call needed).
        This improves recall by searching with multiple phrasings.
        """
        variations = [query]  # Original query always included
        
        q_lower = query.lower().strip().rstrip('?')
        
        # Variation 1: Convert question to statement
        for prefix in ['what is ', 'what are ', 'define ', 'explain ']:
            if q_lower.startswith(prefix):
                topic = q_lower[len(prefix):]
                variations.append(f"{topic} definition")
                variations.append(f"{topic} is defined as")
                return variations
        
        # Variation 2: For "how" questions
        if q_lower.startswith('how '):
            variations.append(q_lower.replace('how ', '', 1))
            variations.append(f"{q_lower} method")
            return variations
        
        # Variation 3: For calculation questions
        if any(word in q_lower for word in ['calculate', 'find', 'determine', 'solve']):
            variations.append(f"formula for {q_lower}")
            return variations
        
        # Default: add "definition" variation
        variations.append(f"{q_lower} definition explanation")
        return variations
    
    def _vector_search(self, query: str, k: int = 10) -> list[Document]:
        """Semantic vector similarity search via pgvector."""
        query_vector = self._embeddings.embed_query(query)
        
        results = self._supabase.similarity_search(
            query_embedding=query_vector,
            subject_id=self._subject_id,
            class_id=self._class_id,
            school_id=self._school_id,
            limit=k,
            threshold=self._similarity_threshold,
        )
        
        documents = []
        for result in results:
            doc = Document(
                page_content=result.content,
                metadata={
                    "id": str(result.id),
                    "page_number": result.page_number,
                    "chunk_index": result.chunk_index,
                    "similarity": result.similarity,
                    "search_type": "vector",
                    **result.metadata,
                }
            )
            documents.append(doc)
        
        return documents
    
    def _keyword_search(self, query: str, k: int = 5) -> list[Document]:
        """
        Keyword-based text search for precision.
        Catches chunks that vector search misses due to embedding dilution.
        """
        try:
            stop_words = {
                'what', 'is', 'the', 'a', 'an', 'how', 'does', 'do', 'can',
                'explain', 'define', 'tell', 'me', 'about', 'of', 'in', 'and',
                'or', 'to', 'for', 'are', 'was', 'were', 'be', 'been', 'with',
                'this', 'that', 'these', 'it', 'its', 'by', 'on', 'at', 'from',
            }
            words = [w.strip('?.,!') for w in query.lower().split()]
            keywords = [w for w in words if w not in stop_words and len(w) > 2]
            
            if not keywords:
                return []
            
            # Strategy: search for each keyword individually and combine
            all_results = []
            
            # Search 1: All keywords together (most specific)
            full_pattern = '%'.join(keywords)
            result = self._supabase.client.table('embeddings').select(
                'id, content, page_number, chunk_index, metadata'
            ).eq(
                'subject_id', str(self._subject_id)
            ).eq(
                'class_id', str(self._class_id)
            ).eq(
                'school_id', str(self._school_id)
            ).ilike(
                'content', f'%{full_pattern}%'
            ).limit(k).execute()
            
            seen_ids = set()
            for row in result.data:
                doc = Document(
                    page_content=row['content'],
                    metadata={
                        'id': str(row['id']),
                        'page_number': row.get('page_number'),
                        'chunk_index': row.get('chunk_index'),
                        'search_type': 'keyword_all',
                        **(row.get('metadata') or {}),
                    }
                )
                all_results.append(doc)
                seen_ids.add(str(row['id']))
            
            # Search 2: Each keyword phrase (for multi-word concepts like "potential energy")
            if len(keywords) >= 2:
                for i in range(len(keywords) - 1):
                    phrase = f"{keywords[i]} {keywords[i+1]}"
                    if len(phrase) > 5:  # Skip very short phrases
                        result2 = self._supabase.client.table('embeddings').select(
                            'id, content, page_number, chunk_index, metadata'
                        ).eq(
                            'subject_id', str(self._subject_id)
                        ).eq(
                            'class_id', str(self._class_id)
                        ).eq(
                            'school_id', str(self._school_id)
                        ).ilike(
                            'content', f'%{phrase}%'
                        ).limit(k).execute()
                        
                        for row in result2.data:
                            if str(row['id']) not in seen_ids:
                                doc = Document(
                                    page_content=row['content'],
                                    metadata={
                                        'id': str(row['id']),
                                        'page_number': row.get('page_number'),
                                        'chunk_index': row.get('chunk_index'),
                                        'search_type': 'keyword_phrase',
                                        **(row.get('metadata') or {}),
                                    }
                                )
                                all_results.append(doc)
                                seen_ids.add(str(row['id']))
            
            return all_results[:k * 2]
        except Exception as e:
            print(f"Keyword search error: {e}")
            return []
    
    def similarity_search(
        self,
        query: str,
        k: int = 5,
        **kwargs
    ) -> list[Document]:
        """
        Industry-standard hybrid retrieval pipeline:
        
        1. Generate query variations (multi-query)
        2. Run vector search for each variation
        3. Run keyword search for precision
        4. Merge all results with Reciprocal Rank Fusion (RRF)
        5. Return top-k ranked results
        """
        # 1. Generate query variations
        queries = self._generate_query_variations(query)
        
        # 2. Vector search with each query variation
        all_result_lists = []
        for q in queries:
            vector_results = self._vector_search(q, k=k)
            if vector_results:
                all_result_lists.append(vector_results)
        
        # 3. Keyword search (original query only)
        keyword_results = self._keyword_search(query, k=k)
        if keyword_results:
            all_result_lists.append(keyword_results)
        
        if not all_result_lists:
            return []
        
        # 4. Reciprocal Rank Fusion
        fused = _reciprocal_rank_fusion(all_result_lists, k=60)
        
        # 5. Return top results
        return fused[:k * 2]
    
    def similarity_search_with_score(
        self,
        query: str,
        k: int = 5,
        **kwargs
    ) -> list[tuple[Document, float]]:
        """Perform similarity search with scores."""
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
        """Get a retriever that uses the full hybrid pipeline."""
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

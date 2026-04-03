"""
Document Ingestion Service - Handles processing and indexing of educational content.

This service coordinates:
- Creating document metadata records
- Loading and chunking PDF files with contextual enrichment
- Generating embeddings and storing them in the school+class-scoped vector store
- Tracking indexing status

Industry-standard techniques:
- Small, focused chunks (300 chars) to isolate individual concepts
- Contextual chunk enrichment: prepends document name + detected section headers
  so that embeddings carry semantic context about WHERE a chunk came from
- Smart separators tuned for educational PDFs
"""
import os
import re
from uuid import UUID
from pathlib import Path
from typing import Optional, Tuple, List

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import DocumentCreate
from app.db.storage import get_storage_client
from app.rag.vectorstore import SupabaseVectorStore
from app.rag_legacy import get_embeddings


# Patterns that indicate section headings in educational PDFs
HEADING_PATTERNS = [
    r'^(?:Module|Chapter|Unit|Section|Part|Topic)\s+\d+',  # Module 1, Chapter 2, etc.
    r'^[A-Z][A-Z\s]{4,}$',                                  # ALL CAPS HEADINGS
    r'^\d+\.\s+[A-Z]',                                      # 1. Title format
    r'^[A-Z][a-z]+(?:\s+[A-Za-z]+){0,4}\s*$',               # Short title-case lines
]


def _detect_section_header(text: str) -> Optional[str]:
    """
    Try to detect the nearest section header from the chunk text.
    Looks at the first few lines for heading-like patterns.
    """
    lines = text.strip().split('\n')
    for line in lines[:3]:  # Check first 3 lines
        line = line.strip()
        if not line or len(line) > 80:
            continue
        for pattern in HEADING_PATTERNS:
            if re.match(pattern, line):
                return line
    return None


def _enrich_chunk(chunk_text: str, doc_name: str, page_num: int) -> str:
    """
    Contextual chunk enrichment (industry standard technique).
    
    Prepends document name and detected section header to the chunk text
    BEFORE embedding. This dramatically improves retrieval because the
    embedding now carries context about where the chunk came from.
    
    Example:
        Before: "It is a energy possessed by a system because of..."
        After:  "From: classical mechanics.pdf | Section: Potential energy | Page 15
                 It is a energy possessed by a system because of..."
    """
    section = _detect_section_header(chunk_text)
    
    prefix_parts = [f"From: {doc_name}"]
    if section:
        prefix_parts.append(f"Section: {section}")
    prefix_parts.append(f"Page {page_num}")
    
    prefix = " | ".join(prefix_parts)
    return f"{prefix}\n\n{chunk_text}"


class IngestionService:
    """Service for processing and indexing documents for RAG."""
    
    def __init__(self, supabase: Optional[SupabaseClient] = None):
        self._supabase = supabase or get_supabase_client()
        
        # Industry-standard: small chunks for educational content
        # 300 chars ≈ 2-3 sentences, perfect for isolating definitions
        # Separators tuned for educational PDF structure
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=300,
            chunk_overlap=75,
            add_start_index=True,
            separators=[
                "\n\n",       # Paragraph breaks (strongest)
                "\n",         # Line breaks
                ". ",         # Sentence boundaries
                "; ",         # Semicolons
                ", ",         # Commas
                " ",          # Words
                "",           # Characters (last resort)
            ]
        )
    
    def _create_contextual_chunks(
        self, pages: List[Document], doc_name: str
    ) -> Tuple[List[str], List[dict]]:
        """
        Split pages into small chunks and enrich each with contextual metadata.
        
        Returns:
            Tuple of (enriched_texts, metadatas)
        """
        # First, split into small chunks
        raw_chunks = self._text_splitter.split_documents(pages)
        
        enriched_texts = []
        metadatas = []
        
        for i, chunk in enumerate(raw_chunks):
            page_num = chunk.metadata.get("page", 0) + 1
            
            # Enrich the chunk with contextual prefix
            enriched = _enrich_chunk(chunk.page_content, doc_name, page_num)
            enriched_texts.append(enriched)
            
            metadatas.append({
                **chunk.metadata,
                "chunk_index": i,
                "page_number": page_num,
                "raw_content": chunk.page_content,  # Keep original for display
            })
        
        return enriched_texts, metadatas
    
    async def ingest_document(
        self, 
        file_path: str, 
        school_id: str,
        subject_id: str, 
        class_id: str,
        doc_type: str = "notes"
    ) -> Tuple[bool, str]:
        """
        Process a PDF document and add it to the school+class-scoped vector store.
        
        Args:
            file_path: Path to the PDF file
            school_id: UUID of the school (for data isolation)
            subject_id: UUID of the subject
            class_id: UUID of the class
            doc_type: Type of document (notes, examples, etc.)
            
        Returns:
            Tuple of (success boolean, message string)
        """
        try:
            # 1. Validate inputs
            path = Path(file_path)
            if not path.exists():
                return False, f"File not found: {file_path}"
            
            sch_uuid = UUID(school_id)
            s_uuid = UUID(subject_id)
            c_uuid = UUID(class_id)
            
            # 2. Create document record in database
            doc_create = DocumentCreate(
                school_id=sch_uuid,
                subject_id=s_uuid,
                class_id=c_uuid,
                filename=path.name,
                file_path=str(path),
                doc_type=doc_type
            )
            doc_record = self._supabase.create_document(doc_create)
            
            # 3. Backup to cloud storage (Supabase Storage)
            try:
                storage = get_storage_client()
                success_stor, result_stor = storage.upload_file(file_path)
                if success_stor:
                    print(f"Cloud backup successful for {path.name}")
            except Exception as se:
                print(f"Cloud backup failed (non-fatal): {se}")

            # 4. Load PDF
            loader = PyPDFLoader(file_path)
            pages = loader.load()
            
            if not pages:
                return False, "No text content found in PDF."
            
            # 5. Create contextually-enriched chunks
            texts, metadatas = self._create_contextual_chunks(pages, path.name)
            
            # Add document metadata to each chunk
            for meta in metadatas:
                meta["document_id"] = str(doc_record.id)
                meta["doc_type"] = doc_type
            
            # 6. Prepare vector store (school-scoped)
            vectorstore = SupabaseVectorStore(
                supabase=self._supabase,
                school_id=sch_uuid,
                subject_id=s_uuid,
                class_id=c_uuid,
                embeddings=get_embeddings()
            )
            
            # 7. Add enriched chunks to vector store
            vectorstore.add_texts(
                texts=texts,
                metadatas=metadatas,
                document_id=doc_record.id
            )
            
            # 8. Update document status
            self._supabase.update_document_indexed(
                doc_id=doc_record.id,
                chunk_count=len(texts)
            )
            
            return True, f"Successfully indexed {len(texts)} chunks from {path.name}"
            
        except Exception as e:
            import traceback
            print(f"Ingestion error: {e}")
            print(traceback.format_exc())
            return False, f"Error during ingestion: {str(e)}"


# Singleton instance
_ingestion_service: Optional[IngestionService] = None

def get_ingestion_service() -> IngestionService:
    """Get or create the ingestion service singleton."""
    global _ingestion_service
    if _ingestion_service is None:
        _ingestion_service = IngestionService()
    return _ingestion_service

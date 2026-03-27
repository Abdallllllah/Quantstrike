"""
Document Ingestion Service - Handles processing and indexing of educational content.

This service coordinates:
- Creating document metadata records
- Loading and chunking PDF files
- Generating embeddings and storing them in the school+class-scoped vector store
- Tracking indexing status
"""
import os
from uuid import UUID
from pathlib import Path
from typing import Optional, Tuple

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import DocumentCreate
from app.db.storage import get_storage_client
from app.rag.vectorstore import SupabaseVectorStore
from app.rag_legacy import get_embeddings


class IngestionService:
    """Service for processing and indexing documents for RAG."""
    
    def __init__(self, supabase: Optional[SupabaseClient] = None):
        self._supabase = supabase or get_supabase_client()
        self._text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            add_start_index=True
        )
    
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
                # Check if bucket exists/upload
                success_stor, result_stor = storage.upload_file(file_path)
                if success_stor:
                    print(f"Cloud backup successful for {path.name}")
            except Exception as se:
                print(f"Cloud backup failed (non-fatal): {se}")

            # 4. Load and split PDF
            loader = PyPDFLoader(file_path)
            pages = loader.load()
            
            if not pages:
                return False, "No text content found in PDF."
            
            chunks = self._text_splitter.split_documents(pages)
            
            # 5. Prepare vector store (school-scoped)
            vectorstore = SupabaseVectorStore(
                supabase=self._supabase,
                school_id=sch_uuid,
                subject_id=s_uuid,
                class_id=c_uuid,
                embeddings=get_embeddings()
            )
            
            # 6. Add chunks to vector store
            texts = [chunk.page_content for chunk in chunks]
            metadatas = [
                {
                    **chunk.metadata,
                    "document_id": str(doc_record.id),
                    "doc_type": doc_type
                } 
                for chunk in chunks
            ]
            
            vectorstore.add_texts(
                texts=texts,
                metadatas=metadatas,
                document_id=doc_record.id
            )
            
            # 7. Update document status
            self._supabase.update_document_indexed(
                doc_id=doc_record.id,
                chunk_count=len(chunks)
            )
            
            return True, f"Successfully indexed {len(chunks)} chunks from {path.name}"
            
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

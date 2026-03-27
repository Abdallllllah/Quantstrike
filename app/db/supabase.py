"""
Supabase client wrapper for database operations.
"""
import os
from typing import Optional
from uuid import UUID
from datetime import datetime

from supabase import create_client, Client
from dotenv import load_dotenv

from app.db.models import (
    User, UserCreate,
    Subject, Class,
    School, SchoolCreate,
    Message, MessageCreate,
    Document, DocumentCreate,
    EmbeddingCreate, SimilarityResult
)
from pydantic import TypeAdapter


load_dotenv()

# Singleton client
_supabase_client: Optional["SupabaseClient"] = None


def get_supabase_client() -> "SupabaseClient":
    """Get or create the Supabase client singleton."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = SupabaseClient()
    return _supabase_client


class SupabaseClient:
    """Wrapper around Supabase client with typed methods."""
    
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        
        if not url or not key:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment variables"
            )
        
        self.client: Client = create_client(url, key)
    
    def _to_user(self, data: dict) -> User:
        return User.model_validate(data)
        
    def _to_subject(self, data: dict) -> Subject:
        return Subject.model_validate(data)
        
    def _to_class(self, data: dict) -> Class:
        return Class.model_validate(data)
        
    def _to_message(self, data: dict) -> Message:
        return Message.model_validate(data)
        
    def _to_document(self, data: dict) -> Document:
        return Document.model_validate(data)
    
    def _to_school(self, data: dict) -> School:
        return School.model_validate(data)
    
    # ==========================================
    # SCHOOL OPERATIONS
    # ==========================================
    
    def get_all_schools(self, active_only: bool = True) -> list[School]:
        """Get all schools."""
        query = self.client.table("schools").select("*")
        if active_only:
            query = query.eq("is_active", True)
        result = query.execute()
        
        return [self._to_school(row) for row in result.data]
    
    def get_school_by_slug(self, slug: str) -> Optional[School]:
        """Get school by slug."""
        result = self.client.table("schools").select("*").eq(
            "slug", slug
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_school(result.data[0])
        return None
    
    def get_school_by_id(self, school_id: UUID) -> Optional[School]:
        """Get school by ID."""
        result = self.client.table("schools").select("*").eq(
            "id", str(school_id)
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_school(result.data[0])
        return None
    
    def create_school(self, school: SchoolCreate) -> School:
        """Create a new school."""
        result = self.client.table("schools").insert({
            "name": school.name,
            "slug": school.slug,
        }).execute()
        
        return self._to_school(result.data[0])
    
    # ==========================================
    # USER OPERATIONS
    # ==========================================
    
    def get_user_by_phone(self, phone_number: str) -> Optional[User]:
        """Get user by phone number."""
        result = self.client.table("users").select("*").eq(
            "phone_number", phone_number
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_user(result.data[0])
        return None
    
    def get_user_by_id(self, user_id: UUID) -> Optional[User]:
        """Get user by ID."""
        result = self.client.table("users").select("*").eq(
            "id", str(user_id)
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_user(result.data[0])
        return None
    
    def create_user(self, user: UserCreate) -> User:
        """Create a new user."""
        data = {
            "phone_number": user.phone_number,
            "display_name": user.display_name,
        }
        if user.school_id:
            data["school_id"] = str(user.school_id)
        
        result = self.client.table("users").insert(data).execute()
        
        return User(**result.data[0])
    
    def get_or_create_user(self, phone_number: str, school_id: Optional[UUID] = None) -> User:
        """Get existing user or create new one."""
        user = self.get_user_by_phone(phone_number)
        if user:
            return user
        return self.create_user(UserCreate(
            phone_number=phone_number,
            school_id=school_id,
        ))
    
    def update_user_context(
        self, 
        user_id: UUID, 
        subject_id: Optional[UUID] = None,
        class_id: Optional[UUID] = None
    ) -> User:
        """Update user's current subject/class context."""
        update_data = {}
        if subject_id is not None:
            update_data["current_subject_id"] = str(subject_id)
        if class_id is not None:
            update_data["current_class_id"] = str(class_id)
        
        result = self.client.table("users").update(update_data).eq(
            "id", str(user_id)
        ).execute()
        
        return User(**result.data[0])
    
    # ==========================================
    # SUBJECT OPERATIONS
    # ==========================================
    
    def get_all_subjects(self, active_only: bool = True) -> list[Subject]:
        """Get all subjects."""
        query = self.client.table("subjects").select("*")
        if active_only:
            query = query.eq("is_active", True)
        result = query.execute()
        
        return [self._to_subject(row) for row in result.data]
    
    def get_subject_by_slug(self, slug: str) -> Optional[Subject]:
        """Get subject by slug."""
        result = self.client.table("subjects").select("*").eq(
            "slug", slug
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_subject(result.data[0])
        return None
    
    def get_subject_by_id(self, subject_id: UUID) -> Optional[Subject]:
        """Get subject by ID."""
        result = self.client.table("subjects").select("*").eq(
            "id", str(subject_id)
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_subject(result.data[0])
        return None
    
    # ==========================================
    # CLASS OPERATIONS
    # ==========================================
    
    def get_classes_by_subject(
        self, 
        subject_id: UUID, 
        active_only: bool = True
    ) -> list[Class]:
        """Get all classes for a subject."""
        query = self.client.table("classes").select("*").eq(
            "subject_id", str(subject_id)
        )
        if active_only:
            query = query.eq("is_active", True)
        result = query.execute()
        
        return [self._to_class(row) for row in result.data]
    
    def get_class_by_id(self, class_id: UUID) -> Optional[Class]:
        """Get class by ID."""
        result = self.client.table("classes").select("*").eq(
            "id", str(class_id)
        ).execute()
        
        if result.data and len(result.data) > 0:
            return self._to_class(result.data[0])
        return None
    
    def create_class(
        self, 
        name: str, 
        subject_id: UUID, 
        description: Optional[str] = None
    ) -> Class:
        """Create a new class."""
        result = self.client.table("classes").insert({
            "name": name,
            "subject_id": str(subject_id),
            "description": description,
        }).execute()
        
        return Class(**result.data[0])
    
    # ==========================================
    # MESSAGE OPERATIONS
    # ==========================================
    
    def get_conversation_history(
        self,
        user_id: UUID,
        school_id: Optional[UUID] = None,
        subject_id: Optional[UUID] = None,
        class_id: Optional[UUID] = None,
        limit: int = 10
    ) -> list[Message]:
        """Get recent conversation history for a user."""
        query = self.client.table("messages").select("*").eq(
            "user_id", str(user_id)
        )
        
        if school_id:
            query = query.eq("school_id", str(school_id))
        if subject_id:
            query = query.eq("subject_id", str(subject_id))
        if class_id:
            query = query.eq("class_id", str(class_id))
        
        result = query.order("created_at", desc=True).limit(limit).execute()
        
        # Reverse to get chronological order
        messages = [self._to_message(row) for row in result.data]
        messages.reverse()
        return messages
    
    def save_message(self, message: MessageCreate) -> Message:
        """Save a message to the database."""
        data = {
            "user_id": str(message.user_id),
            "role": message.role,
            "content": message.content,
            "intent": message.intent,
            "metadata": message.metadata or {},
        }
        
        if message.school_id:
            data["school_id"] = str(message.school_id)
        if message.subject_id:
            data["subject_id"] = str(message.subject_id)
        if message.class_id:
            data["class_id"] = str(message.class_id)
        
        result = self.client.table("messages").insert(data).execute()
        
        return self._to_message(result.data[0])
    
    # ==========================================
    # DOCUMENT OPERATIONS
    # ==========================================
    
    def create_document(self, doc: DocumentCreate) -> Document:
        """Create a new document record."""
        data = {
            "subject_id": str(doc.subject_id),
            "class_id": str(doc.class_id),
            "filename": doc.filename,
            "file_path": doc.file_path,
            "doc_type": doc.doc_type,
        }
        if doc.school_id:
            data["school_id"] = str(doc.school_id)
        
        result = self.client.table("documents").insert(data).execute()
        
        return self._to_document(result.data[0])
    
    def update_document_indexed(
        self, 
        doc_id: UUID, 
        chunk_count: int
    ) -> Document:
        """Mark document as indexed with chunk count."""
        result = self.client.table("documents").update({
            "is_indexed": True,
            "chunk_count": chunk_count,
        }).eq("id", str(doc_id)).execute()
        
        return self._to_document(result.data[0])
    
    def get_documents_by_class(
        self, 
        subject_id: UUID, 
        class_id: UUID,
        school_id: Optional[UUID] = None,
    ) -> list[Document]:
        """Get all documents for a class, optionally scoped to a school."""
        query = self.client.table("documents").select("*").eq(
            "subject_id", str(subject_id)
        ).eq(
            "class_id", str(class_id)
        )
        if school_id:
            query = query.eq("school_id", str(school_id))
        
        result = query.execute()
        
        return [self._to_document(row) for row in result.data]
    
    # ==========================================
    # EMBEDDING OPERATIONS
    # ==========================================
    
    def save_embeddings(self, embeddings: list[EmbeddingCreate]) -> int:
        """Save multiple embeddings to the database."""
        if not embeddings:
            return 0
        
        data = []
        for emb in embeddings:
            row = {
                "document_id": str(emb.document_id),
                "subject_id": str(emb.subject_id),
                "class_id": str(emb.class_id),
                "content": emb.content,
                "embedding": emb.embedding,
                "page_number": emb.page_number,
                "chunk_index": emb.chunk_index,
                "metadata": emb.metadata,
            }
            if emb.school_id:
                row["school_id"] = str(emb.school_id)
            data.append(row)
        
        result = self.client.table("embeddings").insert(data).execute()
        return len(result.data)
    
    def similarity_search(
        self,
        query_embedding: list[float],
        subject_id: UUID,
        class_id: UUID,
        school_id: UUID,
        limit: int = 5,
        threshold: float = 0.7
    ) -> list[SimilarityResult]:
        """Perform similarity search using pgvector, scoped to a school."""
        result = self.client.rpc("match_embeddings", {
            "query_embedding": query_embedding,
            "match_subject_id": str(subject_id),
            "match_class_id": str(class_id),
            "match_school_id": str(school_id),
            "match_count": limit,
            "match_threshold": threshold,
        }).execute()
        
        return [SimilarityResult.model_validate(row) for row in result.data]
    
    def delete_embeddings_by_document(self, document_id: UUID) -> int:
        """Delete all embeddings for a document."""
        result = self.client.rpc("delete_document_embeddings", {
            "doc_id": str(document_id)
        }).execute()
        
        return result.data if result.data else 0

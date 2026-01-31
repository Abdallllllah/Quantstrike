"""
Pydantic models for database entities.
"""
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict
from uuid import UUID


class Subject(BaseModel):
    """Subject/course model (e.g., Math, Physics, Chemistry)."""
    id: UUID
    name: str
    slug: str
    prompt_template: Optional[str] = None
    retrieval_k: int = 5
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Class(BaseModel):
    """Class/grade model scoped to a subject."""
    id: UUID
    name: str
    subject_id: UUID
    description: Optional[str] = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class User(BaseModel):
    """User model identified by phone number."""
    id: UUID
    phone_number: str
    display_name: Optional[str] = None
    current_subject_id: Optional[UUID] = None
    current_class_id: Optional[UUID] = None
    preferences: Optional[dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    """Model for creating a new user."""
    phone_number: str
    display_name: Optional[str] = None


class Message(BaseModel):
    """Conversation message model."""
    id: UUID
    user_id: UUID
    subject_id: Optional[UUID] = None
    class_id: Optional[UUID] = None
    role: str  # 'user' or 'assistant'
    content: str
    intent: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageCreate(BaseModel):
    """Model for creating a new message."""
    user_id: UUID
    subject_id: Optional[UUID] = None
    class_id: Optional[UUID] = None
    role: str
    content: str
    intent: Optional[str] = None
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)


class Document(BaseModel):
    """Document metadata model."""
    id: UUID
    subject_id: UUID
    class_id: UUID
    filename: str
    file_path: str
    doc_type: str = "notes"
    chunk_count: int = 0
    is_indexed: bool = False
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentCreate(BaseModel):
    """Model for creating a new document."""
    subject_id: UUID
    class_id: UUID
    filename: str
    file_path: str
    doc_type: str = "notes"


class Embedding(BaseModel):
    """Vector embedding model."""
    id: UUID
    document_id: UUID
    subject_id: UUID
    class_id: UUID
    content: str
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    # Note: embedding vector is handled separately due to complexity

    model_config = ConfigDict(from_attributes=True)


class EmbeddingCreate(BaseModel):
    """Model for creating a new embedding."""
    document_id: UUID
    subject_id: UUID
    class_id: UUID
    content: str
    embedding: list[float]  # 384-dimensional vector
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)


class SimilarityResult(BaseModel):
    """Result from similarity search."""
    id: UUID
    content: str
    page_number: Optional[int] = None
    chunk_index: Optional[int] = None
    metadata: Optional[dict[str, Any]] = Field(default_factory=dict)
    similarity: float


class ConversationContext(BaseModel):
    """Conversation context passed to RAG functions."""
    user_id: UUID
    subject_id: UUID
    class_id: UUID
    history: list[Message] = Field(default_factory=list)
    
    def format_history(self, max_messages: int = 5) -> str:
        """Format recent history as a string for prompt injection."""
        recent = self.history[-max_messages:] if len(self.history) > max_messages else self.history
        if not recent:
            return ""
        
        formatted = []
        for msg in recent:
            role_label = "Student" if msg.role == "user" else "Tutor"
            formatted.append(f"{role_label}: {msg.content}")
        
        return "\n".join(formatted)

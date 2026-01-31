"""
RAG Service Orchestrator - Clean integration for retrieval and response generation.

Intent classification is handled upstream by another service.
This module focuses solely on:
- User context management
- RAG pipeline invocation
- Response formatting
"""
from typing import Optional, Any
from uuid import UUID
from datetime import datetime
import os

from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import (
    User, Message, MessageCreate, 
    Subject, Class, ConversationContext
)
from app.rag.registry import RAGRegistry, get_rag_registry
from app.context.manager import ContextManager, get_context_manager

# Singleton orchestrator
_orchestrator: Optional["Orchestrator"] = None


def get_orchestrator() -> "Orchestrator":
    """Get or create the orchestrator singleton."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(
            supabase=get_supabase_client(),
            rag_registry=get_rag_registry(),
            context_manager=get_context_manager(),
        )
    return _orchestrator


class Orchestrator:
    """
    RAG service orchestrator for retrieval and response generation.
    
    This is the entry point for the RAG API. Intent classification is
    handled upstream by another service.
    
    Flow:
    1. Receive message with user_id, subject, class
    2. Resolve subject and class from provided identifiers
    3. Load conversation history
    4. Call RAG pipeline for retrieval and generation
    5. Save response
    6. Return formatted response
    """
    
    DEFAULT_HISTORY_LIMIT = 5
    
    def __init__(
        self,
        supabase: SupabaseClient,
        rag_registry: RAGRegistry,
        context_manager: ContextManager,
    ):
        self._supabase = supabase
        self._rag = rag_registry
        self._context = context_manager
    
    async def process_message(
        self,
        user_id: str,
        message: str,
        subject: str,
        class_level: str,
        **kwargs
    ) -> dict[str, Any]:
        """
        Process a message through the RAG pipeline.
        
        Intent classification is handled upstream. This method focuses
        solely on retrieval and response generation.
        
        Args:
            user_id: User identifier
            message: The user's query text
            subject: Subject/topic identifier (slug or ID)
            class_level: Class/grade level identifier (slug or ID)
            **kwargs: Additional metadata
        
        Returns:
            dict with:
                - response: The assistant's response text
                - subject: Current subject
                - class: Current class
                - sources: Retrieved document sources
                - metadata: Additional response metadata
        """
        try:
            # Step 1: Resolve subject and class
            subject_obj = self._resolve_subject(subject)
            class_obj = self._resolve_class(class_level, subject_obj)
            
            if not subject_obj:
                return {
                    "response": f"Subject '{subject}' not found. Please provide a valid subject.",
                    "error": "subject_not_found",
                }
            
            if not class_obj:
                return {
                    "response": f"Class '{class_level}' not found for subject '{subject}'. Please provide a valid class.",
                    "error": "class_not_found",
                }
            
            # Step 2: Get or create user
            user = self._get_or_create_user(user_id)
            
            # Step 3: Update user's current context
            self._supabase.update_user_context(
                user.id, subject_obj.id, class_obj.id
            )
            
            # Step 4: Load conversation history
            history = self._context.get_history(
                user_id=user.id,
                subject_id=subject_obj.id,
                class_id=class_obj.id,
                limit=self.DEFAULT_HISTORY_LIMIT,
            )
            
            # Build conversation context
            context = ConversationContext(
                user_id=user.id,
                subject_id=subject_obj.id,
                class_id=class_obj.id,
                history=history,
            )
            
            # Step 5: Save user message
            self._save_message(
                user_id=user.id,
                subject_id=subject_obj.id,
                class_id=class_obj.id,
                role="user",
                content=message,
            )
            
            # Step 6: Call RAG pipeline for retrieval and generation
            rag_response = await self._rag.process_request(
                intent="question",
                message=message,
                context=context,
            )
            
            # Step 7: Save assistant response
            response_text = self._extract_response_text(rag_response)
            self._save_message(
                user_id=user.id,
                subject_id=subject_obj.id,
                class_id=class_obj.id,
                role="assistant",
                content=response_text,
            )
            
            # Step 8: Update context cache
            self._context.invalidate(user.id, subject_obj.id, class_obj.id)
            
            # Return formatted response
            return {
                "response": response_text,
                "subject": subject_obj.name,
                "class": class_obj.name,
                "sources": rag_response.get("sources", []),
                "metadata": rag_response,
            }
            
        except Exception as e:
            import traceback
            print(f"Orchestrator error: {e}")
            print(traceback.format_exc())
            return {
                "response": "I'm sorry, I encountered an error processing your request. Please try again.",
                "error": str(e),
                "traceback": traceback.format_exc() if os.getenv("DEBUG") == "true" else None
            }
    
    def _resolve_subject(self, subject: str) -> Optional[Subject]:
        """
        Resolve subject from identifier (slug or UUID).
        
        Args:
            subject: Subject slug (e.g., "physics") or UUID string
        
        Returns:
            Subject object or None if not found
        """
        # Try as slug first
        subject_obj = self._supabase.get_subject_by_slug(subject)
        if subject_obj:
            return subject_obj
        
        # Try as UUID
        try:
            uuid_obj = UUID(subject)
            return self._supabase.get_subject_by_id(uuid_obj)
        except (ValueError, AttributeError):
            return None
    
    def _resolve_class(self, class_level: str, subject: Optional[Subject]) -> Optional[Class]:
        """
        Resolve class from identifier.
        
        Args:
            class_level: Class identifier (name, slug, or UUID)
            subject: The resolved subject object
        
        Returns:
            Class object or None if not found
        """
        if not subject:
            return None
        
        # Get all classes for the subject
        classes = self._supabase.get_classes_by_subject(subject.id)
        
        # Try to match by name or slug
        class_level_lower = class_level.lower()
        for cls in classes:
            if cls.name.lower() == class_level_lower:
                return cls
            # Also check if the class_level is contained in the name
            if class_level_lower in cls.name.lower():
                return cls
        
        # Try as UUID
        try:
            uuid_obj = UUID(class_level)
            return self._supabase.get_class_by_id(uuid_obj)
        except (ValueError, AttributeError):
            pass
        
        # Return first class as default if available
        return classes[0] if classes else None
    
    def _get_or_create_user(self, user_id: str) -> User:
        """
        Get or create a user by identifier.
        
        Uses user_id as phone_number for compatibility with existing schema.
        """
        return self._supabase.get_or_create_user(user_id)
    
    def _save_message(
        self,
        user_id: UUID,
        subject_id: Optional[UUID],
        class_id: Optional[UUID],
        role: str,
        content: str,
    ):
        """Save a message to the database."""
        u_id = UUID(str(user_id)) if user_id else None
        s_id = UUID(str(subject_id)) if subject_id else None
        c_id = UUID(str(class_id)) if class_id else None
        
        self._supabase.save_message(MessageCreate(
            user_id=u_id,
            subject_id=s_id,
            class_id=c_id,
            role=role,
            content=content,
            intent="question",  # Default intent for RAG queries
        ))
    
    def _extract_response_text(self, rag_response: dict) -> str:
        """Extract the main response text from RAG output."""
        if "answer" in rag_response:
            return rag_response["answer"]
        if "response" in rag_response:
            return rag_response["response"]
        return str(rag_response)

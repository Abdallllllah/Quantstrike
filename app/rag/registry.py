"""
RAG Registry - Central orchestration for subject/class RAG selection.

This module provides the main registry that:
- Maps subjects to RAG configurations
- Maps (subject, class) pairs to vector stores
- Routes intents to existing RAG functions
"""
from typing import Optional, Callable, Any
from uuid import UUID
from functools import lru_cache

from langchain.prompts import PromptTemplate
from langchain.chains import RetrievalQA
from langchain_core.vectorstores import VectorStore

from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import Subject, Class, SimilarityResult, ConversationContext
from app.rag.base import SubjectRAGConfig, DEFAULT_CONFIGS
from app.rag.vectorstore import SupabaseVectorStore

# Import existing RAG functions (these will NOT be modified)
from app.rag_legacy import (
    get_llm,
    get_embeddings,
    ask_question as legacy_ask_question,
    generate_practice_questions as legacy_generate_practice,
    mark_student_answer as legacy_mark_answer,
)

# Singleton registry
_registry: Optional["RAGRegistry"] = None


def get_rag_registry() -> "RAGRegistry":
    """Get or create the RAG registry singleton."""
    global _registry
    if _registry is None:
        _registry = RAGRegistry(get_supabase_client())
    return _registry


class RAGRegistry:
    """
    Central registry for subject/class RAG selection.
    
    This is the single source of truth for:
    - Subject configurations (prompts, retrieval settings)
    - Class-scoped vector stores
    - Intent-to-function mapping
    """
    
    def __init__(self, supabase: SupabaseClient):
        self._supabase = supabase
        self._config_cache: dict[str, SubjectRAGConfig] = {}
        self._store_cache: dict[tuple[str, str, str], SupabaseVectorStore] = {}
        
        # Map intents to handler methods
        # These call the existing RAG functions with proper context
        self._intent_handlers = {
            "question": self._handle_question,
            "practice": self._handle_practice,
            "mark": self._handle_mark,
        }
    
    def get_subject_config(self, subject_id: str) -> SubjectRAGConfig:
        """
        Load subject configuration from database with caching.
        
        Falls back to default configs if database entry has no custom template.
        """
        if subject_id in self._config_cache:
            return self._config_cache[subject_id]
        
        # Fetch from database
        subject = self._supabase.get_subject_by_id(UUID(subject_id))
        if not subject:
            raise ValueError(f"Subject not found: {subject_id}")
        
        # Check for default config
        base_config = DEFAULT_CONFIGS.get(subject.slug)
        
        # Validate database template - must contain required variables
        # for StuffDocumentsChain to work properly
        db_template = subject.prompt_template
        if db_template and not ('{context}' in db_template and '{question}' in db_template):
            # Invalid template - missing required variables, ignore it
            db_template = None
        
        # Create config with database overrides
        config = SubjectRAGConfig(
            subject_id=str(subject.id),
            subject_name=subject.name,
            slug=subject.slug,
            prompt_template=db_template or (
                base_config.prompt_template if base_config else None
            ),
            retrieval_k=subject.retrieval_k,
            system_context=base_config.system_context if base_config else "You are a helpful tutor.",
        )
        
        self._config_cache[subject_id] = config
        return config
    
    def get_subject_by_slug(self, slug: str) -> Optional[Subject]:
        """Get subject by its slug identifier."""
        return self._supabase.get_subject_by_slug(slug)
    
    def get_vectorstore(
        self, 
        school_id: str,
        subject_id: str, 
        class_id: str
    ) -> SupabaseVectorStore:
        """
        Get school+class-scoped vector store.
        
        Creates a new SupabaseVectorStore instance scoped to the 
        specific school, subject, and class for filtering during retrieval.
        """
        key = (school_id, subject_id, class_id)
        if key not in self._store_cache:
            store = SupabaseVectorStore(
                supabase=self._supabase,
                school_id=UUID(school_id),
                subject_id=UUID(subject_id),
                class_id=UUID(class_id),
                embeddings=get_embeddings(),
            )
            self._store_cache[key] = store
        return self._store_cache[key]
    
    def get_intent_handler(self, intent: str) -> Callable:
        """Get the handler function for an intent."""
        return self._intent_handlers.get(intent, self._handle_question)
    
    async def process_request(
        self,
        intent: str,
        message: str,
        context: ConversationContext,
        **kwargs
    ) -> dict[str, Any]:
        """
        Process a request through the appropriate RAG pipeline.
        
        This is the main entry point called by the orchestrator.
        
        Args:
            intent: Detected intent (question, practice, mark)
            message: User's message content
            context: Conversation context with user, subject, class info
            **kwargs: Additional parameters (e.g., difficulty for practice)
        
        Returns:
            dict with response data
        """
        handler = self.get_intent_handler(intent)
        return await handler(message, context, **kwargs)
    
    async def _handle_question(
        self,
        query: str,
        context: ConversationContext,
        **kwargs
    ) -> dict[str, Any]:
        """Handle a question intent using the RAG pipeline."""
        config = self.get_subject_config(str(context.subject_id))
        vectorstore = self.get_vectorstore(
            str(context.school_id),
            str(context.subject_id), 
            str(context.class_id)
        )
        
        # Build conversation-aware prompt
        history_text = context.format_history(max_messages=5)
        prompt_with_history = self._inject_history(
            config.get_qa_prompt(), 
            history_text
        )
        
        llm = get_llm()
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": config.retrieval_k}
        )
        
        qa_prompt = PromptTemplate.from_template(prompt_with_history)
        
        # Debug logging
        print(f"DEBUG: Prompt template input_variables: {qa_prompt.input_variables}")
        print(f"DEBUG: Prompt ends with: {prompt_with_history[-100:]}")
        
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": qa_prompt}
        )
        
        result = qa_chain.invoke({"query": query})
        
        # Extract unique sources
        sources = list(set(
            doc.metadata.get("source", "Unknown")
            for doc in result.get("source_documents", [])
        ))
        
        return {
            "answer": result["result"],
            "sources": sources,
            "intent": "question",
        }
    
    async def _handle_practice(
        self,
        topic: str,
        context: ConversationContext,
        difficulty: str = "medium",
        count: int = 5,
        **kwargs
    ) -> dict[str, Any]:
        """Handle a practice question generation request."""
        config = self.get_subject_config(str(context.subject_id))
        vectorstore = self.get_vectorstore(
            str(context.school_id),
            str(context.subject_id), 
            str(context.class_id)
        )
        
        llm = get_llm()
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": 10}  # More context for practice generation
        )
        
        # Build prompt with difficulty and count
        # Note: f-string in get_practice_prompt() converts {{ to { so we replace single braces
        prompt_template = config.get_practice_prompt()
        prompt_template = prompt_template.replace("{difficulty}", difficulty)
        prompt_template = prompt_template.replace("{count}", str(count))
        
        qa_prompt = PromptTemplate.from_template(prompt_template)
        
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": qa_prompt}
        )
        
        result = qa_chain.invoke({"query": topic})
        
        return {
            "topic": topic,
            "difficulty": difficulty,
            "count": count,
            "questions": result["result"],
            "intent": "practice",
        }
    
    async def _handle_mark(
        self,
        question: str,
        context: ConversationContext,
        student_answer: str = "",
        max_marks: int = 5,
        **kwargs
    ) -> dict[str, Any]:
        """Handle answer marking request."""
        config = self.get_subject_config(str(context.subject_id))
        vectorstore = self.get_vectorstore(
            str(context.school_id),
            str(context.subject_id), 
            str(context.class_id)
        )
        
        llm = get_llm()
        retriever = vectorstore.as_retriever(
            search_kwargs={"k": 8}
        )
        
        # Build prompt with student answer and max marks
        # Note: f-string in get_marking_prompt() converts {{ to { so we replace single braces
        prompt_template = config.get_marking_prompt()
        prompt_template = prompt_template.replace("{student_answer}", student_answer)
        prompt_template = prompt_template.replace("{max_marks}", str(max_marks))
        
        qa_prompt = PromptTemplate.from_template(prompt_template)
        
        qa_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,
            chain_type_kwargs={"prompt": qa_prompt}
        )
        
        result = qa_chain.invoke({"query": question})
        
        return {
            "question": question,
            "student_answer": student_answer,
            "max_marks": max_marks,
            "marking_result": result["result"],
            "intent": "mark",
        }
    
    def _inject_history(self, prompt: str, history: str) -> str:
        """Inject conversation history into a prompt template."""
        if not history:
            return prompt
        
        history_section = f"""
Previous Conversation:
{history}

---
"""
        # Insert after "Context:" section
        if "Context:" in prompt:
            return prompt.replace(
                "Context:", 
                f"Previous Conversation:\n{history}\n\n---\n\nContext:"
            )
        return history_section + prompt
    
    def clear_cache(self):
        """Clear all cached configurations and stores."""
        self._config_cache.clear()
        self._store_cache.clear()
    
    def invalidate_subject(self, subject_id: str):
        """Invalidate cache for a specific subject."""
        if subject_id in self._config_cache:
            del self._config_cache[subject_id]
        
        # Remove all stores for this subject
        keys_to_remove = [
            key for key in self._store_cache 
            if key[1] == subject_id
        ]
        for key in keys_to_remove:
            del self._store_cache[key]

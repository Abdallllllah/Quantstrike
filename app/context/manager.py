"""
Conversation context manager.

Handles loading and caching of conversation history for RAG context injection.
"""
from typing import Optional
from uuid import UUID
from datetime import datetime, timedelta
from collections import OrderedDict

from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import Message

# Singleton
_context_manager: Optional["ContextManager"] = None


def get_context_manager() -> "ContextManager":
    """Get or create the context manager singleton."""
    global _context_manager
    if _context_manager is None:
        _context_manager = ContextManager(get_supabase_client())
    return _context_manager


class LRUCache:
    """Simple LRU cache with TTL support."""
    
    def __init__(self, maxsize: int = 100, ttl_seconds: int = 300):
        self.maxsize = maxsize
        self.ttl = timedelta(seconds=ttl_seconds)
        self._cache: OrderedDict[str, tuple[datetime, any]] = OrderedDict()
    
    def get(self, key: str) -> Optional[any]:
        """Get value if exists and not expired."""
        if key not in self._cache:
            return None
        
        timestamp, value = self._cache[key]
        if datetime.now() - timestamp > self.ttl:
            del self._cache[key]
            return None
        
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        return value
    
    def set(self, key: str, value: any):
        """Set value with current timestamp."""
        if key in self._cache:
            del self._cache[key]
        
        self._cache[key] = (datetime.now(), value)
        
        # Evict oldest if over capacity
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)
    
    def invalidate(self, key: str):
        """Remove a key from cache."""
        if key in self._cache:
            del self._cache[key]
    
    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()


class ContextManager:
    """
    Manages conversation context for RAG functions.
    
    Features:
    - Loads conversation history from Supabase
    - Caches recent contexts for fast access
    - School, subject, and class-aware history retrieval
    """
    
    def __init__(
        self, 
        supabase: SupabaseClient,
        cache_size: int = 100,
        cache_ttl: int = 300,  # 5 minutes
    ):
        self._supabase = supabase
        self._cache = LRUCache(maxsize=cache_size, ttl_seconds=cache_ttl)
    
    def _make_cache_key(
        self, 
        user_id: UUID,
        school_id: UUID,
        subject_id: UUID, 
        class_id: UUID
    ) -> str:
        """Create cache key from context identifiers (includes school for isolation)."""
        return f"{user_id}:{school_id}:{subject_id}:{class_id}"
    
    def get_history(
        self,
        user_id: UUID,
        school_id: UUID,
        subject_id: UUID,
        class_id: UUID,
        limit: int = 10,
    ) -> list[Message]:
        """
        Get conversation history for a user in a specific school/subject/class.
        
        Uses cache for fast repeated access.
        """
        cache_key = self._make_cache_key(user_id, school_id, subject_id, class_id)
        
        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached[:limit]
        
        # Fetch from database
        messages = self._supabase.get_conversation_history(
            user_id=user_id,
            school_id=school_id,
            subject_id=subject_id,
            class_id=class_id,
            limit=limit,
        )
        
        # Cache the result
        self._cache.set(cache_key, messages)
        
        return messages
    
    def invalidate(
        self,
        user_id: UUID,
        school_id: UUID,
        subject_id: UUID,
        class_id: UUID,
    ):
        """Invalidate cache for a specific context."""
        cache_key = self._make_cache_key(user_id, school_id, subject_id, class_id)
        self._cache.invalidate(cache_key)
    
    def invalidate_user(self, user_id: UUID):
        """Invalidate all cached contexts for a user."""
        # Since our LRU cache doesn't support partial key matching,
        # we'd need to iterate. For now, just clear all.
        # In production, consider using a more sophisticated cache.
        self._cache.clear()
    
    def format_history_for_prompt(
        self,
        messages: list[Message],
        max_messages: int = 5,
        max_chars: int = 2000,
    ) -> str:
        """
        Format conversation history for prompt injection.
        
        Args:
            messages: List of messages (oldest first)
            max_messages: Maximum number of messages to include
            max_chars: Maximum total characters
        
        Returns:
            Formatted string for prompt injection
        """
        if not messages:
            return ""
        
        # Take last N messages
        recent = messages[-max_messages:] if len(messages) > max_messages else messages
        
        formatted = []
        total_chars = 0
        
        for msg in recent:
            role_label = "Student" if msg.role == "user" else "Tutor"
            line = f"{role_label}: {msg.content}"
            
            if total_chars + len(line) > max_chars:
                break
            
            formatted.append(line)
            total_chars += len(line)
        
        if not formatted:
            return ""
        
        return "\n\n".join(formatted)

# Database package
from app.db.supabase import SupabaseClient, get_supabase_client
from app.db.models import User, Subject, Class, Message, Document, Embedding

__all__ = [
    "SupabaseClient",
    "get_supabase_client",
    "User",
    "Subject", 
    "Class",
    "Message",
    "Document",
    "Embedding",
]

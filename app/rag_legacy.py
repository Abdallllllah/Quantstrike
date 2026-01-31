"""
Legacy RAG module wrapper.

This file re-exports the existing RAG functions from the original rag.py
to maintain backward compatibility while allowing the new architecture
to use them as pluggable modules.

The original rag.py is preserved and these functions continue to work
for browser-based requests. The new orchestrator wraps these functions
with additional context management.
"""
# Re-export from original rag module
# Note: The original rag.py should be kept as-is

import os
import shutil
from pathlib import Path
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_groq import ChatGroq
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from dotenv import load_dotenv

load_dotenv()

# Configuration (same as original)
DB_PATH = "vectorstore"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
GROQ_MODEL_NAME = "llama-3.3-70b-versatile"

# Singletons
_vectorstore = None
_embeddings = None
_llm = None


def get_llm():
    """Get or create LLM instance."""
    global _llm
    if _llm is None:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not found in environment variables")
        _llm = ChatGroq(model=GROQ_MODEL_NAME, groq_api_key=api_key, temperature=0.3)
    return _llm


def get_embeddings():
    """Get or create embeddings model."""
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    return _embeddings


def get_vectorstore():
    """Get or create local FAISS vectorstore."""
    global _vectorstore
    if _vectorstore is None:
        embeddings = get_embeddings()
        if os.path.exists(DB_PATH) and os.path.isdir(DB_PATH):
            try:
                _vectorstore = FAISS.load_local(
                    DB_PATH, embeddings, allow_dangerous_deserialization=True
                )
            except Exception as e:
                print(f"Failed to load vectorstore: {e}")
                _vectorstore = None
    return _vectorstore


# Original functions - kept for backward compatibility with browser interface
# These are called by both the legacy API and wrapped by the new registry

def ask_question(query: str):
    """Original ask_question - used by legacy API routes."""
    from app.rag_original import ask_question as original_ask
    return original_ask(query)


def generate_practice_questions(topic: str, difficulty: str = "medium", count: int = 5):
    """Original generate_practice_questions - used by legacy API routes."""
    from app.rag_original import generate_practice_questions as original_practice
    return original_practice(topic, difficulty, count)


def mark_student_answer(question: str, student_answer: str, max_marks: int = 5):
    """Original mark_student_answer - used by legacy API routes."""
    from app.rag_original import mark_student_answer as original_mark
    return original_mark(question, student_answer, max_marks)

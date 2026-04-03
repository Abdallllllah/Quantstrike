"""
Re-ingestion script: Downloads PDFs from Supabase Storage and re-embeds
them with the new OpenAI text-embedding-3-large model.

Usage:
    .\.venv\Scripts\python.exe reingest.py
"""
import os
import sys
import asyncio
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from app.db.supabase import get_supabase_client
from app.rag.ingestion import get_ingestion_service


DOCUMENTS_BUCKET = "documents"


async def reingest_all():
    """Re-ingest all documents from Supabase."""
    client = get_supabase_client()
    
    # 1. Get all documents from the database
    result = client.client.table("documents").select("*").execute()
    documents = result.data
    
    if not documents:
        print("No documents found in the database.")
        return
    
    print(f"Found {len(documents)} documents to re-ingest.\n")
    
    # 2. Get the storage client
    storage = client.client.storage.from_(DOCUMENTS_BUCKET)
    
    # 3. List files available in storage
    try:
        storage_files = storage.list()
        available_files = {f["name"] for f in storage_files}
        print(f"Files in Supabase Storage: {available_files}\n")
    except Exception as e:
        print(f"Warning: Could not list storage files: {e}")
        available_files = set()
    
    # 4. Clear old embeddings (migration should have done this, but just in case)
    try:
        client.client.table("embeddings").delete().neq("id", "00000000-0000-0000-0000-000000000000").execute()
        print("Cleared old embeddings.\n")
    except Exception as e:
        print(f"Note: Could not clear embeddings: {e}\n")
    
    ingestion_service = get_ingestion_service()
    success_count = 0
    fail_count = 0
    
    for i, doc in enumerate(documents, 1):
        filename = doc["filename"]
        doc_id = doc["id"]
        school_id = doc.get("school_id")
        subject_id = doc["subject_id"]
        class_id = doc["class_id"]
        doc_type = doc.get("doc_type", "notes")
        
        print(f"[{i}/{len(documents)}] Processing: {filename}")
        
        if not school_id:
            print(f"  ⚠ Skipping — no school_id assigned")
            fail_count += 1
            continue
        
        # Try to download from Supabase Storage
        temp_path = None
        try:
            # Try the filename directly
            file_bytes = storage.download(filename)
            
            if not file_bytes:
                print(f"  ⚠ Empty file in storage, skipping")
                fail_count += 1
                continue
            
            # Save to temp file
            temp_dir = Path("uploads")
            temp_dir.mkdir(exist_ok=True)
            temp_path = str(temp_dir / filename)
            
            with open(temp_path, "wb") as f:
                f.write(file_bytes)
            
            print(f"  ✓ Downloaded from storage ({len(file_bytes)} bytes)")
            
        except Exception as e:
            print(f"  ⚠ Could not download '{filename}' from storage: {e}")
            
            # Fallback: check if file exists locally
            local_path = doc.get("file_path", f"uploads/{filename}")
            if os.path.exists(local_path):
                temp_path = local_path
                print(f"  ✓ Found local file at {local_path}")
            else:
                print(f"  ✗ File not available locally either, skipping")
                fail_count += 1
                continue
        
        # Delete the old document record and create fresh
        try:
            # Delete old doc record (and its embeddings via cascade/manual)
            client.client.table("embeddings").delete().eq(
                "document_id", doc_id
            ).execute()
            client.client.table("documents").delete().eq(
                "id", doc_id
            ).execute()
        except Exception as e:
            print(f"  Note: cleanup of old record: {e}")
        
        # Re-ingest with new embeddings
        try:
            success, msg = await ingestion_service.ingest_document(
                file_path=temp_path,
                school_id=school_id,
                subject_id=subject_id,
                class_id=class_id,
                doc_type=doc_type,
            )
            
            if success:
                print(f"  ✓ {msg}")
                success_count += 1
            else:
                print(f"  ✗ {msg}")
                fail_count += 1
                
        except Exception as e:
            print(f"  ✗ Ingestion error: {e}")
            fail_count += 1
    
    print(f"\n{'='*50}")
    print(f"Re-ingestion complete!")
    print(f"  ✓ Success: {success_count}")
    print(f"  ✗ Failed:  {fail_count}")
    print(f"  Total:     {len(documents)}")


if __name__ == "__main__":
    print("=" * 50)
    print("RAG Re-Ingestion Script")
    print("Model: OpenAI text-embedding-3-large (1536 dims)")
    print("=" * 50 + "\n")
    
    asyncio.run(reingest_all())

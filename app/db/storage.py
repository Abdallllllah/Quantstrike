"""
Supabase Storage helper for cloud file uploads.

This module provides a simple interface to upload files to Supabase Storage
buckets, ensuring PDFs persist even when containers restart.
"""
import os
from typing import Optional, Tuple
from pathlib import Path

from supabase import Client
from dotenv import load_dotenv

load_dotenv()

# Bucket name for document storage
DOCUMENTS_BUCKET = "documents"


class StorageClient:
    """Helper for Supabase Storage operations."""
    
    def __init__(self, supabase_client: Client):
        self.client = supabase_client
        self._ensure_bucket_exists()
    
    def _ensure_bucket_exists(self):
        """Create the documents bucket if it doesn't exist."""
        try:
            # List buckets to check if ours exists
            buckets = self.client.storage.list_buckets()
            bucket_names = [b.name for b in buckets]
            
            if DOCUMENTS_BUCKET not in bucket_names:
                # Create the bucket (public=False for security)
                self.client.storage.create_bucket(
                    DOCUMENTS_BUCKET,
                    options={"public": False}
                )
        except Exception as e:
            # Bucket might already exist or we don't have create permissions
            print(f"Bucket check/create info: {e}")
    
    def upload_file(
        self, 
        file_path: str, 
        destination_path: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Upload a file to Supabase Storage.
        
        Args:
            file_path: Local path to the file
            destination_path: Optional custom path in the bucket
            
        Returns:
            Tuple of (success, message/url)
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return False, f"File not found: {file_path}"
            
            # Use filename if no destination specified
            dest = destination_path or path.name
            
            with open(file_path, "rb") as f:
                file_content = f.read()
            
            # Upload to Supabase Storage
            result = self.client.storage.from_(DOCUMENTS_BUCKET).upload(
                path=dest,
                file=file_content,
                file_options={"content-type": "application/pdf"}
            )
            
            # Get public URL (or signed URL for private buckets)
            url = self.client.storage.from_(DOCUMENTS_BUCKET).get_public_url(dest)
            
            return True, url
            
        except Exception as e:
            return False, f"Upload failed: {str(e)}"
    
    def delete_file(self, file_path: str) -> Tuple[bool, str]:
        """
        Delete a file from Supabase Storage.
        
        Args:
            file_path: Path to the file in the bucket
            
        Returns:
            Tuple of (success, message)
        """
        try:
            self.client.storage.from_(DOCUMENTS_BUCKET).remove([file_path])
            return True, f"Deleted {file_path}"
        except Exception as e:
            return False, f"Delete failed: {str(e)}"
    
    def list_files(self, folder: str = "") -> list[str]:
        """List files in a folder."""
        try:
            files = self.client.storage.from_(DOCUMENTS_BUCKET).list(folder)
            return [f["name"] for f in files]
        except Exception as e:
            print(f"List files error: {e}")
            return []


# Singleton
_storage_client: Optional[StorageClient] = None


def get_storage_client() -> StorageClient:
    """Get or create the storage client singleton."""
    global _storage_client
    if _storage_client is None:
        from app.db.supabase import get_supabase_client
        supabase = get_supabase_client()
        _storage_client = StorageClient(supabase.client)
    return _storage_client

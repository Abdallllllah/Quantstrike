import asyncio
import os
from uuid import UUID
from app.db.supabase import get_supabase_client
from app.rag_original import PyPDFLoader, RecursiveCharacterTextSplitter, get_embeddings
from app.db.models import DocumentCreate, EmbeddingCreate

async def ingest_file(file_path: str, subject_slug: str, class_name: str):
    """
    Ingests a PDF into Supabase pgvector for a specific subject and class.
    """
    if not os.path.exists(file_path):
        print(f"Error: File '{file_path}' not found.")
        return

    supabase = get_supabase_client()
    embeddings_model = get_embeddings()
    
    # 1. Resolve Subject
    subject = supabase.get_subject_by_slug(subject_slug)
    if not subject:
        print(f"Error: Subject '{subject_slug}' not found in Supabase.")
        return

    # 2. Resolve or Create Class
    classes = supabase.get_classes_by_subject(subject.id)
    target_class = next((c for c in classes if c.name == class_name), None)
    
    if not target_class:
        print(f"Class '{class_name}' not found for {subject.name}. Creating it...")
        # Since our client wrapper might not have create_class yet, we'll use a direct insert
        # or assume the user will create it. Let's try to add it.
        try:
           target_class = supabase.create_class(class_name, subject.id)
        except Exception as e:
            print(f"Could not create class: {e}")
            return

    # 3. Load and Split PDF
    print(f"--- Processing {os.path.basename(file_path)} ---")
    loader = PyPDFLoader(file_path)
    docs = loader.load()
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    splits = text_splitter.split_documents(docs)
    print(f"Split into {len(splits)} chunks.")

    # 4. Create Document Record
    doc_record = supabase.create_document(DocumentCreate(
        subject_id=subject.id,
        class_id=target_class.id,
        filename=os.path.basename(file_path),
        file_path=file_path
    ))

    # 5. Generate Embeddings
    print(f"Generating embeddings using {embeddings_model.model_name}...")
    texts = [s.page_content for s in splits]
    
    # We'll batch these to avoid potential timeout or API limits
    batch_size = 50
    all_vectors = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i+batch_size]
        print(f"  Encoding batch {i//batch_size + 1}...")
        vectors = embeddings_model.embed_documents(batch)
        all_vectors.extend(vectors)
    
    # 6. Save to Supabase
    print(f"Saving to Supabase...")
    emb_records = []
    for i, (text, vector) in enumerate(zip(texts, all_vectors)):
        emb_records.append(EmbeddingCreate(
            document_id=doc_record.id,
            subject_id=subject.id,
            class_id=target_class.id,
            content=text,
            embedding=vector,
            chunk_index=i,
            metadata={"source": os.path.basename(file_path)}
        ))
    
    count = supabase.save_embeddings(emb_records)
    supabase.update_document_indexed(doc_record.id, count)
    print(f"✅ SUCCESS: Ingested {count} chunks into {subject.name} -> {target_class.name}")
    print(f"You can now test RAG for this subject on WhatsApp/API!")

if __name__ == "__main__":
    # CONFIGURATION: Update these values as needed
    PDF_FILE = "uploads/Physics_Notes.pdf" # Place a real PDF here or rename
    SUBJECT = "physics"                    # slug from your subjects table
    CLASS = "Grade 10"                     # Name of the class
    
    # Look for the first PDF in uploads if the default doesn't exist
    if not os.path.exists(PDF_FILE):
        uploads = [f for f in os.listdir("uploads") if f.endswith(".pdf")]
        if uploads:
            PDF_FILE = os.path.join("uploads", uploads[0])
            print(f"Default file not found, using: {PDF_FILE}")

    asyncio.run(ingest_file(PDF_FILE, SUBJECT, CLASS))

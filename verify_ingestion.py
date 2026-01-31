import asyncio
from app.rag.ingestion import get_ingestion_service

async def test_ingestion():
    service = get_ingestion_service()
    
    # Mathematics Subject and Grade 10 Class
    subject_id = 'aa3949e5-9121-4590-9ed5-dfc406deedc8'
    class_id = 'd44f0085-7011-4418-bcac-d53a917e5110'
    file_path = 'uploads/physics module 1.pdf'
    
    print(f"Starting ingestion of {file_path}...")
    result, msg = await service.ingest_document(
        file_path=file_path,
        subject_id=subject_id,
        class_id=class_id,
        doc_type="notes"
    )
    
    print(f"Result: {result}")
    print(f"Message: {msg}")

if __name__ == "__main__":
    asyncio.run(test_ingestion())

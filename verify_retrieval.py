import asyncio
from app.rag.vectorstore import SupabaseVectorStore
from app.db.supabase import get_supabase_client
from app.rag_legacy import get_embeddings
from uuid import UUID

async def test_retrieval():
    supabase = get_supabase_client()
    subject_id = UUID('aa3949e5-9121-4590-9ed5-dfc406deedc8')
    class_id = UUID('d44f0085-7011-4418-bcac-d53a917e5110')
    
    vectorstore = SupabaseVectorStore(
        supabase=supabase,
        subject_id=subject_id,
        class_id=class_id,
        embeddings=get_embeddings(),
        similarity_threshold=0.1  # Very low for debugging
    )
    
    query = "What is the content of this physics module?"
    print(f"Querying: '{query}' for Subject: {subject_id}, Class: {class_id}...")
    
    results = vectorstore.similarity_search_with_score(query, k=3)
    
    print(f"Found {len(results)} results:")
    for i, (doc, score) in enumerate(results):
        print(f"\nResult {i+1} (Score: {score:.4f}):")
        print(f"Source: {doc.metadata.get('source')}")
        print(f"Snippet: {doc.page_content[:200]}...")

if __name__ == "__main__":
    asyncio.run(test_retrieval())

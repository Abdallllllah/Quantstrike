import os
import shutil
from pathlib import Path
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import ChatOpenAI
from langchain.chains import RetrievalQA
from langchain.prompts import PromptTemplate
from dotenv import load_dotenv

load_dotenv()

# Configuration
DB_PATH = "vectorstore"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
OPENROUTER_MODEL_NAME = os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-lite-001")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Singleton
_vectorstore = None
_embeddings = None
_llm = None

def get_llm():
    global _llm
    if _llm is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not found in environment variables")
        _llm = ChatOpenAI(
            model=OPENROUTER_MODEL_NAME,
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            temperature=0.3,
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "https://quantstrike.app"),
                "X-Title": os.getenv("OPENROUTER_SITE_NAME", "Quantstrike Tutor"),
            },
        )
    return _llm

def get_embeddings():
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    return _embeddings

def get_vectorstore():
    global _vectorstore
    if _vectorstore is None:
        embeddings = get_embeddings()
        if os.path.exists(DB_PATH) and os.path.isdir(DB_PATH):
            try:
                _vectorstore = FAISS.load_local(DB_PATH, embeddings, allow_dangerous_deserialization=True)
            except Exception as e:
                print(f"Failed to load vectorstore: {e}")
                _vectorstore = None
        
        if _vectorstore is None:
            # Initialize empty vectorstore
            # FAISS requires at least one text to initialize, so we'll handle this dynamically
            pass
    return _vectorstore

async def add_document(file_path: str):
    """
    Ingest a PDF document into the vector store.
    """
    global _vectorstore
    try:
        # Load PDF
        loader = PyPDFLoader(file_path)
        docs = loader.load()
        
        if not docs:
            return False, "No text found in PDF."

        # Split text
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=350,
            add_start_index=True
        )
        splits = text_splitter.split_documents(docs)
        
        # Add metadata
        filename = os.path.basename(file_path)
        for doc in splits:
            doc.metadata["source"] = filename
            
        embeddings = get_embeddings()
        
        if _vectorstore is None:
            _vectorstore = FAISS.from_documents(splits, embeddings)
        else:
            _vectorstore.add_documents(splits)
            
        # Save local
        _vectorstore.save_local(DB_PATH)
        
        return True, f"Processed {len(splits)} chunks."
    except Exception as e:
        return False, str(e)

async def delete_document(filename: str):
    """
    Remove a document from the vector store by rebuilding the index excluding the file.
    FAISS doesn't support easy deletion like Chroma, so we rebuild.
    For a startup MVP with few files, this is acceptable.
    """
    global _vectorstore
    try:
        if _vectorstore is None:
            return False, "Vector store is empty."

    
        
        # Reset vectorstore
        _vectorstore = None
        
        # Rebuild
        uploads_dir = Path("uploads")
        if not uploads_dir.exists():
            return True, "Index cleared."
            
        files = [f for f in uploads_dir.iterdir() if f.suffix == '.pdf']
        
        if not files:
            # Delete vectorstore file if no PDFs left
            if os.path.exists(DB_PATH):
                shutil.rmtree(DB_PATH)
            return True, "Index cleared."

        # Ingest all remaining
        total_chunks = 0
        all_splits = []
        embeddings = get_embeddings()
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=350)

        for file_path in files:
            loader = PyPDFLoader(str(file_path))
            docs = loader.load()
            splits = text_splitter.split_documents(docs)
            for doc in splits:
                doc.metadata["source"] = file_path.name
            all_splits.extend(splits)
            total_chunks += len(splits)

        if all_splits:
            _vectorstore = FAISS.from_documents(all_splits, embeddings)
            _vectorstore.save_local(DB_PATH)
        
        return True, f"Index rebuilt with {total_chunks} chunks."

    except Exception as e:
        return False, str(e)

def query_rag(query: str, k: int = 5):
    """
    Query the vector store.
    """
    vectorstore = get_vectorstore()
    if vectorstore is None:
        return []
    results = vectorstore.similarity_search(query, k=k)
    return results

def ask_question(query: str):
    """
    Full RAG pipeline: Query -> Retrieve -> Generate.
    """
    vectorstore = get_vectorstore()
    if vectorstore is None:
        return {"answer": "Vector store not initialized. Please upload documents first.", "sources": []}
    
    llm = get_llm()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
    
    # Custom Prompt
    template = """You are a precise and analytical assistant. Your task is to answer questions based STRICTLY on the provided context.

CRITICAL INSTRUCTIONS:
1. **Numerical Accuracy**: When the question contains numbers that differ from examples in the context, you MUST:
   - Extract the exact numerical values from the QUESTION (not the context examples)
   - Identify the underlying formula, method, or concept from the context
   - Apply that formula/method using the numbers from the QUESTION
   - Show your calculations step-by-step with explicit arithmetic

2. **Conceptual Understanding**: 
   - Recognize when questions ask about the same concept with different parameters
   - Adapt formulas and methods to the specific numbers in the question
   - Do NOT simply copy answers from context if the numbers differ

3. **Calculation Protocol**:
   - State what values you're using and where they come from (the question)
   - Show each calculation step explicitly (e.g., "5 × 3 = 15")
   - Verify your final answer makes logical sense
   - Include units where applicable

4. **Answer Quality**:
   - Be professional, clear, and thorough
   - If you don't know or the context doesn't contain relevant information, explicitly state: "I don't have enough information in the provided context to answer this question."
   - Never fabricate information not present in the context
   - For multi-step problems, number your steps clearly

5. **Context Fidelity**:
   - If the context shows an example with different numbers, extract the METHOD and apply it to the question's numbers
   - Cite relevant parts of the context when applicable

You are a helpful tutor. Answer the student's question directly and clearly 
using the retrieved context. Do not mention "the context" or "the provided 
information" in your response. Do not complain about missing information. 
Write as if you're explaining directly to the student in a natural, 
conversational way

Context: {context}

Question: {question}

Helpful Answer (show all work for calculations):"""
    
    QA_CHAIN_PROMPT = PromptTemplate.from_template(template)
    
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": QA_CHAIN_PROMPT}
    )
    
    result = qa_chain.invoke({"query": query})
    
    # Format sources
    sources = []
    seen_sources = set()
    for doc in result["source_documents"]:
        source_name = doc.metadata.get("source", "Unknown")
        if source_name not in seen_sources:
            sources.append(source_name)
            seen_sources.add(source_name)
            
    return {
        "answer": result["result"],
        "sources": sources
    }


def lookup_exam_question(reference: str):
    """
    Look up a specific exam question by reference.
    Examples: "June 2004 Physics Paper 2 Q3c", "momentum question 1", "mechanics problem"
    """
    vectorstore = get_vectorstore()
    if vectorstore is None:
        return {"error": "Vector store not initialized. Please upload documents first."}
    
    llm = get_llm()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
    
    template = """You are an exam question lookup assistant. Your task is to find and present a specific exam question from the provided context.

The user is looking for: {question}

INSTRUCTIONS:
1. Search the context for the question that best matches the reference
2. If you find a matching question, present it clearly with:
   - The full question text
   - The mark allocation if available
   - The model answer or marking scheme if available
3. If the exact question isn't found, present the closest matching question from the context
4. Format your response clearly with sections for Question, Marks, and Model Answer

Context: {context}

Response:"""
    
    QA_CHAIN_PROMPT = PromptTemplate.from_template(template)
    
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": QA_CHAIN_PROMPT}
    )
    
    result = qa_chain.invoke({"query": reference})
    
    sources = []
    seen_sources = set()
    for doc in result["source_documents"]:
        source_name = doc.metadata.get("source", "Unknown")
        if source_name not in seen_sources:
            sources.append(source_name)
            seen_sources.add(source_name)
    
    return {
        "reference": reference,
        "result": result["result"],
        "sources": sources
    }


def generate_practice_questions(topic: str, difficulty: str = "medium", count: int = 5):
    """
    Generate practice questions on a topic using RAG context.
    """
    vectorstore = get_vectorstore()
    if vectorstore is None:
        return {"error": "Vector store not initialized. Please upload documents first."}
    
    llm = get_llm()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 10})
    
    template = """You are an expert exam question writer. Generate practice questions based on the provided educational content.

Topic requested: {question}
Difficulty level: """ + difficulty + """
Number of questions to generate: """ + str(count) + """

INSTRUCTIONS:
1. Study the context carefully to understand the topic
2. Generate exactly """ + str(count) + """ practice questions at """ + difficulty + """ difficulty
3. For each question, provide:
   - The question number
   - The question text (clear and exam-style)
   - Mark allocation (1-5 marks based on complexity)
   - A brief expected answer

FORMAT your response as:
---
Q1. [Question text] [X marks]
Expected Answer: [Brief answer]

Q2. [Question text] [X marks]
Expected Answer: [Brief answer]
---

Make questions progressively assess understanding from basic recall to application.

Context: {context}

Practice Questions:"""
    
    QA_CHAIN_PROMPT = PromptTemplate.from_template(template)
    
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": QA_CHAIN_PROMPT}
    )
    
    result = qa_chain.invoke({"query": topic})
    
    return {
        "topic": topic,
        "difficulty": difficulty,
        "count": count,
        "questions": result["result"]
    }


def mark_student_answer(question: str, student_answer: str, max_marks: int = 5):
    """
    Compare student answer against expected answer and provide feedback.
    Returns: marks awarded, feedback, missing points
    """
    vectorstore = get_vectorstore()
    if vectorstore is None:
        return {
            "question": question,
            "student_answer": student_answer,
            "max_marks": max_marks,
            "marking_result": "No documents have been uploaded yet. Please upload some PDFs first, then try marking your answer."
        }

    
    llm = get_llm()
    retriever = vectorstore.as_retriever(search_kwargs={"k": 8})
    
    template = """You are a STRICT and PRECISE exam marker. Your task is to carefully mark a student's answer, paying special attention to numerical accuracy.

QUESTION: {question}

STUDENT'S ANSWER: """ + student_answer + """

MAXIMUM MARKS: """ + str(max_marks) + """

CRITICAL MARKING INSTRUCTIONS:

1. **VERIFY ALL CALCULATIONS**: 
   - Check EVERY arithmetic operation in the student's answer
   - If the student writes "5 × 20 = 500", this is WRONG (correct: 5 × 20 = 100)
   - Perform each calculation yourself to verify correctness
   - Arithmetic errors should result in mark deductions

2. **CHECK FINAL ANSWERS**:
   - Compare the student's final numerical answer to your calculated correct answer
   - If the numbers don't match, the answer is INCORRECT regardless of method
   - State clearly: "Student's answer: [X], Correct answer: [Y]"

3. **MARKING CRITERIA** (deduct marks for):
   - Wrong final answer (major deduction)
   - Arithmetic/calculation errors
   - Missing steps or explanations
   - Incorrect formulas or methods
   - Missing units

4. **BE STRICT BUT FAIR**:
   - Give credit for correct method even if arithmetic is wrong (partial marks)
   - But never give full marks if the final answer is wrong
   - Show your own calculation as the model answer

PROVIDE YOUR RESPONSE IN THIS EXACT FORMAT:

MARKS AWARDED: [X]/""" + str(max_marks) + """

CALCULATION CHECK:
[Show your step-by-step verification of the student's calculations]
[Clearly state if any calculations are CORRECT or INCORRECT]

FEEDBACK:
[What was done well and what needs improvement]

ERRORS FOUND:
- [List any errors, including arithmetic mistakes]
(If no errors, state "No errors found")

MODEL ANSWER:
[The complete correct solution with your own calculations]

Context: {context}

Marking:"""
    
    QA_CHAIN_PROMPT = PromptTemplate.from_template(template)
    
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=retriever,
        return_source_documents=True,
        chain_type_kwargs={"prompt": QA_CHAIN_PROMPT}
    )
    
    result = qa_chain.invoke({"query": question})
    
    return {
        "question": question,
        "student_answer": student_answer,
        "max_marks": max_marks,
        "marking_result": result["result"]
    }

from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil
import os
from pathlib import Path

from app.rag import add_document as rag_add, delete_document as rag_delete

Path("uploads").mkdir(exist_ok=True)

app = FastAPI(title="Quant RAG", description=" RAG System for PDF Querying")

# Mount static files (CSS, JS, etc.)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    
    try:
        file_location = f"uploads/{file.filename}"
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Trigger RAG Ingestion
        success, msg = await rag_add(file_location)
        if not success:
             print(f"RAG Ingestion failed: {msg}") # Log but don't fail upload for now?
             # actually, maybe we should warn?
        
        return JSONResponse({
            "filename": file.filename, 
            "status": "success",
            "message": "File uploaded and processed successfully",
            "rag_status": msg
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/documents")
async def list_documents():
    """List all uploaded PDF documents."""
    try:
        files = []
        if os.path.exists("uploads"):
            for filename in os.listdir("uploads"):
                if filename.endswith(".pdf"):
                    files.append(filename)
        return JSONResponse({"files": files})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/documents/{filename}")
async def delete_document(filename: str):
    """Delete a document."""
    try:
        file_path = os.path.join("uploads", filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            
            # Update RAG Index
            await rag_delete(filename)
            
            return JSONResponse({"status": "success", "message": f"{filename} deleted"})
        else:
            raise HTTPException(status_code=404, detail="File not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/query")
async def query_documents(request: Request):
    """Query the vector store and return top K chunks."""
    try:
        data = await request.json()
        query = data.get("query")
        if not query:
             raise HTTPException(status_code=400, detail="Query is required")
        
       
        from app.rag import ask_question
        
        # Use full RAG
        result = ask_question(query)
        
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

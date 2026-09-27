"""
FastAPI backend for the Medical Document Chatbot.

Endpoints:
  POST /api/upload      -> upload one or more PDF/DOCX/image files
  POST /api/chat        -> ask a question about uploaded documents
  GET  /api/documents    -> list currently indexed documents
  POST /api/reset       -> clear chat memory (keeps documents)
  POST /api/clear-documents -> wipe all uploaded documents + vector DB

Run with:  uvicorn app.main:app --reload --port 8000
"""
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import settings
from app.document_loader import load_document
from app.chunking import chunk_document
from app.vectorstore import replace_chunks, list_sources, clear_all
from app.chat_engine import answer_question

settings.validate()

app = FastAPI(title="Medical Document Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single-user app -> one in-memory chat history is enough.
CHAT_HISTORY: list[dict] = []

ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc", ".png", ".jpg", ".jpeg"}


class ChatRequest(BaseModel):
    message: str


def _saved_uploads(filename: str) -> list[Path]:
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid document filename.")
    suffix = f"_{filename}"
    return [
        path for path in settings.UPLOAD_DIR.iterdir()
        if path.is_file() and path.name.endswith(suffix)
    ]


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload_documents(files: list[UploadFile] = File(...)):
    results = []

    for file in files:
        suffix = Path(file.filename).suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            results.append({
                "filename": file.filename,
                "status": "skipped",
                "reason": f"Unsupported file type '{suffix}'",
            })
            continue

        # Save with a unique name on disk to avoid collisions, but keep
        # the original name as metadata for a friendly display.
        safe_name = f"{uuid.uuid4().hex}_{file.filename}"
        dest_path = settings.UPLOAD_DIR / safe_name
        previous_files = _saved_uploads(file.filename)

        try:
            with dest_path.open("wb") as f:
                shutil.copyfileobj(file.file, f)

            text = load_document(dest_path)
            doc_type = "image" if suffix in (".png", ".jpg", ".jpeg") else suffix.strip(".")
            chunks = chunk_document(text, filename=file.filename, doc_type=doc_type)
            if not chunks:
                dest_path.unlink(missing_ok=True)
                results.append({
                    "filename": file.filename,
                    "status": "empty",
                    "chunks_added": 0,
                    "characters_extracted": len(text),
                    "reason": "No text could be extracted; the existing index was left unchanged.",
                })
                continue

            added = replace_chunks(chunks, source=file.filename)
            for previous_file in previous_files:
                previous_file.unlink(missing_ok=True)

            results.append({
                "filename": file.filename,
                "status": "indexed" if added else "empty",
                "chunks_added": added,
                "characters_extracted": len(text),
            })
        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "error",
                "reason": str(e),
            })

    return {"results": results}


@app.get("/api/documents")
def get_documents():
    documents = list_sources()
    for document in documents:
        document["can_reindex"] = bool(_saved_uploads(document["filename"]))
    return {"documents": documents}


@app.post("/api/documents/{filename}/reindex")
def reindex_document(filename: str):
    saved_files = _saved_uploads(filename)
    if not saved_files:
        raise HTTPException(status_code=404, detail="Saved upload not found for this document.")

    source_path = max(saved_files, key=lambda path: path.stat().st_mtime_ns)
    try:
        text = load_document(source_path)
        suffix = source_path.suffix.lower()
        doc_type = "image" if suffix in (".png", ".jpg", ".jpeg") else suffix.strip(".")
        chunks = chunk_document(text, filename=filename, doc_type=doc_type)
        if not chunks:
            raise HTTPException(status_code=422, detail="No text could be extracted; the existing index was left unchanged.")
        added = replace_chunks(chunks, source=filename)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Reindex failed: {e}") from e

    return {
        "filename": filename,
        "status": "indexed",
        "chunks_added": added,
        "characters_extracted": len(text),
    }


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    result = answer_question(req.message.strip(), history=CHAT_HISTORY)

    CHAT_HISTORY.append({"question": req.message.strip(), "answer": result["answer"]})
    # Keep history from growing unbounded
    if len(CHAT_HISTORY) > 20:
        del CHAT_HISTORY[: len(CHAT_HISTORY) - 20]

    return result


@app.post("/api/reset")
def reset_chat():
    CHAT_HISTORY.clear()
    return {"status": "chat history cleared"}


@app.post("/api/clear-documents")
def clear_documents():
    clear_all()
    CHAT_HISTORY.clear()
    for f in settings.UPLOAD_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    return {"status": "all documents and chat history cleared"}

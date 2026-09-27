"""
Splits extracted document text into overlapping chunks using LangChain's
RecursiveCharacterTextSplitter, and attaches metadata (source filename,
document type, upload timestamp) so answers can later be traced back to
the exact document/chunk they came from.
"""
from datetime import datetime
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document as LCDocument

from app.config import settings


def build_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )


def chunk_document(
    text: str,
    filename: str,
    doc_type: str = "unknown",
) -> list[LCDocument]:
    """
    Turns raw extracted text into a list of LangChain Documents (chunks)
    ready to be embedded and stored in the vector DB.
    """
    if not text or not text.strip():
        return []

    splitter = build_splitter()
    raw_chunks = splitter.split_text(text)

    upload_time = datetime.utcnow().isoformat()
    documents = [
        LCDocument(
            page_content=chunk,
            metadata={
                "source": filename,
                "doc_type": doc_type,
                "uploaded_at": upload_time,
                "chunk_index": idx,
            },
        )
        for idx, chunk in enumerate(raw_chunks)
    ]
    return documents

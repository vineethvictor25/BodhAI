"""
ChromaDB-backed vector store. Persists to a local folder on disk
(backend/data/chroma_db) so uploaded documents survive server restarts.
"""
from functools import lru_cache
from threading import RLock
from chromadb.errors import InvalidCollectionException
from langchain_chroma import Chroma
from langchain.schema import Document as LCDocument

from app.config import settings
from app.embeddings import get_embeddings

COLLECTION_NAME = "medical_documents"
_vectorstore_lock = RLock()


@lru_cache(maxsize=1)
def _create_vectorstore() -> Chroma:
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=get_embeddings(),
        persist_directory=str(settings.CHROMA_DIR),
    )


def get_vectorstore() -> Chroma:
    with _vectorstore_lock:
        return _create_vectorstore()


def replace_chunks(chunks: list[LCDocument], source: str) -> int:
    if not chunks:
        return 0
    with _vectorstore_lock:
        store = get_vectorstore()
        try:
            existing = store.get(where={"source": source}, include=[])
            if existing["ids"]:
                store.delete(ids=existing["ids"])
            store.add_documents(chunks)
        except InvalidCollectionException:
            _create_vectorstore.cache_clear()
            get_vectorstore().add_documents(chunks)
    return len(chunks)


def get_retriever(k: int | None = None):
    store = get_vectorstore()
    return store.as_retriever(search_kwargs={"k": k or settings.RETRIEVAL_K})


def list_sources() -> list[dict]:
    """Return the distinct set of uploaded documents currently indexed."""
    with _vectorstore_lock:
        store = get_vectorstore()
        raw = store.get(include=["metadatas"])
        seen = {}
        chunk_counts = {}
        for meta in raw.get("metadatas", []):
            if not meta:
                continue
            source = meta.get("source")
            if source:
                chunk_counts[source] = chunk_counts.get(source, 0) + 1
            if source and source not in seen:
                seen[source] = {
                    "filename": source,
                    "doc_type": meta.get("doc_type", "unknown"),
                    "uploaded_at": meta.get("uploaded_at"),
                }
        for source, document in seen.items():
            document["chunk_count"] = chunk_counts[source]
        return list(seen.values())


def clear_all():
    with _vectorstore_lock:
        store = get_vectorstore()
        store.delete_collection()
        _create_vectorstore.cache_clear()

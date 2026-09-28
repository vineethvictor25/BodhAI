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


def replace_chunks(chunks: list[LCDocument], source: str, owner_id: str) -> int:
    if not chunks:
        return 0
    with _vectorstore_lock:
        store = get_vectorstore()
        try:
            existing = store.get(
                where={"$and": [{"source": source}, {"owner_id": owner_id}]},
                include=[],
            )
            if existing["ids"]:
                store.delete(ids=existing["ids"])
            store.add_documents(chunks)
        except InvalidCollectionException:
            _create_vectorstore.cache_clear()
            get_vectorstore().add_documents(chunks)
    return len(chunks)


def get_retriever(k: int | None = None, owner_id: str | None = None):
    store = get_vectorstore()
    search_kwargs = {"k": k or settings.RETRIEVAL_K}
    if owner_id is not None:
        search_kwargs["filter"] = {"owner_id": owner_id}
    return store.as_retriever(search_kwargs=search_kwargs)


def list_sources(owner_id: str | None = None) -> list[dict]:
    """Return the distinct set of uploaded documents currently indexed."""
    with _vectorstore_lock:
        store = get_vectorstore()
        raw = store.get(
            where={"owner_id": owner_id} if owner_id is not None else None,
            include=["metadatas"],
        )
        seen = {}
        chunk_counts = {}
        for meta in raw.get("metadatas", []):
            if not meta:
                continue
            source = meta.get("source")
            owner_id = meta.get("owner_id", "legacy")
            key = (owner_id, source)
            if source:
                chunk_counts[key] = chunk_counts.get(key, 0) + 1
            if source and key not in seen:
                seen[key] = {
                    "filename": source,
                    "doc_type": meta.get("doc_type", "unknown"),
                    "uploaded_at": meta.get("uploaded_at"),
                    "owner_id": owner_id,
                }
        for key, document in seen.items():
            document["chunk_count"] = chunk_counts[key]
        return list(seen.values())


def clear_all():
    with _vectorstore_lock:
        store = get_vectorstore()
        store.delete_collection()
        _create_vectorstore.cache_clear()


def clear_owner(owner_id: str):
    with _vectorstore_lock:
        store = get_vectorstore()
        store.delete(where={"owner_id": owner_id})

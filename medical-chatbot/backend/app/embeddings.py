"""
Local HuggingFace embedding model (sentence-transformers).

Runs entirely on your laptop's CPU, no API key or network calls needed
once the model weights are cached (~80MB, downloaded automatically the
first time you run the app).
"""
from functools import lru_cache
from threading import Lock
from langchain_huggingface import HuggingFaceEmbeddings

from app.config import settings


_embedding_lock = Lock()


@lru_cache(maxsize=1)
def _load_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=settings.EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def get_embeddings() -> HuggingFaceEmbeddings:
    with _embedding_lock:
        return _load_embeddings()

"""
Central configuration for the Medical Document Chatbot backend.
All values are loaded from environment variables / .env so nothing
sensitive (like the Google AI Studio API key) is hardcoded.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# backend/ is the project root for this app
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    # --- Google AI Studio (Gemini) ---
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # --- Embeddings (local HuggingFace model, no API key needed) ---
    EMBEDDING_MODEL: str = os.getenv(
        "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )

    # --- Chunking (RecursiveCharacterTextSplitter) ---
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", 1000))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", 150))

    # --- Retrieval ---
    RETRIEVAL_K: int = int(os.getenv("RETRIEVAL_K", 5))

    # --- Storage ---
    UPLOAD_DIR: Path = BASE_DIR / os.getenv("UPLOAD_DIR", "data/uploads")
    CHROMA_DIR: Path = BASE_DIR / os.getenv("CHROMA_DIR", "data/chroma_db")

    def validate(self):
        if not self.GOOGLE_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and paste your Google AI Studio API key."
            )
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.CHROMA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()

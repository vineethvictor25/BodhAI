"""
Central configuration for the Medical Document Chatbot backend.
All values are loaded from environment variables / .env so nothing
sensitive (like the Google AI Studio API key) is hardcoded.
"""
import os
import secrets
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

    # --- Authentication ---
    AUTH_SECRET: str = os.getenv("AUTH_SECRET", "")
    AUTH_COOKIE_SECURE: bool = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"

    # --- Medication reminder delivery ---
    REMINDER_DELIVERY_MODE: str = os.getenv("REMINDER_DELIVERY_MODE", "email").lower()
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", 587))
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_EMAIL: str = os.getenv("SMTP_FROM_EMAIL", "")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_USE_SSL: bool = os.getenv("SMTP_USE_SSL", "false").lower() == "true"
    APP_PUBLIC_URL: str = os.getenv("APP_PUBLIC_URL", "http://localhost:5173")

    # --- Optional WhatsApp Cloud API reminders ---
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_TEMPLATE_NAME: str = os.getenv("WHATSAPP_TEMPLATE_NAME", "medication_reminder")
    WHATSAPP_TEMPLATE_LANGUAGE: str = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US")
    WHATSAPP_GRAPH_API_VERSION: str = os.getenv("WHATSAPP_GRAPH_API_VERSION", "v25.0")

    def validate(self):
        if not self.GOOGLE_API_KEY:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Copy backend/.env.example to "
                "backend/.env and paste your Google AI Studio API key."
            )
        secret_path = BASE_DIR / "data" / ".auth_secret"
        if not self.AUTH_SECRET:
            try:
                with secret_path.open("x", encoding="utf-8") as secret_file:
                    secret_file.write(secrets.token_urlsafe(48))
            except FileExistsError:
                pass
            self.AUTH_SECRET = secret_path.read_text(encoding="utf-8").strip()
        if len(self.AUTH_SECRET) < 32:
            raise RuntimeError("AUTH_SECRET must be a random value of at least 32 characters.")
        if self.REMINDER_DELIVERY_MODE not in {"email", "manual_whatsapp", "whatsapp_cloud"}:
            raise RuntimeError(
                "REMINDER_DELIVERY_MODE must be 'email', 'manual_whatsapp', or 'whatsapp_cloud'."
            )
        self.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.CHROMA_DIR.mkdir(parents=True, exist_ok=True)


settings = Settings()

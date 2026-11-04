"""Public model gateway; implementation lives in llm_router."""
# These module objects remain exposed for the existing mock-based regression tests.
import httpx
import time
from .llm_router import provider, structured, public_config

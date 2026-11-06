"""Public model gateway; implementation lives in llm_router."""
# These module objects remain exposed for the existing mock-based regression tests.
from functools import partial

import httpx
import time
from .llm_router import provider, structured, public_config

structured_mapping = partial(structured, repair_invalid_output=True)

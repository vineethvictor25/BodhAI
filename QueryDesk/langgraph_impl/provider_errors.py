"""Safe, structured provider failures; never retain response bodies or credentials."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math


class ProviderError(RuntimeError):
    def __init__(self, message, kind, retry_after=None):
        super().__init__(message)
        self.kind, self.retry_after = kind, retry_after

    def details(self):
        return {"kind":self.kind,"retry_after":self.retry_after}


def retry_delay(headers):
    def number(raw):
        value=float(raw)
        return max(0,value) if math.isfinite(value) else None
    raw = headers.get("retry-after")
    if raw is None:
        milliseconds = headers.get("retry-after-ms")
        try:
            value=number(milliseconds) if milliseconds else None
            return value/1000 if value is not None else None
        except ValueError:
            return None
    try:
        return number(raw)
    except ValueError:
        try:
            return max(0,(parsedate_to_datetime(raw)-datetime.now(timezone.utc)).total_seconds())
        except (ValueError,TypeError):
            return None

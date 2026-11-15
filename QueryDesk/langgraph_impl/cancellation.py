"""Request-local cancellation; never retain shared source connections or credentials."""
from contextlib import contextmanager
from contextvars import ContextVar
import threading


class QueryCancelled(RuntimeError):
    pass


class Cancellation:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.RLock()
        self.callbacks = {}

    def check(self):
        if self.event.is_set():
            raise QueryCancelled('Query cancelled. Any late results were discarded.')

    def signal(self):
        # Serialize removal with callbacks so a released connection is never targeted.
        with self.lock:
            for callback in list(self.callbacks.values()):
                try: callback()
                except Exception: pass  # cancellation is best effort; do not expose driver details


current = ContextVar('query_cancellation', default=None)


def check():
    token = current.get()
    if token: token.check()


def cancelled():
    token=current.get()
    return bool(token and token.event.is_set())


def driver_cancel(connection):
    if not current.get(): return None
    raw = connection.connection.driver_connection
    callback = getattr(raw, 'cancel', None)
    return callback if callable(callback) else None


@contextmanager
def activate(control):
    token = current.set(control)
    try: yield
    finally: current.reset(token)


@contextmanager
def interruptible(callback=None):
    control = current.get()
    if not control:
        yield
        return
    key = object()
    with control.lock:
        control.check()
        if callback: control.callbacks[key] = callback
    try:
        yield
        control.check()
    finally:
        with control.lock: control.callbacks.pop(key, None)

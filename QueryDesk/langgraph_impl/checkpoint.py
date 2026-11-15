"""Atomic evaluation snapshots and an OS lock released even after process interruption."""
import json
from .value_codec import encode
import os
from contextlib import contextmanager
from pathlib import Path


def save_report(filename, report):
    path=Path(filename)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    with temporary.open("w",encoding="utf-8") as output:
        json.dump(encode(report),output,indent=2,allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary,path)


@contextmanager
def report_lock(filename):
    path=Path(str(filename)+".lock")
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a+b") as lock:
        lock.seek(0)
        if os.fstat(lock.fileno()).st_size==0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        try:
            if os.name=="nt":
                import msvcrt
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            raise ValueError("This report is already in use by another evaluation") from None
        try:
            yield
        finally:
            lock.seek(0)
            if os.name=="nt":
                msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(lock.fileno(),fcntl.LOCK_UN)

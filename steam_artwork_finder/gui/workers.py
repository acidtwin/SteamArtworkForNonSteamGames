"""Run blocking work off the UI thread."""

from __future__ import annotations

import hashlib
import os
import tempfile
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from ..config import cache_dir


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # report anything to the UI
            traceback.print_exc()
            self.signals.failed.emit(str(e))
            return
        self.signals.done.emit(result)


_live: set[Task] = set()


def run(fn, *args, on_done=None, on_error=None, on_progress=None, **kwargs) -> Task:
    """Start fn in the pool. If fn takes `progress`, it gets an emit function."""
    task = Task(fn, *args, **kwargs)
    if on_progress is not None:
        task.kwargs["progress"] = task.signals.progress.emit
        task.signals.progress.connect(on_progress)
    if on_done:
        task.signals.done.connect(on_done)
    if on_error:
        task.signals.failed.connect(on_error)
    # Keep the Python object alive until it finishes.
    _live.add(task)
    task.signals.done.connect(lambda *_: _live.discard(task))
    task.signals.failed.connect(lambda *_: _live.discard(task))
    task.setAutoDelete(False)
    QThreadPool.globalInstance().start(task)
    return task


def _is_video(data: bytes) -> bool:
    return data[:4] == b"\x1a\x45\xdf\xa3" or data[4:8] == b"ftyp"  # WebM/Matroska, MP4


def _whole(data: bytes) -> bool:
    return _is_video(data) or not QImage.fromData(data).isNull()


def cached_file(url: str, fetch) -> str:
    """Fetch url via fetch(url) into the cache folder (once) and return the file's path."""
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / hashlib.sha1(url.encode()).hexdigest()
    try:
        if _whole(p.read_bytes()):
            return str(p)
        p.unlink(missing_ok=True)  # cut off or broken: fetch it again
    except OSError:
        pass
    data = fetch(url)
    # Written aside then renamed, so another thread never reads half a file.
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".part", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return str(p)


def cached_download(url: str, fetch) -> bytes:
    """Fetch url via fetch(url), keeping a copy in the cache folder."""
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / hashlib.sha1(url.encode()).hexdigest()
    try:
        data = p.read_bytes()
        if not QImage.fromData(data).isNull():
            return data
        p.unlink(missing_ok=True)  # cut off or broken: fetch it again
    except OSError:
        pass
    data = fetch(url)
    # Written aside then renamed, so another thread never reads half a file.
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".part", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return data

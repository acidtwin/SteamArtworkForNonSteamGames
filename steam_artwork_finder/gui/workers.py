"""Run blocking work off the UI thread."""

from __future__ import annotations

import hashlib
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

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


def cached_download(url: str, fetch) -> bytes:
    """Fetch url via fetch(url), keeping a copy in the cache folder."""
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / hashlib.sha1(url.encode()).hexdigest()
    if p.exists():
        return p.read_bytes()
    data = fetch(url)
    p.write_bytes(data)
    return data

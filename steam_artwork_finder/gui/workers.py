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
            self.signals.failed.emit(str(e) or type(e).__name__)
            return
        self.signals.done.emit(result)


_live: set[Task] = set()
_pools: dict[str, QThreadPool] = {}


def pool_for(kind: str) -> QThreadPool:
    """Separate pools so slow work never queues ahead of quick work:
    "net"  - downloads and API calls (mostly waiting on the network), the default;
    "cpu"  - image decoding, sized to the machine's cores;
    "long" - Get Artwork, Apply and other jobs that hold a thread for a long time."""
    if kind == "cpu":
        return QThreadPool.globalInstance()
    p = _pools.get(kind)
    if p is None:
        p = _pools[kind] = QThreadPool()
        p.setMaxThreadCount(12 if kind == "net" else 2)
        p.setExpiryTimeout(30_000)
    return p


def run(fn, *args, on_done=None, on_error=None, on_progress=None, pool=None, kind: str = "net",
        priority: int = 0, **kwargs) -> Task:
    """Start fn in the `kind` pool (or `pool`). If fn takes `progress`, it gets an emit function."""
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
    (pool or pool_for(kind)).start(task, priority)
    return task


def shutdown(ms: int = 1500):
    """When quitting: drop queued work and give running work a moment to finish."""
    pools = [QThreadPool.globalInstance(), *_pools.values()]
    for p in pools:
        p.clear()
    for p in pools:
        p.waitForDone(ms)


def _is_video(data: bytes) -> bool:
    return data[:4] == b"\x1a\x45\xdf\xa3" or data[4:8] == b"ftyp"  # WebM/Matroska, MP4


def _whole(data: bytes) -> bool:
    """A cached file that is complete. Judged by its ending where the format allows, so a
    cache hit doesn't cost a full decode."""
    if not data:
        return False
    if _is_video(data):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data[-12:-4] == b"\x00\x00\x00\x00IEND"
    if data[:3] == b"\xff\xd8\xff":
        return b"\xff\xd9" in data[-64:]
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return int.from_bytes(data[4:8], "little") + 8 <= len(data)
    if data[:4] in (b"GIF8",):
        return data.rstrip(b"\x00")[-1:] == b"\x3b"
    return not QImage.fromData(data).isNull()


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
        if _whole(data):
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


def prune_cache(max_bytes: int = 400 * 1024 * 1024):
    """Keep the thumbnail and .exe icon caches under max_bytes, dropping the least recently used."""
    files = []
    for sub in ("thumbs", "exe-icons"):
        try:
            with os.scandir(cache_dir() / sub) as it:
                for e in it:
                    try:
                        st = e.stat()
                    except OSError:
                        continue
                    if e.is_file():
                        files.append((max(st.st_atime, st.st_mtime), st.st_size, e.path))
        except OSError:
            continue
    total = sum(f[1] for f in files)
    for _t, size, path in sorted(files):
        if total <= max_bytes:
            break
        try:
            os.unlink(path)
            total -= size
        except OSError:
            pass

"""Run blocking work off the UI thread."""

from __future__ import annotations

import hashlib
import os
import tempfile
import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from ..config import cache_dir
from ..sgdb import GoneError, SGDBError


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)


class Task(QRunnable):
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.error: Exception | None = None

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # report anything to the UI
            if isinstance(e, RuntimeError) and "Signal source has been deleted" in str(e):
                return  # the app is closing: nobody is left to tell
            if not isinstance(e, (SGDBError, CutOff, OSError)):
                traceback.print_exc()  # a bug, not the network: worth seeing
            self.error = e
            self._emit(self.signals.failed, str(e) or type(e).__name__)
            return
        self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value):
        try:
            signal.emit(value)
        except RuntimeError:  # the signals went with the closing app
            pass


_live: set[Task] = set()
_pools: dict[str, QThreadPool] = {}


def pool_for(kind: str) -> QThreadPool:
    """Separate pools so slow work never queues ahead of quick work:
    "net"  - downloads and API calls (mostly waiting on the network), the default;
    "cpu"  - image decoding, sized to the machine's cores;
    "long" - Get Artwork, Apply, matching and other jobs that hold a thread for a long time."""
    if kind == "cpu":
        return QThreadPool.globalInstance()
    p = _pools.get(kind)
    if p is None:
        p = _pools[kind] = QThreadPool()
        p.setMaxThreadCount(12 if kind == "net" else 4)
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
        return _PNG_END in data[-4096:]  # some editors add bytes after the end chunk
    if data[:3] == b"\xff\xd8\xff":
        # The end marker after the main image's scan (an EXIF thumbnail before it has its own).
        # Scan data never holds FF D9: every FF byte in it is followed by 00.
        return data.rfind(b"\xff\xd9") > _jpeg_scan(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return int.from_bytes(data[4:8], "little") + 8 <= len(data)
    if data[:4] in (b"GIF8",):
        return data.rstrip(b"\x00")[-1:] == b"\x3b"
    return not QImage.fromData(data).isNull()


_PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"  # the end chunk, with its fixed checksum


def _jpeg_scan(data: bytes) -> int:
    """Where a JPEG's main image data starts (its first start-of-scan), skipping the header
    segments by their lengths; the file's length if it never gets there."""
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:
            return i
        if marker == 0xFF:  # padding
            i += 1
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return len(data)


def _whole_file(p) -> bool:
    """_whole for a file in the cache, reading only its two ends when it's big (animated
    pieces run to tens of MB, and are asked for again and again)."""
    size = p.stat().st_size
    if size <= 256 * 1024:
        return _whole(p.read_bytes())
    with open(p, "rb") as f:
        head = f.read(64 * 1024)
        f.seek(-64 * 1024, os.SEEK_END)
        tail = f.read()
    if _is_video(head):
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return _PNG_END in tail
    if head[:3] == b"\xff\xd8\xff":
        return b"\xff\xd9" in tail  # far past the headers, so only the image's own end marker
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return int.from_bytes(head[4:8], "little") + 8 <= size
    if head[:4] == b"GIF8":
        return tail.rstrip(b"\x00")[-1:] == b"\x3b"
    return _whole(p.read_bytes())


RETRIES = 3  # tries after the first before a download is given up on (each shown as "Retrying (n of 3)")
RETRY_PAUSE = (1000, 2000, 4000)  # ms before each retry


class CutOff(Exception):
    """A download that arrived incomplete."""


def run_retrying(fn, *args, on_done=None, on_error=None, on_retry=None, on_gone=None, kind: str = "net",
                 **kwargs) -> None:
    """Like run(), but when fn fails it's tried again up to RETRIES times, after a growing pause,
    before on_error hears of it: a dropped or cut-off download usually works the second time.
    The pause is a timer on the GUI thread, so no worker sits idle through it. `on_retry(n)` is
    told as retry n is scheduled. A file that isn't there (GoneError) is given up on at once,
    and told to `on_gone` instead, if given."""
    from PySide6.QtCore import QTimer

    def attempt(n: int):
        def failed(msg):
            err = task.error
            if isinstance(err, GoneError) and on_gone is not None:
                on_gone(msg)
                return
            if n >= RETRIES or isinstance(err, GoneError):
                if on_error is not None:
                    on_error(msg)
                return
            if on_retry is not None:
                on_retry(n + 1)
            QTimer.singleShot(RETRY_PAUSE[min(n, len(RETRY_PAUSE) - 1)], lambda: attempt(n + 1))

        task = run(fn, *args, on_done=on_done, on_error=failed, kind=kind, **kwargs)

    attempt(0)


class Shared:
    """One job for everyone who asks for the same thing while it's under way (the strip and
    See all both wanting a thumbnail, say), rather than one each."""

    def __init__(self):
        self._waiting: dict = {}  # key -> [callbacks (done, error, retry, gone)], and the retry it's on

    def get(self, key, fn, *args, on_done=None, on_error=None, on_retry=None, on_gone=None, **kwargs) -> int:
        """Start fn (retrying) unless it's already running for `key`; returns the retry it's on (0: first try)."""
        entry = self._waiting.get(key)
        if entry is not None:
            entry[0].append((on_done, on_error, on_retry, on_gone))
            return entry[1]
        entry = self._waiting[key] = [[(on_done, on_error, on_retry, on_gone)], 0]

        def tell(which, value):
            for cbs in self._waiting.pop(key, [[]])[0]:
                cb = cbs[which] if which != 3 or cbs[3] is not None else cbs[1]  # gone, else failed
                if cb is not None:
                    cb(value)

        def retry(n):
            entry[1] = n
            for cbs in entry[0]:
                if cbs[2] is not None:
                    cbs[2](n)

        run_retrying(fn, *args, on_done=lambda r: tell(0, r), on_error=lambda m: tell(1, m), on_retry=retry,
                     on_gone=lambda m: tell(3, m), **kwargs)
        return 0


def _fetch(url: str, fetch) -> bytes:
    data = fetch(url)
    if not _whole(data):
        raise CutOff("The download was cut off")
    return data


def cached_file(url: str, fetch) -> str:
    """Fetch url via fetch(url) into the cache folder (once) and return the file's path."""
    d = cache_dir() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / hashlib.sha1(url.encode()).hexdigest()
    try:
        if _whole_file(p):
            return str(p)
        p.unlink(missing_ok=True)  # cut off or broken: fetch it again
    except OSError:
        pass
    data = _fetch(url, fetch)
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
    data = _fetch(url, fetch)
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

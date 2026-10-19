"""Read and safely write a Steam user's shortcuts.vdf."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from . import binvdf

MAX_BACKUPS = 10


def legacy_appid(exe: str, name: str) -> int:
    """The id Steam derives for a shortcut that has no stored appid."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def _get(entry: binvdf.VDFMap, *names: str, default=""):
    # Key case varies between Steam versions ("AppName" vs "appname").
    lower = {k.lower(): k for k in entry}
    for n in names:
        k = lower.get(n.lower())
        if k is not None:
            return entry.get_value(k, default), k
    return default, None


@dataclass
class Shortcut:
    index: str  # key inside the "shortcuts" map, e.g. "0"
    appid: int  # unsigned 32-bit id, used for artwork file names
    name: str
    exe: str
    icon: str
    entry: binvdf.VDFMap

    def set_icon(self, path: str):
        _, key = _get(self.entry, "icon")
        self.entry.set_string(key or "icon", path)
        self.icon = path


class ShortcutsChanged(Exception):
    """The file on disk changed since it was loaded (Steam rewrote it)."""


class ShortcutsFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.root = binvdf.VDFMap()
        self.shortcuts: list[Shortcut] = []
        self._backed_up = False
        self._stamp = None
        if self.path.exists():
            self.reload()

    def _disk_stamp(self):
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def reload(self):
        self.root = binvdf.loads(self.path.read_bytes())
        self.shortcuts = self._extract()
        self._backed_up = False
        self._stamp = self._disk_stamp()

    def changed_on_disk(self) -> bool:
        """True if Steam or another tool rewrote the file since it was loaded."""
        return self._disk_stamp() != self._stamp

    def find(self, appid: int) -> Shortcut | None:
        return next((s for s in self.shortcuts if s.appid == appid), None)

    def _extract(self) -> list[Shortcut]:
        container = None
        for k, v in self.root.items():
            if k.lower() == "shortcuts" and isinstance(v, dict):
                container = v
        if container is None:
            return []
        out = []
        for idx, entry in container.items():
            if not isinstance(entry, dict):
                continue
            name, _ = _get(entry, "AppName", "appname")
            exe, _ = _get(entry, "Exe", "exe")
            icon, _ = _get(entry, "icon")
            appid, _ = _get(entry, "appid", default=None)
            if appid is None or appid == 0:
                appid = legacy_appid(exe, name)
            out.append(Shortcut(idx, int(appid) & 0xFFFFFFFF, name, exe, icon, entry))
        return out

    @property
    def backup_dir(self) -> Path:
        return self.path.parent / "shortcuts-backups"

    def backup(self) -> Path | None:
        if not self.path.exists():
            return None
        self.backup_dir.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = self.backup_dir / f"shortcuts-{stamp}.vdf"
        n = 1
        while dest.exists():
            dest = self.backup_dir / f"shortcuts-{stamp}-{n}.vdf"
            n += 1
        shutil.copyfile(self.path, dest)
        for old in self._backup_files()[MAX_BACKUPS:]:
            old.unlink(missing_ok=True)
        return dest

    def _backup_files(self) -> list[Path]:
        files = list(self.backup_dir.glob("shortcuts-*.vdf"))
        return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)

    def backups(self) -> list[tuple[Path, int]]:
        """Saved copies, newest first, with how many games each one lists."""
        out = []
        for p in self._backup_files():
            try:
                count = len(_shortcut_maps(binvdf.loads(p.read_bytes())))
            except (OSError, binvdf.VDFError):
                count = -1
            out.append((p, count))
        return out

    def restore(self, backup: Path):
        """Put a backup back, keeping a copy of the current file first."""
        binvdf.loads(backup.read_bytes())  # refuse a broken backup
        self.backup()
        tmp = self.path.with_name(".shortcuts-restore")
        shutil.copyfile(backup, tmp)
        os.replace(tmp, self.path)
        self.reload()

    def save(self):
        """Back up, write atomically, then re-read to prove the file is sound.

        Raises ShortcutsChanged, writing nothing, if the file on disk is no
        longer the one that was loaded: the caller must reload and redo its
        change on the fresh list rather than overwrite Steam's newer copy.
        """
        if self.changed_on_disk():
            raise ShortcutsChanged("Steam's shortcut list changed on disk since it was loaded")
        if not self._backed_up:
            self.backup()
            self._backed_up = True
        data = binvdf.dumps(self.root)
        check = binvdf.loads(data)
        if len(_shortcut_maps(check)) != len(self.shortcuts):
            raise binvdf.VDFError("refusing to save: shortcut count changed")
        fd, tmp = tempfile.mkstemp(prefix=".shortcuts-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if self.path.exists():
                shutil.copymode(self.path, tmp)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        if self.path.read_bytes() != data:
            raise OSError("shortcuts.vdf did not read back as written")
        self._stamp = self._disk_stamp()


def _shortcut_maps(root) -> list:
    for k, v in root.items():
        if k.lower() == "shortcuts" and isinstance(v, dict):
            return [e for e in v.values() if isinstance(e, dict)]
    return []

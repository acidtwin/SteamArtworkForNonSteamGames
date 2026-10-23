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

    def set_name(self, name: str):
        """Retitle the shortcut. One with no stored appid gets its current id written in first:
        Steam would otherwise work a new one out from the new name, losing its artwork."""
        stored, idkey = _get(self.entry, "appid", default=None)
        if not stored:
            self.entry.set_int32(idkey or "appid", binvdf._to_int32(self.appid))
        _, key = _get(self.entry, "AppName", "appname")
        self.entry.set_string(key or "AppName", name)
        self.name = name


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
            return _stamp_of(self.path.stat())
        except OSError:
            return None

    def reload(self):
        # Stamp from the open file itself, so a rewrite between the read and
        # the stat can't leave old contents paired with the new file's stamp.
        with open(self.path, "rb") as f:
            st = os.fstat(f.fileno())
            data = f.read()
        self.root = binvdf.loads(data)
        self.shortcuts = self._extract()
        self._backed_up = False
        self._stamp = _stamp_of(st)

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

    def backup(self, keep: Path | None = None) -> Path | None:
        """Copy the current file into the backup folder, keeping the newest
        MAX_BACKUPS (`keep` is never pruned: it is the one being restored)."""
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
        for old in [p for p in self._backup_files() if p != keep][MAX_BACKUPS:]:
            old.unlink(missing_ok=True)
        return dest

    def _backup_files(self) -> list[Path]:
        files = []
        for p in self.backup_dir.glob("shortcuts-*.vdf"):
            try:
                files.append((p.stat().st_mtime_ns, p.name, p))
            except OSError:  # removed since the glob
                continue
        return [p for *_, p in sorted(files, reverse=True)]

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
        data = backup.read_bytes()
        binvdf.loads(data)  # refuse a broken backup
        self.backup(keep=backup)
        self._atomic_write(data)
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
        stamp = self._atomic_write(data)
        if self.path.read_bytes() != data:
            raise OSError("shortcuts.vdf did not read back as written")
        self._stamp = stamp

    def _atomic_write(self, data: bytes):
        """Write via a synced temp file and rename; returns the new file's stamp."""
        fd, tmp = tempfile.mkstemp(prefix=".shortcuts-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            if self.path.exists():
                shutil.copymode(self.path, tmp)
            stamp = _stamp_of(os.stat(tmp))  # a rename keeps inode, mtime and size
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return stamp


@dataclass
class Difference:
    """How a backup differs from the list now: what restoring it would change."""

    back: list[str]  # games in the backup that aren't in the list now (they'd come back)
    gone: list[str]  # games in the list now that aren't in the backup (they'd go)
    renamed: int
    icons: int


def compare(backup: Path, now: list[Shortcut]) -> Difference:
    then = ShortcutsFile(backup).shortcuts
    old = {s.appid: s for s in then}
    new = {s.appid: s for s in now}
    both = old.keys() & new.keys()
    return Difference(
        back=[old[a].name for a in old if a not in new],
        gone=[new[a].name for a in new if a not in old],
        renamed=sum(1 for a in both if old[a].name != new[a].name),
        icons=sum(1 for a in both if old[a].icon != new[a].icon),
    )


def _stamp_of(st) -> tuple:
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _shortcut_maps(root) -> list:
    for k, v in root.items():
        if k.lower() == "shortcuts" and isinstance(v, dict):
            return [e for e in v.values() if isinstance(e, dict)]
    return []

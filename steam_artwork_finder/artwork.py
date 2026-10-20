"""Where each artwork type lives in Steam's grid folder, and how to save it."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .shortcuts import Shortcut, ShortcutsChanged, ShortcutsFile

KEEP_REPLACED_DAYS = 30
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".ico")
MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/vnd.microsoft.icon": ".ico",
    "image/x-icon": ".ico",
}


@dataclass(frozen=True)
class Slot:
    key: str
    label: str
    endpoint: str
    suffix: str  # file name is f"{appid}{suffix}{ext}"
    aspect: float  # width / height, for previews
    query: dict = field(default_factory=dict)
    animates: bool = False  # Steam plays animated files for this piece

    def search(self, animated: bool = False) -> dict:
        """The SteamGridDB filters for this piece, with or without animated options."""
        q = dict(self.query)
        if animated and self.animates:
            q["types"] = "static,animated"
            q["mimes"] += ",image/webp"  # animated options are APNG or WebP
        return q


SLOTS: dict[str, Slot] = {
    s.key: s
    for s in (
        Slot("grid", "Cover", "grids", "p", 600 / 900,
             {"dimensions": "600x900,342x482,660x930", "mimes": "image/png,image/jpeg", "types": "static"}, True),
        Slot("wide", "Wide cover", "grids", "", 920 / 430,
             {"dimensions": "460x215,920x430", "mimes": "image/png,image/jpeg", "types": "static"}, True),
        Slot("hero", "Banner", "heroes", "_hero", 1920 / 620,
             {"mimes": "image/png,image/jpeg", "types": "static"}, True),
        Slot("logo", "Logo", "logos", "_logo", 16 / 9,
             {"mimes": "image/png", "types": "static"}, True),
        Slot("icon", "Icon", "icons", "_icon", 1.0,
             {"mimes": "image/png,image/vnd.microsoft.icon", "types": "static"}),
    )
}


def existing(grid_dir: Path, appid: int, slot: Slot) -> Path | None:
    stem = f"{appid}{slot.suffix}"
    for ext in IMAGE_EXTS:
        p = grid_dir / (stem + ext)
        if p.exists():
            return p
    return None


def current_icon(shortcut: Shortcut, grid_dir: Path) -> Path | None:
    if shortcut.icon:
        p = Path(shortcut.icon.strip('"'))
        if p.exists():
            return p
    return existing(grid_dir, shortcut.appid, SLOTS["icon"])


@dataclass
class Change:
    """Everything needed to undo one artwork change."""

    slot: Slot
    shortcut: Shortcut
    new_file: Path | None
    moved: list[tuple[Path, Path]]  # (original path, where it was moved to)
    old_icon: str | None = None
    reloaded: bool = False  # shortcuts.vdf had changed on disk and was re-read


_ASIDE_STAMP = re.compile(r"\.(\d{8}-\d{6})-\d+\.[^.]+$")


def _set_aside_time(p: Path) -> float:
    """When a file was set aside: the time in its name, else its mtime."""
    m = _ASIDE_STAMP.search(p.name)
    if m:
        try:
            return time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S"))
        except (ValueError, OverflowError):
            pass
    return p.stat().st_mtime


def prune_replaced(grid_dir: Path, days: int = KEEP_REPLACED_DAYS):
    """Drop set-aside artwork older than `days`; undo only needs recent files."""
    old_dir = grid_dir / ".replaced"
    cutoff = time.time() - days * 86400
    try:
        for p in old_dir.iterdir():
            try:
                if p.is_file() and _set_aside_time(p) < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def set_icon(shortcuts_file: ShortcutsFile, shortcut: Shortcut, path: str) -> bool:
    """Point the shortcut's icon at `path` and save.

    The change is always made on the live list, found by appid, so a
    `Shortcut` held from before a reload still lands in the saved file. If
    Steam rewrote shortcuts.vdf meanwhile, the fresh list is loaded first.
    Returns True when the list was reloaded (or `shortcut` was stale), so the
    caller can rebuild anything that indexes into it.
    """
    reloaded = _edit(shortcuts_file, shortcut, lambda sc: sc.set_icon(path))
    shortcut.icon = path
    return reloaded


def set_name(shortcuts_file: ShortcutsFile, shortcut: Shortcut, name: str) -> bool:
    """Retitle the shortcut and save, keeping its appid (so its artwork stays linked).
    Made on the live list like `set_icon`, with the same return value."""
    reloaded = _edit(shortcuts_file, shortcut, lambda sc: sc.set_name(name))
    shortcut.name = name
    return reloaded


def _edit(shortcuts_file: ShortcutsFile, shortcut: Shortcut, change) -> bool:
    reloaded = False
    if shortcuts_file.changed_on_disk():
        shortcuts_file.reload()
        reloaded = True
    live = shortcuts_file.find(shortcut.appid)
    if live is None:
        raise ShortcutsChanged(f"{shortcut.name} is no longer in Steam's game list")
    change(live)
    try:
        shortcuts_file.save()
    except ShortcutsChanged:  # Steam wrote between the check and the save
        shortcuts_file.reload()
        reloaded = True
        live = shortcuts_file.find(shortcut.appid)
        if live is None:
            raise
        change(live)
        shortcuts_file.save()
    return reloaded or live is not shortcut


def _set_aside(grid_dir: Path, stem: str) -> list[tuple[Path, Path]]:
    moved = []
    old_dir = grid_dir / ".replaced"
    for e in IMAGE_EXTS:
        old = grid_dir / (stem + e)
        if old.exists():
            old_dir.mkdir(exist_ok=True)
            dest = old_dir / f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6}{e}"
            old.replace(dest)
            os.utime(dest)  # the 30 days start now, not when the file was first made
            moved.append((old, dest))
    return moved


def _put_back(moved: list[tuple[Path, Path]]):
    for original, aside in moved:
        if aside.exists():
            aside.replace(original)


def save_asset(
    grid_dir: Path,
    shortcut: Shortcut,
    slot: Slot,
    data: bytes,
    mime: str,
    shortcuts_file: ShortcutsFile | None = None,
) -> Change:
    """Write artwork into the grid folder; old files go to grid/.replaced/."""
    if _looks_like_text(data):
        # A captive portal or CDN error page served as 200 must not replace real artwork.
        raise ValueError("the download isn't a picture")
    grid_dir.mkdir(parents=True, exist_ok=True)
    ext = MIME_EXT.get(mime) or _sniff_ext(data)
    if ext == ".webp":
        # Steam only looks for .png and .jpg names, but reads the file by its contents, so an
        # animated WebP saved as .png plays (the SteamGridDB plugin for Steam does the same).
        ext = ".png"
    stem = f"{shortcut.appid}{slot.suffix}"
    dest = grid_dir / (stem + ext)
    tmp = dest.with_name(dest.name + ".part")
    try:
        tmp.write_bytes(data)  # written before anything is moved, so a full disk loses nothing
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    moved = _set_aside(grid_dir, stem)
    try:
        tmp.replace(dest)
        change = Change(slot, shortcut, dest, moved)
        if slot.key == "icon" and shortcuts_file is not None:
            change.old_icon = shortcut.icon
            change.reloaded = set_icon(shortcuts_file, shortcut, str(dest))
    except BaseException:
        dest.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
        _put_back(moved)
        raise
    return change


def remove_asset(
    grid_dir: Path, shortcut: Shortcut, slot: Slot, shortcuts_file: ShortcutsFile | None = None
) -> Change:
    """Take a piece of artwork away (kept in grid/.replaced/ for undo)."""
    moved = _set_aside(grid_dir, f"{shortcut.appid}{slot.suffix}")
    change = Change(slot, shortcut, None, moved)
    try:
        if slot.key == "icon" and shortcuts_file is not None and shortcut.icon:
            change.old_icon = shortcut.icon
            change.reloaded = set_icon(shortcuts_file, shortcut, "")
    except BaseException:
        _put_back(moved)
        raise
    return change


def undo(change: Change, shortcuts_file: ShortcutsFile | None = None):
    if change.new_file is not None and change.new_file.exists():
        change.new_file.unlink()
    _put_back(change.moved)
    if change.old_icon is not None and shortcuts_file is not None:
        change.reloaded = set_icon(shortcuts_file, change.shortcut, change.old_icon)


def is_animated(data: bytes) -> bool:
    """True for an animated PNG (APNG) or animated WebP, judged from the start of the file."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        # APNG puts an acTL chunk before the first image data.
        pos = 8
        while pos + 8 <= len(data):
            length = int.from_bytes(data[pos:pos + 4], "big")
            kind = data[pos + 4:pos + 8]
            if kind == b"acTL":
                return True
            if kind in (b"IDAT", b"IEND"):
                return False
            pos += 12 + length
        return False
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and data[12:16] == b"VP8X" and len(data) > 20:
        return bool(data[20] & 0x02)  # the animation flag
    return False


def file_is_animated(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
            if not head.startswith(b"\x89PNG\r\n\x1a\n"):
                return is_animated(head)
            # Walk the PNG chunk headers: large metadata chunks can push acTL past the first 4 KB.
            pos = 8
            for _ in range(64):
                f.seek(pos)
                hdr = f.read(8)
                if len(hdr) < 8:
                    return False
                kind = hdr[4:]
                if kind == b"acTL":
                    return True
                if kind in (b"IDAT", b"IEND"):
                    return False
                pos += 12 + int.from_bytes(hdr[:4], "big")
            return False
    except OSError:
        return False


def _looks_like_text(data: bytes) -> bool:
    head = data[:64].lstrip()
    return not head or head[:1] in (b"<", b"{", b"[")


def _sniff_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:4] == b"\x00\x00\x01\x00":
        return ".ico"
    return ".png"

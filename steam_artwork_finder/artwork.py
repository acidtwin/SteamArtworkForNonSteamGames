"""Where each artwork type lives in Steam's grid folder, and how to save it."""

from __future__ import annotations

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


SLOTS: dict[str, Slot] = {
    s.key: s
    for s in (
        Slot("grid", "Cover", "grids", "p", 600 / 900,
             {"dimensions": "600x900,342x482,660x930", "mimes": "image/png,image/jpeg", "types": "static"}),
        Slot("wide", "Wide cover", "grids", "", 920 / 430,
             {"dimensions": "460x215,920x430", "mimes": "image/png,image/jpeg", "types": "static"}),
        Slot("hero", "Banner", "heroes", "_hero", 1920 / 620,
             {"mimes": "image/png,image/jpeg", "types": "static"}),
        Slot("logo", "Logo", "logos", "_logo", 16 / 9,
             {"mimes": "image/png", "types": "static"}),
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


def prune_replaced(grid_dir: Path, days: int = KEEP_REPLACED_DAYS):
    """Drop set-aside artwork older than `days`; undo only needs recent files."""
    old_dir = grid_dir / ".replaced"
    cutoff = time.time() - days * 86400
    try:
        for p in old_dir.iterdir():
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def set_icon(shortcuts_file: ShortcutsFile, shortcut: Shortcut, path: str) -> bool:
    """Point the shortcut's icon at `path` and save.

    If Steam rewrote shortcuts.vdf meanwhile, the fresh list is loaded and
    the change is redone on it. Returns True when the list was reloaded, so
    the caller can rebuild anything that indexes into it.
    """
    try:
        shortcut.set_icon(path)
        shortcuts_file.save()
        return False
    except ShortcutsChanged:
        shortcuts_file.reload()
        fresh = shortcuts_file.find(shortcut.appid)
        if fresh is None:
            raise
        fresh.set_icon(path)
        shortcuts_file.save()
        shortcut.icon = path
        return True


def _set_aside(grid_dir: Path, stem: str) -> list[tuple[Path, Path]]:
    moved = []
    old_dir = grid_dir / ".replaced"
    for e in IMAGE_EXTS:
        old = grid_dir / (stem + e)
        if old.exists():
            old_dir.mkdir(exist_ok=True)
            dest = old_dir / f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 10**6}{e}"
            old.replace(dest)
            moved.append((old, dest))
    return moved


def save_asset(
    grid_dir: Path,
    shortcut: Shortcut,
    slot: Slot,
    data: bytes,
    mime: str,
    shortcuts_file: ShortcutsFile | None = None,
) -> Change:
    """Write artwork into the grid folder; old files go to grid/.replaced/."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    ext = MIME_EXT.get(mime) or _sniff_ext(data)
    stem = f"{shortcut.appid}{slot.suffix}"
    moved = _set_aside(grid_dir, stem)
    dest = grid_dir / (stem + ext)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    change = Change(slot, shortcut, dest, moved)
    if slot.key == "icon" and shortcuts_file is not None:
        change.old_icon = shortcut.icon
        change.reloaded = set_icon(shortcuts_file, shortcut, str(dest))
    return change


def remove_asset(
    grid_dir: Path, shortcut: Shortcut, slot: Slot, shortcuts_file: ShortcutsFile | None = None
) -> Change:
    """Take a piece of artwork away (kept in grid/.replaced/ for undo)."""
    moved = _set_aside(grid_dir, f"{shortcut.appid}{slot.suffix}")
    change = Change(slot, shortcut, None, moved)
    if slot.key == "icon" and shortcuts_file is not None and shortcut.icon:
        change.old_icon = shortcut.icon
        change.reloaded = set_icon(shortcuts_file, shortcut, "")
    return change


def undo(change: Change, shortcuts_file: ShortcutsFile | None = None):
    if change.new_file is not None and change.new_file.exists():
        change.new_file.unlink()
    for original, aside in change.moved:
        if aside.exists():
            aside.replace(original)
    if change.old_icon is not None and shortcuts_file is not None:
        change.reloaded = set_icon(shortcuts_file, change.shortcut, change.old_icon)


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

"""Where each artwork type lives in Steam's grid folder, and how to save it."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .shortcuts import Shortcut, ShortcutsFile

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


def save_asset(
    grid_dir: Path,
    shortcut: Shortcut,
    slot: Slot,
    data: bytes,
    mime: str,
    shortcuts_file: ShortcutsFile | None = None,
) -> Path:
    """Write artwork into the grid folder; old files go to grid/.replaced/."""
    grid_dir.mkdir(parents=True, exist_ok=True)
    ext = MIME_EXT.get(mime) or _sniff_ext(data)
    stem = f"{shortcut.appid}{slot.suffix}"
    old_dir = grid_dir / ".replaced"
    for e in IMAGE_EXTS:
        old = grid_dir / (stem + e)
        if old.exists():
            old_dir.mkdir(exist_ok=True)
            old.replace(old_dir / f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}{e}")
    dest = grid_dir / (stem + ext)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    if slot.key == "icon" and shortcuts_file is not None:
        shortcut.set_icon(str(dest))
        shortcuts_file.save()
    return dest


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

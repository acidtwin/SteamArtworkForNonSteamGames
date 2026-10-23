"""The icon built into a Windows .exe, read straight from its resources (no Wine needed).

Only the headers and the resource section are read, so a large game .exe costs a few
small reads. Anything that isn't a Windows program (a script, a Linux binary, a
launcher command) gives None, and the caller moves on to its next fallback.
"""

from __future__ import annotations

import os
import shlex
import struct
from pathlib import Path

RT_ICON, RT_GROUP_ICON = 3, 14


def exe_path(exe_field: str) -> Path | None:
    """The .exe a shortcut's Exe field points at, if it is one and it exists."""
    text = exe_field.strip()
    posix = os.name != "nt"  # POSIX rules would eat the backslashes in Windows paths
    try:
        parts = shlex.split(text, posix=posix) if text else []
    except ValueError:
        parts = [text]
    if not parts:
        return None
    path = Path(parts[0].strip('"'))
    if path.suffix.lower() != ".exe" or not path.is_file():
        return None
    return path


def exe_icon(path: Path) -> bytes | None:
    """The biggest icon in the program: PNG bytes, or a one-image .ico. None if it has none."""
    try:
        with open(path, "rb") as f:
            return _read_icon(f)
    except (OSError, struct.error, ValueError, IndexError):
        return None


def _read_icon(f) -> bytes | None:
    def at(offset: int, size: int) -> bytes:
        f.seek(offset)
        data = f.read(size)
        if len(data) != size:
            raise ValueError("cut short")
        return data

    if at(0, 2) != b"MZ":
        return None
    pe = struct.unpack("<I", at(0x3C, 4))[0]
    if at(pe, 4) != b"PE\0\0":
        return None
    n_sections, = struct.unpack("<H", at(pe + 6, 2))
    opt_size, = struct.unpack("<H", at(pe + 20, 2))
    opt = pe + 24
    magic, = struct.unpack("<H", at(opt, 2))
    dirs = opt + (96 if magic == 0x10B else 112)
    n_dirs, = struct.unpack("<I", at(dirs - 4, 4))
    if n_dirs < 3:
        return None
    res_rva, res_size = struct.unpack("<II", at(dirs + 2 * 8, 8))
    if not res_rva:
        return None
    sections = []
    for i in range(min(n_sections, 96)):
        _, vsize, vaddr, raw_size, raw_ptr = struct.unpack("<8sIIII", at(opt + opt_size + i * 40, 24))
        sections.append((vaddr, max(vsize, raw_size), raw_ptr))

    def offset(rva: int) -> int:
        for vaddr, size, raw in sections:
            if vaddr <= rva < vaddr + size:
                return rva - vaddr + raw
        raise ValueError("address outside the file")

    root = offset(res_rva)

    def entries(directory: int) -> list[tuple[int | None, int]]:
        """(id, offset of child) for each entry; id is None for a named one."""
        named, ids = struct.unpack("<HH", at(root + directory + 12, 4))
        out = []
        for i in range(min(named + ids, 4096)):
            name, child = struct.unpack("<II", at(root + directory + 16 + i * 8, 8))
            out.append((None if name & 0x80000000 else name, child))
        return out

    def first_data(child: int) -> bytes:
        """Follow subdirectories down to the first data entry (any language)."""
        for _ in range(3):
            if not child & 0x80000000:
                break
            child = entries(child & 0x7FFFFFFF)[0][1]
        rva, size = struct.unpack("<II", at(root + child, 8))
        if size > 16 * 1024 * 1024:
            raise ValueError("too big")
        return at(offset(rva), size)

    top = entries(0)
    groups = next((c for i, c in top if i == RT_GROUP_ICON), None)
    icons = next((c for i, c in top if i == RT_ICON), None)
    if groups is None or icons is None:
        return None
    group = first_data(entries(groups & 0x7FFFFFFF)[0][1])
    count, = struct.unpack("<H", group[4:6])
    best = None
    for i in range(count):
        w, h, colours, _, planes, bits, size, icon_id = struct.unpack("<BBBBHHIH", group[6 + i * 14: 20 + i * 14])
        rank = ((w or 256) * (h or 256), bits)
        if best is None or rank > best[0]:
            best = (rank, (w, h, colours, planes, bits), icon_id)
    if best is None:
        return None
    by_id = dict(entries(icons & 0x7FFFFFFF))
    if best[2] not in by_id:
        return None
    data = first_data(by_id[best[2]])
    if data.startswith(b"\x89PNG"):
        return data
    w, h, colours, planes, bits = best[1]
    # A bare bitmap: wrap it in a one-image .ico so image readers can open it.
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", w, h, colours, 0, planes, bits, len(data), 22) + data

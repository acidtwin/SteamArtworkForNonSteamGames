"""Lossless reader/writer for Steam's binary VDF format (shortcuts.vdf).

Every value keeps its on-disk type so that a load followed by a dump gives
back exactly the same bytes. Unknown keys and odd fields that other tools
or Steam itself add are carried through untouched.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

T_MAP = 0x00
T_STRING = 0x01
T_INT32 = 0x02
T_FLOAT32 = 0x03
T_POINTER = 0x04
T_WSTRING = 0x05
T_COLOR = 0x06
T_UINT64 = 0x07
T_END = 0x08
T_INT64 = 0x0A
T_END_ALT = 0x0B


class VDFError(ValueError):
    pass


@dataclass
class Typed:
    """A scalar that remembers the binary type it was read as."""

    type: int
    value: object

    def __eq__(self, other):
        if isinstance(other, Typed):
            return self.type == other.type and self.value == other.value
        return self.value == other


class VDFMap(dict):
    """An ordered mapping of key -> VDFMap | Typed."""

    def get_value(self, key, default=None):
        v = self.get(key)
        if v is None:
            return default
        return v.value if isinstance(v, Typed) else v

    def set_string(self, key, value: str):
        self[key] = Typed(T_STRING, value)

    def set_int32(self, key, value: int):
        self[key] = Typed(T_INT32, value)


def _read_cstr(data: bytes, pos: int) -> tuple[bytes, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise VDFError(f"unterminated string at offset {pos}")
    return data[pos:end], end + 1


def _decode(raw: bytes) -> str:
    # Steam writes UTF-8, but older shortcut editors have written other
    # encodings. surrogateescape lets those bytes round-trip unchanged.
    return raw.decode("utf-8", "surrogateescape")


def _encode(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def loads(data: bytes) -> VDFMap:
    root = VDFMap()
    stack = [root]
    pos = 0
    n = len(data)
    while pos < n:
        t = data[pos]
        pos += 1
        if t in (T_END, T_END_ALT):
            if len(stack) == 1:
                # Trailing end marker of the root map.
                if pos != n and data[pos:].strip(b"\x08") != b"":
                    raise VDFError(f"unexpected data after root at offset {pos}")
                return root
            stack.pop()
            continue
        raw_key, pos = _read_cstr(data, pos)
        key = _decode(raw_key)
        cur = stack[-1]
        if t == T_MAP:
            child = VDFMap()
            cur[key] = child
            stack.append(child)
        elif t in (T_STRING,):
            raw, pos = _read_cstr(data, pos)
            cur[key] = Typed(t, _decode(raw))
        elif t == T_WSTRING:
            end = pos
            while data[end : end + 2] != b"\x00\x00":
                end += 2
                if end > n:
                    raise VDFError("unterminated wide string")
            cur[key] = Typed(t, data[pos:end].decode("utf-16-le"))
            pos = end + 2
        elif t in (T_INT32, T_POINTER, T_COLOR):
            cur[key] = Typed(t, struct.unpack_from("<i", data, pos)[0])
            pos += 4
        elif t == T_FLOAT32:
            cur[key] = Typed(t, struct.unpack_from("<f", data, pos)[0])
            pos += 4
        elif t == T_UINT64:
            cur[key] = Typed(t, struct.unpack_from("<Q", data, pos)[0])
            pos += 8
        elif t == T_INT64:
            cur[key] = Typed(t, struct.unpack_from("<q", data, pos)[0])
            pos += 8
        else:
            raise VDFError(f"unknown type 0x{t:02x} at offset {pos - 1}")
    if len(stack) != 1:
        raise VDFError("file ended inside a nested map")
    return root


def dumps(obj: VDFMap) -> bytes:
    out = bytearray()
    _dump_map(obj, out)
    out.append(T_END)
    return bytes(out)


def _dump_map(obj: dict, out: bytearray):
    for key, val in obj.items():
        k = _encode(str(key)) + b"\x00"
        if isinstance(val, dict):
            out.append(T_MAP)
            out += k
            _dump_map(val, out)
            out.append(T_END)
            continue
        if not isinstance(val, Typed):
            val = _infer(val)
        out.append(val.type)
        out += k
        t, v = val.type, val.value
        if t == T_STRING:
            out += _encode(v) + b"\x00"
        elif t == T_WSTRING:
            out += v.encode("utf-16-le") + b"\x00\x00"
        elif t in (T_INT32, T_POINTER, T_COLOR):
            out += struct.pack("<i", _to_int32(v))
        elif t == T_FLOAT32:
            out += struct.pack("<f", v)
        elif t == T_UINT64:
            out += struct.pack("<Q", v)
        elif t == T_INT64:
            out += struct.pack("<q", v)
        else:
            raise VDFError(f"cannot write type 0x{t:02x}")


def _to_int32(v: int) -> int:
    # Accept unsigned app ids (>= 2**31) and store them as Steam does.
    if v >= 2**31:
        v -= 2**32
    return v


def _infer(v) -> Typed:
    if isinstance(v, bool):
        return Typed(T_INT32, int(v))
    if isinstance(v, int):
        return Typed(T_INT32, v)
    if isinstance(v, float):
        return Typed(T_FLOAT32, v)
    return Typed(T_STRING, str(v))

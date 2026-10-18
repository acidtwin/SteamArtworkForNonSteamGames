import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _s(key, val):
    return b"\x01" + key.encode() + b"\x00" + val.encode() + b"\x00"


def _i(key, val):
    return b"\x02" + key.encode() + b"\x00" + struct.pack("<i", val)


def _m(key, body):
    return b"\x00" + key.encode() + b"\x00" + body + b"\x08"


def make_entry(appid, name, exe, icon="", tags=()):
    body = (
        _i("appid", appid)
        + _s("AppName", name)
        + _s("Exe", exe)
        + _s("StartDir", '"/home/deck/Games/"')
        + _s("icon", icon)
        + _s("ShortcutPath", "")
        + _s("LaunchOptions", "--fullscreen")
        + _i("IsHidden", 0)
        + _i("AllowDesktopConfig", 1)
        + _i("AllowOverlay", 1)
        + _i("OpenVR", 0)
        + _i("Devkit", 0)
        + _s("DevkitGameID", "")
        + _i("DevkitOverrideAppID", 0)
        + _i("LastPlayTime", 1729000000)
        + _s("FlatpakAppID", "")
        + _m("tags", b"".join(_s(str(i), t) for i, t in enumerate(tags)))
    )
    return body


def make_shortcuts(entries):
    inner = b"".join(_m(str(i), e) for i, e in enumerate(entries))
    return _m("shortcuts", inner) + b"\x08"


SAMPLE_ENTRIES = [
    # appid stored signed, as Steam does for ids >= 2**31
    (-1734531011, "Celeste", '"/home/deck/Games/Celeste/Celeste"', "", ("Favorites",)),
    (-2011916284, "Hollow Knight™", '"/opt/hk/hollow_knight.x86_64"', "/old/icon.png", ()),
    (-1000000000, "Pokémon émulé", '"/usr/bin/flatpak"', "", ("Emulators", "RPG")),
]


@pytest.fixture
def sample_bytes():
    return make_shortcuts([make_entry(*e) for e in SAMPLE_ENTRIES])


@pytest.fixture
def steam_root(tmp_path, sample_bytes):
    root = tmp_path / "Steam"
    cfg = root / "userdata" / "12345678" / "config"
    cfg.mkdir(parents=True)
    (cfg / "shortcuts.vdf").write_bytes(sample_bytes)
    (root / "userdata" / "87654321" / "config").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "config" / "loginusers.vdf").write_text(
        '"users"\n{\n'
        '\t"76561197972611406"\n\t{\n\t\t"AccountName"\t\t"deck"\n'
        '\t\t"PersonaName"\t\t"Deck User"\n\t\t"MostRecent"\t\t"1"\n\t}\n'
        '\t"76561198047920049"\n\t{\n\t\t"PersonaName"\t\t"Other"\n\t\t"MostRecent"\t\t"0"\n\t}\n'
        "}\n"
    )
    return root

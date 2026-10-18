# Steam Artwork Finder

A small desktop app that gives your **non-Steam games** proper artwork in Steam. It finds Steam on its own, lists every non-Steam shortcut you've added, and pulls all five artwork types from [SteamGridDB](https://www.steamgriddb.com) in one click. You can then swap any piece you don't like from a visual picker.

Linux first, including Steam Deck desktop mode: the window fits the Deck's 1280×800 screen and the buttons are sized for touch. Windows support is built in but not yet packaged or tested on real hardware.

![Main window](docs/screenshot.png)
*The main window, shown with placeholder artwork from the test suite.*

## Features

- **Zero-config Steam detection.** Finds native, Flatpak and Snap installs on Linux (`~/.local/share/Steam`, `~/.steam/steam`, `~/.var/app/com.valvesoftware.Steam/...`, `~/snap/steam/...`) and the registry path on Windows. Picks the account you last signed in with, and lets you switch if there's more than one.
- **Reads `shortcuts.vdf` directly.** No parser rules or config files to set up. Every field Steam or other tools have written is kept exactly as it was.
- **All 5 artwork types:** Cover, Wide cover, Banner, Logo and Icon. (SteamGridDB calls the first three Grid, Wide Grid and Hero.)
- **One click for everything.** "Get missing artwork for all games" matches each shortcut on SteamGridDB and fills in whatever it's missing with the top-rated option.
- **Visual picker.** Click *Change…* on any artwork to see every option SteamGridDB has and double-click the one you want.
- **Fix wrong matches.** *Wrong game?* lets you search SteamGridDB yourself. Your choice is remembered.
- **Safe writes.** See [How it keeps your shortcuts safe](#how-it-keeps-your-shortcuts-safe).

## Getting started

### 1. Get a SteamGridDB API key

Sign in at [steamgriddb.com](https://www.steamgriddb.com), then go to **Preferences → API** ([direct link](https://www.steamgriddb.com/profile/preferences/api)) and copy your key. It's free. The app asks for it the first time you open it and checks it before saving.

### 2. Install and run

You need Python 3.10 or newer.

```bash
git clone https://github.com/acidtwin/steam-artwork-finder.git
cd steam-artwork-finder
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
steam-artwork-finder
```

To run from the source folder without installing:

```bash
pip install -r requirements.txt
python3 -m steam_artwork_finder
```

### 3. Use it

1. Pick a game on the left, or press **Get missing artwork for all games**.
2. Click **Change…** on any artwork to choose a different one.
3. **Restart Steam** to see the new artwork.

If Steam is running, the app shows a reminder. Cover, Wide cover, Banner and Logo images can be added while Steam is open. Icons are stored inside `shortcuts.vdf`, which Steam rewrites when it exits, so close Steam before changing icons.

## Where things are saved

| Artwork   | File in `Steam/userdata/<your id>/config/grid/` |
|-----------|-------------------------------------------------|
| Cover      | `<appid>p.png` (or `.jpg`)                     |
| Wide cover | `<appid>.png`                                  |
| Banner     | `<appid>_hero.png`                             |
| Logo      | `<appid>_logo.png`                              |
| Icon      | `<appid>_icon.png`, linked from the shortcut's `icon` field in `shortcuts.vdf` |

`<appid>` is the shortcut's id from `shortcuts.vdf`. For very old shortcuts that don't store one, it's worked out the same way Steam does it.

The app's own settings (your API key and remembered game matches) live in `~/.config/steam-artwork-finder/config.json` on Linux or `%APPDATA%\steam-artwork-finder\config.json` on Windows. The file is readable only by you. Thumbnails are cached in `~/.cache/steam-artwork-finder`.

## How it keeps your shortcuts safe

- Before the first change in a session, `shortcuts.vdf` is copied to `config/shortcuts-backups/shortcuts-<date>-<time>.vdf`. The last 10 backups are kept.
- The new file is written to a temporary file, flushed to disk and then swapped into place, so a crash or power cut can't leave a half-written file.
- Before writing, the app re-parses its own output and refuses to save if the number of shortcuts would change. After writing, it reads the file back to check it matches.
- The binary VDF reader keeps every value's original type, so loading and saving an untouched file gives back exactly the same bytes. Unusual text encodings written by other tools are preserved too.
- Artwork you replace isn't deleted. It's moved to `grid/.replaced/`.

To restore a backup, close Steam and copy the backup file over `config/shortcuts.vdf`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The tests build synthetic `shortcuts.vdf` files, so they don't need Steam installed or a SteamGridDB key.

### Project layout

| Path | What it does |
|------|--------------|
| `steam_artwork_finder/steam.py` | Finds Steam installs, users and whether Steam is running |
| `steam_artwork_finder/binvdf.py` | Lossless binary VDF reader and writer |
| `steam_artwork_finder/shortcuts.py` | Shortcut list, app ids, backups and safe saving |
| `steam_artwork_finder/sgdb.py` | SteamGridDB API v2 client |
| `steam_artwork_finder/artwork.py` | Artwork types, file names and saving |
| `steam_artwork_finder/gui/` | The Qt (PySide6) window and dialogs |
| `packaging/` | Desktop entry for Linux |

## Roadmap

- AppImage and Flatpak builds for Linux
- Windows build with PyInstaller, tested on a real install
- Animated artwork option
- Undo for the last change

## Credits

Artwork comes from [SteamGridDB](https://www.steamgriddb.com) and its community of artists. This project isn't affiliated with Valve or SteamGridDB.

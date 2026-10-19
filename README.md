# Steam Artwork for Non-Steam Games

A small desktop app that gives your **non-Steam games** proper artwork in Steam. It finds Steam on its own, lists every non-Steam shortcut you've added, and pulls all five artwork types from [SteamGridDB](https://www.steamgriddb.com) in one click. You can then swap any piece you don't like from a visual picker.

Linux first, including Steam Deck desktop mode: the window fits the Deck's 1280×800 screen and the buttons are sized for touch. Windows support is built in but not yet packaged or tested on real hardware.

![Start page](docs/screenshot.png)

![A game's page](docs/game-page.png)

*Shown with placeholder artwork from the test suite.*

## Features

- **Zero-config Steam detection.** Finds native, Flatpak and Snap installs on Linux (`~/.local/share/Steam`, `~/.steam/steam`, `~/.var/app/com.valvesoftware.Steam/...`, `~/snap/steam/...`) and the registry path on Windows. Picks the account you last signed in with, and lets you switch if there's more than one.
- **Reads `shortcuts.vdf` directly.** No parser rules or config files to set up. Every field Steam or other tools have written is kept exactly as it was.
- **All 5 artwork types:** Cover, Wide cover, Banner, Logo and Icon. (SteamGridDB calls the first three Grid, Wide Grid and Hero.)
- **Looks like your Steam library.** The start page is a cover wall like Steam's library home, with games that have no cover drawn as dashed tiles so the gaps stand out.
- **One click for everything.** "Get artwork for N games" matches each shortcut on SteamGridDB and fills in whatever it's missing with the top-rated option. It never replaces artwork you already have.
- **Every piece explained, with its options beside it.** A game's page has a row for each piece, saying where Steam shows it and its size, with the art in use and a strip of SteamGridDB options next to it. Hover an option to preview it, click to use it, and Undo if you change your mind. "See all" opens every option with style filters.
- **Logo on the banner.** Tick "Show it on the banner" to judge a logo against the banner it will sit on.
- **Fix wrong matches.** *Not this game?* lets you search SteamGridDB yourself, and your choice is remembered. Games matched by a best guess are tagged "Check match".
- **Safe writes.** See [How it keeps your shortcuts safe](#how-it-keeps-your-shortcuts-safe). Backups can be restored from inside the app.

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

To add it to your app menu on Linux, install the launcher and its icon:

```bash
install -Dm644 packaging/steam-artwork-finder.desktop ~/.local/share/applications/steam-artwork-finder.desktop
install -Dm644 packaging/steam-artwork-finder.svg ~/.local/share/icons/hicolor/scalable/apps/steam-artwork-finder.svg
```

### 3. Use it

1. Press **Get artwork for N games** to fill every gap at once, or pick a game on the left.
2. On a game's page, click any option beside a piece to use it (Undo is right there), or **See all** for the full list.
3. **Restart Steam** to see the new artwork.

The ⋯ menu has your API key, **Restore a backup**, and a shortcut to the artwork folder.

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

To restore one, close Steam and use **⋯ → Restore a backup**. The list you had before is backed up too, so a restore can itself be undone.

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
| `steam_artwork_finder/gui/` | The Qt (PySide6) window: `library.py` (game list, start page), `gamepage.py` (artwork rows), `tray.py` (See all), `theme.py` (colours from DESIGN.md) |
| `packaging/` | Desktop entry for Linux |

## Roadmap

- AppImage and Flatpak builds for Linux
- Windows build with PyInstaller, tested on a real install
- Animated artwork option

## Credits

Artwork comes from [SteamGridDB](https://www.steamgriddb.com) and its community of artists. This project isn't affiliated with Valve or SteamGridDB.

# To do

Ideas and jobs saved for later, once the app is more final.

## Steam Deck

- Touch support: a way to open the right-click game menu (long press or a ⋯ button), the
  Clear pill on game page artwork (only shows on hover now), and previews of options
  (also hover only).

## Bug reports

- Debug logging the user can switch on and off (a menu toggle), writing a log file to attach
  to bug reports. It can replace the hidden wheel-debug switch in gui/smooth.py.

## Release

- Submit to Flathub: done by hand by the developer (Flathub bans AI-written submissions and needs AI-written code disclosed); expect them to want more history first. A ready-built .flatpak is attached to each GitHub release meanwhile.

### Where to publish at 1.0

- **Flathub:** the main one. It's the app store on the Steam Deck, Bazzite and most gaming distros.
- **PyPI,** so people can `pipx install` it. Almost free, as the app is already a Python package.
- **AUR:** a short PKGBUILD for Arch, CachyOS and EndeavourOS users.

Possible later, not worth it for now:

- **AppImage:** one download, but Qt makes it about 150 MB, and Flathub reaches the same people.
- **.deb, .rpm and Snap:** a lot of upkeep for little gain, since Flathub covers those distros.
- **Windows .exe** (PyInstaller), once the Windows version is a goal.

## Later

- **TheGamesDB as a gap-filling artwork source** (box art, fanart and clear logos, cropped to
  Steam's shapes). Parked 2026-10-19: it needs a developer key, applied for at
  https://api.thegamesdb.net/key.php after signing up, which felt too much for a quick, simple app.
  Accounts are also limited to 1,000 API requests a month, with more only through a paid
  Patreon tier, so it doesn't meet the free-only rule at any real scale.

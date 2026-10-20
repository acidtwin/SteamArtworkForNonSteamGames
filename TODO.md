# To do

Ideas and jobs saved for later, once the app is more final.

## Steam Deck

- Touch support: a way to open the right-click game menu (long press or a ⋯ button), the
  Clear pill on game page artwork (only shows on hover now), and previews of options
  (also hover only).

## Release

- Write the Flatpak manifest for Flathub.

### Where to publish at 1.0

- **Flathub:** the main one. It's the app store on the Steam Deck, Bazzite and most gaming distros.
- **PyPI,** so people can `pipx install` it. Almost free, as the app is already a Python package.
- **AUR:** a short PKGBUILD for Arch, CachyOS and EndeavourOS users.

Possible later, not worth it for now:

- **AppImage:** one download, but Qt makes it about 150 MB, and Flathub reaches the same people.
- **.deb, .rpm and Snap:** a lot of upkeep for little gain, since Flathub covers those distros.
- **Windows .exe** (PyInstaller), once the Windows version is a goal.

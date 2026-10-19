# To do

Ideas and jobs saved for later, once the app is more final.

## Features

- **Rename games left with a generic title.** Games added to Steam often keep a title like
  "game.exe" or "launcher". Spot titles that match the .exe name or a generic word, search
  SteamGridDB by the game's folder name instead (for example `Games/Hollow Knight/game.exe`
  gives "Hollow Knight"), suggest "Rename to Hollow Knight?" on the match card, and write the
  new title on Apply. Keep the shortcut's stored ID so its artwork stays linked.
- **Sort the game list and cover wall**, for example A to Z, or games missing artwork first.

## Design ideas from the 2026-10-19 review (waiting on a decision)

- **Game page buttons:** the banner header has three filled buttons and two greens. Make Get All
  Artwork a quiet button when Apply is lit, and green only when it is the one thing to do.
- **Sticky bar on the game page:** Apply scrolls away with the banner. Show a slim bar (cover,
  name, Get All Artwork, Apply) once the banner is out of view, like Steam's play bar.
- **Sidebar filter reach:** "To Check" filters only the left list, not the cover wall (like Steam).
- **Line up the option strips:** each row's options start at a different x position. Use one
  column width for all rows, and smaller logo thumbnails so more fit at 1280.
- Account and Steam folder menus are still plain Qt menus; restyle them like the other menus.

## Steam Deck

- Touch support: a way to open the right-click game menu (long press or a ⋯ button), the
  Clear pill on game page artwork (only shows on hover now), and previews of options
  (also hover only).

## Release

- Write the Flatpak manifest for Flathub.

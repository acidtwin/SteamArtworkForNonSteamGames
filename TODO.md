# To do

Ideas and jobs saved for later, once the app is more final.

## Features

- **Rename games left with a generic title.** Games added to Steam often keep a title like
  "game.exe" or "launcher". Spot titles that match the .exe name or a generic word, search
  SteamGridDB by the game's folder name instead (for example `Games/Hollow Knight/game.exe`
  gives "Hollow Knight"), suggest "Rename to Hollow Knight?" on the match card, and write the
  new title on Apply. Keep the shortcut's stored ID so its artwork stays linked.
- **Sort the game list and cover wall**, for example A to Z, or games missing artwork first.

## UI polish

- Account and Steam folder menus are still plain Qt menus; restyle them like the other menus.

## Steam Deck

- Touch support: a way to open the right-click game menu (long press or a ⋯ button), the
  Clear pill on game page artwork (only shows on hover now), and previews of options
  (also hover only).

## Release

- Choose a licence (the metainfo file has a placeholder).
- Write the Flatpak manifest for Flathub.

## Later

- **TheGamesDB as a gap-filling artwork source** (box art, fanart and clear logos, cropped to
  Steam's shapes). Parked 2026-10-19: it needs a developer key, applied for at
  https://api.thegamesdb.net/key.php after signing up, which felt too much for a quick, simple app.

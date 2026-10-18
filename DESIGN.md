---
name: Steam Artwork for Non-Steam Games
description: Artwork for your non-Steam games, styled to sit next to the Steam client.
colors:
  ground: "#171a21"
  panel: "#1e2329"
  stage: "#1b2838"
  raised: "#2a3442"
  tray: "#202935"
  line: "#313b48"
  select: "#3d4c5e"
  text: "#dcdedf"
  text-2: "#a3adb8"
  text-3: "#8a95a2"
  blue: "#1a9fff"
  blue-ink: "#66c0f4"
  action: "#1474c8"
  action-hover: "#1a84dc"
  go: "#3f7e1d"
  go-hover: "#4b9123"
  warn: "#e3b341"
  warn-ground: "#3a3220"
  info-ground: "#1f3346"
  bad: "#ef7b6c"
typography:
  title:
    fontFamily: "Hanken Grotesk, Segoe UI, Noto Sans, sans-serif"
    fontSize: "1.625rem"
    fontWeight: 700
    lineHeight: 1.15
    letterSpacing: "-0.01em"
  heading:
    fontFamily: "Hanken Grotesk, Segoe UI, Noto Sans, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 700
    lineHeight: 1.3
  body:
    fontFamily: "Hanken Grotesk, Segoe UI, Noto Sans, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.4
  control:
    fontFamily: "Hanken Grotesk, Segoe UI, Noto Sans, sans-serif"
    fontSize: "0.8125rem"
    fontWeight: 600
    lineHeight: 1.2
  label:
    fontFamily: "Hanken Grotesk, Segoe UI, Noto Sans, sans-serif"
    fontSize: "0.6875rem"
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: "0.08em"
rounded:
  sm: "2px"
  md: "3px"
  lg: "4px"
  dialog: "6px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.go}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 18px"
    height: "40px"
  button-primary-hover:
    backgroundColor: "{colors.go-hover}"
  button-confirm:
    backgroundColor: "{colors.action}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "40px"
  button-secondary:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "40px"
  game-row-selected:
    backgroundColor: "{colors.select}"
    textColor: "#ffffff"
    rounded: "{rounded.md}"
    height: "48px"
  search-input:
    backgroundColor: "#14181e"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    height: "40px"
  tag-check:
    backgroundColor: "{colors.warn-ground}"
    textColor: "{colors.warn}"
    rounded: "{rounded.sm}"
    typography: "{typography.label}"
---

# Design System: Steam Artwork for Non-Steam Games

## Overview

**Creative North Star: "The library page that edits itself."**

The app borrows the Steam desktop client's visual language (the user asked for it) so it reads as a companion to Steam rather than another tool with its own look. Deep blue-grey surfaces, pale grey-blue text, Steam blue for selection and focus, and one green button. The start page is a cover wall like Steam's library home, with blank covers drawn as dashed tiles so the gaps are what you see first. A game page lists the five pieces as labelled rows: what the piece is, where Steam shows it, its size, the art in use, and a strip of its SteamGridDB options right beside it. Clicking an option uses it (with Undo); hovering previews it in place.

It is a working tool used occasionally, so precision beats expression: standard controls, a calm ground, colour only where it means something. No Valve logo, wordmark or anything suggesting an official product.

**Key Characteristics:**
- Dark, single theme (Steam has no light mode).
- One filled primary action per context: green in the header, blue in the tray and dialogs.
- Every piece named and explained in plain words, with its alternatives visible next to it.
- State shown by shape and text as well as colour (filled vs hollow coverage dots, labelled tags).

Mockup: `design/mockup/index.html`. Flows and states: `design/architecture.md`.

## Colors

Restrained strategy: tinted blue-grey neutrals carry the window; blue and green each have one job.

### Primary
- **Steam Blue** `#1a9fff` (`blue`): selection outlines, focus rings, progress fill, "Previewing" chip. Never a text colour on dark (use `blue-ink` `#66c0f4` for links and quiet buttons).
- **Confirm Blue** `#1474c8` (`action`): filled confirm buttons ("Use this cover", dialog primary). Darker than Steam Blue so white text passes 4.5:1.

### Secondary
- **Play Green** `#3f7e1d` (`go`): the single header action ("Get artwork for N games") and the full-pane recovery actions. Darkened from Steam's brighter green for white-text contrast (4.98:1).

### Neutral
- `ground` `#171a21` window and header, `panel` `#1e2329` game list, `stage` `#1b2838` game pane, `tray` `#202935` option tray, `raised` `#2a3442` secondary buttons and hover, `select` `#3d4c5e` selected row and tab.
- Text: `text` `#dcdedf`, `text-2` `#a3adb8` secondary, `text-3` `#8a95a2` labels and captions (≥4.5:1 on panel, stage and tray).
- Status: `warn` on `warn-ground` (Steam running, low-confidence match), `info-ground` for the post-fetch summary, `bad` `#ef7b6c` for "Not found".

### Named Rules
**The Two Fills Rule.** Only green and confirm-blue buttons are filled with colour. Everything else is `raised` grey or a text button.

## Typography

One workhorse sans in the register of Steam's Motiva Sans: Hanken Grotesk in the mockup. In the Qt app, use the system UI font (Qt default) unless the project chooses to bundle Hanken Grotesk; sizes and weights carry over.

### Hierarchy
- **Title** 26px / 700: game name on the game page, "Your non-Steam games" on the start page.
- **Piece name** 18px / 700 (heading): Cover, Wide cover, Banner, Logo, Icon.
- **Heading** 18px / 700: tray and dialog titles.
- **Body** 15px / 400: notices, dialog copy.
- **Control** 13px / 600: buttons, game names in the list, tabs.
- **Label** 11px / 700, uppercase, 0.08em tracking: region labels (GAME PAGE, LIBRARY) and tags. Labels name regions; they never sit above a heading as a kicker.

Numbers that count things use tabular figures.

## Layout

- Window: header bar, optional notice strip, then a split: game list (300px, 260px under 1180px, with an "All games" row at the top that returns to the start page) and the main pane (start page or game page). The option tray is a 360px third column at ≥1180px and a right-side overlay below that.
- Steam Deck target is 1280×800: all three columns fit; the game pane scrolls.
- Under 760px (mockup only) everything stacks.
- Spacing scale 4 / 8 / 12 / 16 / 24. Region labels sit 8px above their content; regions are 18px apart.
- Minimum control height 40px (game rows 48px) for Deck touch.

## Elevation & Depth

Flat surfaces separated by tone, not shadow. Shadows appear only on things that float: menus and popovers (`0 10px 30px rgba(0,0,0,.45)`), the tray overlay, dialogs, and the toast.

## Shapes

Small radii throughout, as in Steam: 2px tags, 3px buttons, rows and artwork, 4px menus, 6px dialogs. Empty artwork slots are dashed 1.5px outlines with a plus and "Add cover" text.

## Components

### Buttons
- **Primary (green):** header "Get artwork for N games". Disappears when nothing is fillable, replaced by a plain check-mark note.
- **Confirm (blue):** "Use this …" in the tray, dialog primary. Disabled until a different option is picked.
- **Secondary (raised grey):** "Fill 3 missing", "Restart Steam", Cancel.
- **Quiet (blue-ink text):** "Not this game?", "Is this right?".

### Game list row
Icon (32px, or a muted initial), name, five coverage dots in fixed order (cover, wide cover, banner, logo, icon; filled = present, hollow = missing), then "3 of 5", "All artwork", or a tag (Check match, Not found, Getting).

### Start page
Title, a line saying what was found ("Found Steam for kestrel at ~/.local/share/Steam · 18 non-Steam games"), coverage counts per piece ("Covers 9 of 18"), then a wall of covers. A game with no cover shows as a dashed tile with its name. Clicking a tile opens that game.

### Artwork row (signature)
One row per piece, in fixed order. Left: the piece name, a Missing tag when empty, one line on where Steam shows it, and its size. The logo row adds "Show it on the banner". Middle: the art in use at its real shape, captioned "In use · by author"; clicking it opens the tray. Right: "10 options on SteamGridDB" with "See all 10", then a strip of six options at the piece's true aspect ratio, the one in use badged "In use". Click applies at once with an Undo toast; hover previews in the middle column. Under 760px the three columns stack.

### Option tray
Tabs for the five slots, style filter chips (All, Official, Alternate, Minimal), Animated checkbox (off by default), thumbnails in the slot's true aspect ratio with "In use" badge and author/style caption, then a confirm button and Remove. Esc closes.

### Notices and toast
Full-width strip under the header: amber for "Steam is running", blue for the post-fetch summary with Review and Restart Steam actions. Toasts are light on dark, bottom centre, with Undo for any artwork change.

### Qt mapping
QSplitter for the panes; QListView + delegate for game rows; a custom QWidget for the preview; QListView (IconMode) for the tray; QTabBar; QToolButton menu for More; QFrame strip for notices. Apply colours via a Qt stylesheet built from the tokens above.

## Do's and Don'ts

### Do:
- Do show every artwork piece at its real aspect ratio, named, with where Steam shows it.
- Do say exactly what a click affects ("Get artwork for 13 games", "Fill 3 missing").
- Do offer Undo after every artwork change, and back up `shortcuts.vdf` before every write.
- Do pair every status colour with a shape or word.

### Don't:
- Don't use the Steam or Valve logo, wordmark or name in a way that implies an official product.
- Don't show SteamGridDB's internal names (Grid, Hero) in the UI.
- Don't add a second filled colour button to any context.
- Don't put white text on Steam Blue `#1a9fff` (2.8:1); use Confirm Blue.
- Don't replace existing artwork in bulk; bulk only fills gaps.

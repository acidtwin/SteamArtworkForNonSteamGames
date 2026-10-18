# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

<!-- The shipped product is a native desktop app (Python + PySide6 / Qt Widgets). Impeccable has no desktop platform value; "web" is used for the HTML mockup stage. Design decisions must stay buildable with stock Qt Widgets. -->

## Users

People who play games through Steam on Linux (desktop PCs and Steam Deck desktop mode) and have added games Steam doesn't sell as "non-Steam games": emulators, Heroic/Lutris/Bottles installs, GOG or itch.io games, native binaries. Their library shows blank grey tiles beside real artwork. Libraries range from a handful of shortcuts to a few hundred. Windows users later.

Their job: make those games look like they belong in the library, once, without learning Steam's file layout, then forget the app until they add another game.

## Product Purpose

Scan the local Steam install for non-Steam shortcuts, match each to a SteamGridDB title, and fetch the five artwork pieces Steam uses (Cover, Wide cover, Banner, Logo, Icon), with a preview to swap any piece. Success: a first-time user goes from launch to a filled library with one decision (paste a SteamGridDB API key) and one click.

## Positioning

Zero configuration: no parser rules, no path hunting, no account. Reads `shortcuts.vdf` directly and auto-detects native and Flatpak Steam. Existing tools (Steam ROM Manager, SGDBoop, Decky plugins) are described by users as messy and annoying; this app's claim is that it is the simple one.

## Operating Context

- Used occasionally: on first install, then after adding new games.
- Steam is often running. Steam reads artwork at start-up and rewrites `shortcuts.vdf` (where icons live) on exit, so a restart is part of the workflow.
- Steam Deck desktop mode: 1280×800 screen, touch and trackpad. The user can't test on a Deck; the design must be checked at that size.
- Requires a free personal SteamGridDB API key.

## Capabilities and Constraints

- Steam detection, account selection, `shortcuts.vdf` read/write, SteamGridDB search, match and asset download, and artwork saving already exist in code (`steam_artwork_finder/`).
- Five artwork slots, named for users as: **Cover** (600×900), **Wide cover** (920×430), **Banner** (hero, 1920×620), **Logo**, **Icon**. SteamGridDB's "Grid / Wide Grid / Hero" names are not shown in the UI. (Confirmed by user.)
- `shortcuts.vdf` is backed up before every write.
- Bulk fetch only fills missing slots; existing artwork is replaced only by an explicit per-slot choice.
- Must be buildable with stock Qt Widgets; no embedded web view.
- Undecided: whether uncertain matches download automatically and get flagged, or wait for confirmation.

## Brand Commitments

Working name "Steam Artwork for Non-Steam Games" (`APP_NAME`). No logo or visual identity exists yet. Visual style should match the Steam desktop client so the app feels native next to it (confirmed by user). Not affiliated with Valve or SteamGridDB: borrow Steam's visual language (dark blue-grey surfaces, library layout), never its logo, wordmark or anything implying an official Valve product.

## Evidence on Hand

No user research, testimonials or usage data. The brief and the user's own frustration with existing tools are the only evidence. Do not fabricate claims, counts or quotes.

## Product Principles

1. Value before questions: show the user's own games before asking for anything.
2. Never surprise-write: nothing changes in Steam's files without a user action, and every change can be undone.
3. Show it as Steam will show it: judge artwork in context, not as isolated thumbnails.
4. Say what a click affects: counts and names, not percentages or vague verbs.

## Accessibility & Inclusion

Full keyboard operation; touch targets sized for Steam Deck; state never conveyed by colour alone; layout holds with system font scaling at 1280×800.

<!-- Intended distribution: public release on GitHub and Flathub (confirmed by user). First-run guidance must assume a stranger with no context. -->

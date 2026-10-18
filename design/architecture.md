# Steam Artwork for Non-Steam Games: experience architecture

Phase 1 of the Intent + Impeccable workflow. This file says what the app must achieve and how a person moves through it. It deliberately says nothing about colour, type or visual style; that belongs to DESIGN.md (Impeccable).

Status: draft, written from the project brief and the current Qt code (`steam_artwork_finder/gui/app.py`). Items marked **Assumption** have not been confirmed by the user.

---

## 1. Context

**Users.** People who play games on Linux through Steam but launch some of them from outside Steam: emulators, Heroic/Lutris/Bottles installs, itch.io or GOG games, native binaries. They have added these as "non-Steam games", so their library shows blank grey tiles next to real artwork. Library size ranges from a handful of shortcuts to a few hundred (emulator users).

**Their goal.** "Make my non-Steam games look like they belong in my library." Not "manage artwork". They want it done once, correctly, and then forget the app exists until they add another game.

**How they solve it today.** Steam ROM Manager (powerful but needs parser configuration and exposes Steam internals), SGDBoop (per-game, browser driven), Decky plugins (Steam Deck gaming mode only), or copying files into `userdata/<id>/config/grid` by hand. The common complaints are configuration before value, unclear file naming, and fear of breaking `shortcuts.vdf`.

**Constraints.**
- Desktop app, Python + PySide6 (Qt Widgets). Linux first (native and Flatpak Steam), Windows later.
- Artwork comes from SteamGridDB, which needs a free personal API key.
- Steam reads artwork at start-up. Icons live in `shortcuts.vdf`, which Steam rewrites on exit, so icon changes while Steam is running can be lost.
- **Assumption:** some users run this on a Steam Deck in desktop mode (1280×800, touch plus trackpad), so the main window must work at that size.

**Ethical stance (defaults to maximum user protection).**
- Nothing is written to Steam's files without an action the user took.
- Bulk actions only fill gaps. Existing artwork is never replaced unless the user picks a replacement for that slot.
- `shortcuts.vdf` is backed up before every write, and the user can restore it from inside the app.
- No telemetry, no account, no network calls except to SteamGridDB. The API key stays on this machine.
- Rejected outright: every pattern in the Intent anti-pattern catalog. In particular no nagging for donations or ratings, no artificial "profile completeness" pressure (the coverage count is informational, not a score).

**Success.** A first-time user goes from launch to "all my games have artwork" in under two minutes with one decision (paste API key) and one click (fetch). A returning user can fix one wrong image in three clicks.

---

## 2. Jobs, in priority order

| # | Job | Frequency | Design consequence |
|---|-----|-----------|--------------------|
| 1 | Fill every blank tile at once | First run, then after adding games | The library-level primary action. Always visible, labelled with the count it will affect. |
| 2 | Fix a game that matched the wrong title | Occasional | Match is shown next to the game name with a one-click "Not this game?" |
| 3 | Swap one image I don't like | Occasional | Every artwork slot is directly clickable; options open in place, not in a separate window. |
| 4 | See what it will look like in Steam | Every time art changes | The detail pane mimics Steam's own library layout (hero with logo, capsule, wide capsule, icon), not a grid of equal boxes. |
| 5 | Undo a mistake / recover | Rare | Per-slot "revert" and a library-level "Restore backup". |

Jobs 1 to 3 are where people spend their time. 4 is what makes 3 confident. 5 is what makes 1 safe to click.

---

## 3. Information architecture

One window, no tabs, no wizard after first run.

```
Window
├── Header bar
│   ├── Steam account (shown as name + avatar; a switcher only if there are several)
│   ├── Library summary: "38 games · 9 need artwork"
│   ├── Primary action: "Get artwork for 9 games"
│   └── Overflow menu: API key, Restore backup, Open grid folder, About
├── Notice strip (only when relevant): Steam is running / key missing / offline
├── Library pane (left)
│   ├── Search box
│   ├── Filter: All · Needs artwork · Check match
│   └── Game rows: icon, name, coverage dots (5), match state
└── Game pane (right)
    ├── Title row: game name, "Matched to: <SGDB title, year> · Not this game?"
    ├── Steam preview (all five slots in their real proportions, each clickable)
    └── Option tray (appears under the preview when a slot is chosen)
        ├── Slot tabs: Grid · Wide · Hero · Logo · Icon
        ├── Filters: style (official, alternate, minimal…), animated off by default
        ├── Thumbnails in the slot's aspect ratio, current one marked
        └── Actions: Use this · Revert to previous · Close
```

**Terminology.** Use Steam's own words where users know them, and plain words where they don't.

| Internal / SGDB term | Shown to the user |
|---|---|
| grid (600×900) | Cover |
| grid (920×430) | Wide cover |
| hero | Banner |
| logo | Logo |
| icon | Icon |
| shortcut | Game |
| SGDB game id / match | "Matched to …" |
| `shortcuts.vdf` | "Steam's shortcut list" (file name only in the backup dialog) |

**Assumption:** "Cover / Wide cover / Banner" reads better than SteamGridDB's "Grid / Wide Grid / Hero" for people who never used SGDB. To confirm with the user, since the original brief used the SGDB names.

---

## 4. Flows

### 4.1 First run

1. **Find Steam.** Silent. Check native (`~/.local/share/Steam`, `~/.steam/steam`) and Flatpak (`~/.var/app/com.valvesoftware.Steam/...`). If several installs or accounts exist, pick the most recently used and show the switcher. Only if nothing is found does the user see a screen, offering "Choose Steam folder…".
2. **Show the library immediately**, with current coverage. The user sees value (their games, recognised) before being asked for anything.
3. **Primary action asks for the key only when needed.** Clicking "Get artwork for 9 games" with no key opens a short sheet: why a key is needed, a button that opens the SteamGridDB API page, a paste field that validates as you paste. Then the fetch continues without a second click.
4. **Fetch.** Progress in the header ("Getting artwork… 4 of 9"), tiles fill in live in the library list and preview. Cancel is always available and keeps what's finished.
5. **Finish summary** in the notice strip: "9 games updated. 2 need a look." The "Check match" filter is selected automatically if anything is uncertain.
6. **Apply in Steam.** If Steam is running: "Restart Steam to see the new artwork" with a "Restart Steam" button (only if we can do it safely; otherwise the instruction).

### 4.2 Fix a wrong match

Title row → "Not this game?" → search popover pre-filled with the shortcut name, results with year, platform and a small cover → choose → existing artwork from the old match is kept until the user fetches again; the app offers "Replace artwork from the old match" as one explicit button.

### 4.3 Swap one image

Click a slot in the preview → option tray opens under the preview on that slot's tab → hovering or arrow-keying a thumbnail previews it live in the Steam preview above → "Use this" (or double-click / Enter) saves it → toast with Undo. The tray stays open so the user can go to the next slot.

Why in place instead of the current modal picker: the user judges art by how it sits next to the other four pieces (does the logo read on this banner?), and a modal hides that context.

### 4.4 Restore

Overflow → "Restore backup" → list of dated backups of Steam's shortcut list with game counts → restore → confirmation that names what changes. Artwork files are not deleted by a restore.

---

## 5. Screen layout logic

**Game pane priority (top to bottom = order of importance):**
1. Which game and which SteamGridDB title it matched. A wrong match makes everything below wrong, so it comes first.
2. The Steam preview. This is the product; it gets the most space.
3. The option tray, only when invoked.

**Steam preview composition.** Mirrors Steam's library page so users recognise it:
- Banner across the top with the Logo placed over it (bottom-left, as Steam does).
- Below: Cover (portrait) on the left, Wide cover and Icon to its right.
- Empty slots show a dashed outline with "Add cover" etc., never a grey void.
- Each slot shows its state on hover/focus: source (SteamGridDB author), size, and "Change".

**Library row.** Icon (or placeholder initial), name, five coverage dots in a fixed order (cover, wide, banner, logo, icon). A row with an uncertain match gets a "Check" tag. Rows are the only list; no card grid of games, because names need to be scannable and lists of 200 must stay fast.

**Primary action placement.** Header, right side, the only filled button in the window when idle. It disappears (replaced by "All games have artwork") when coverage is complete, so it never asks for a no-op.

**Qt realism.** Every region maps to stock Qt Widgets: `QSplitter` (panes), `QListView` + custom delegate (library rows), a custom `QWidget` composing `QLabel`s/`QPainter` (Steam preview), `QListView` in IconMode (option tray), `QToolButton` menus (overflow), a frameless notice `QFrame`. No web views needed.

---

## 6. States (fortify)

| Condition | What the user sees | Recovery |
|---|---|---|
| Steam not found | Full-pane message | "Choose Steam folder…" |
| Native and Flatpak both present / several accounts | Account switcher in header | Remember last choice |
| No non-Steam games | Pane explains how to add one in Steam | "Check again" |
| No API key | Library works; fetch opens key sheet | Paste key |
| Key rejected | Inline in key sheet | Link to key page |
| Offline / SGDB down | Notice strip; existing art still shown | "Try again" |
| Rate limited | Fetch pauses with countdown | Resumes automatically |
| No match on SGDB | Row tagged "Not found" | "Search by another name" |
| Slot has no options | Tray shows "No banners for this game on SteamGridDB yet" | Link to upload page on SGDB |
| Steam running | Notice strip; icon changes queued with explanation | "Restart Steam" or close Steam first |
| Write fails (permissions, disk) | Toast with the real reason | Retry; nothing half-written (write to temp, then rename) |
| `shortcuts.vdf` unreadable/corrupt | Blocking message | Restore backup |
| Very long names / 300+ games | Elide names with tooltip; list virtualised | Search |

---

## 7. Cognitive load rules

- One primary action per context: library header (fetch all), tray (use this). Everything else is secondary or in the overflow.
- Never ask a question the app can answer itself (Steam path, account, which image to start with).
- Show counts, not percentages ("9 need artwork"), so the user knows exactly what a click affects.
- Defaults are the SteamGridDB top-voted static image; animated and NSFW are off unless the user turns them on.
- Every write is reversible from where it happened (toast Undo, per-slot Revert) or from the overflow (Restore backup).

---

## 8. Accessibility

- Full keyboard path: library (arrows) → Tab to preview slots → Enter opens tray → arrows through options → Enter applies → Esc closes.
- Coverage dots are paired with text for screen readers ("3 of 5 artwork") and never rely on colour alone.
- Focus is visible on every slot and thumbnail. Hit targets at least 32px for Steam Deck touch.
- Respect system font scaling; layout must hold at 125% text on 1280×800.

---

## 9. Open questions for the user

1. Steam Deck desktop mode: a real target, or desktop PCs only?
2. Words: "Cover / Wide cover / Banner" or keep SteamGridDB's "Grid / Wide Grid / Hero"?
3. After a bulk fetch, should uncertain matches be downloaded and flagged (current plan), or held back until the user confirms them?

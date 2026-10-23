# QDVC Tadaima for macOS

A native SwiftUI photo browser for macOS 14 (Sonoma) and later: the macOS
edition of QDVC Tadaima, in the same repository as the
[GTK 4 edition](../qdvc-tadaima-gtk4). It shows the photos in the folders you
choose as one continuous stream, a heading per folder, with a folder sidebar
that follows along as you scroll. It **never changes anything in those
folders**: it only reads them, and keeps its thumbnails and index in its own
cache.

- **One continuous stream.** Every photo under the folder you're focused on
  (all of them, unfocused) in one scrolling grid, in the sidebar's order. Each
  folder's photos sit under a heading with its name and, dimmed beside it,
  where it lives ("boats  holiday-photos-copenhagen / nyhavn").
- **A sidebar that follows along.** As you scroll, the sidebar highlights the
  folder you're looking at; if it is hidden in a collapsed folder, its nearest
  visible parent is highlighted instead, and scrolling never opens or closes
  folders. Click a folder (or move to it with the arrow keys) to jump to its
  photos. → and ← open and close the highlighted folder. Folders with photos
  of their own show a crop of their first photo as their icon, and every
  folder shows how many photos it holds, all the way down.
- **Focus on Folder** (right-click a folder) makes it the top of the sidebar
  and the stream, with the folders above it listed flat; click one of those,
  or press ⌘↑ (Focus on Enclosing Folder) or ⇧⌘↑ (Show All Folders), to step
  back out.
- **Photos at their own proportions**, with a soft shadow, at the size you
  pick with the toolbar slider or ⌘+, ⌘− and ⌘0. Thumbnails load in the
  background, what's on screen first; until one arrives, a faint tile of the
  right size holds its place.
- **Keyboard and mouse as in Finder and Photos.** Click selects; the arrow
  keys move the selection through the stream (↑/↓ cross into the next
  folder), Page Up/Down a screen at a time, Home/End to the first and last
  photo. Double-click, Return or ⌘↓ opens a photo; Space or ⌘Y shows it in
  Quick Look. Drag a photo out to Finder or Mail; ⌥⌘C copies it.
- **Viewer.** The photo fitted to the window, with its name, date taken,
  size and place in its folder beneath, and a filmstrip of the folder along
  the bottom. ← and → step through the folder; Esc (or Back) returns to the
  stream exactly where you left it.
- **Info inspector (⌘I).** Name, date taken, dimensions, file size and
  folder, plus the camera details the photo carries: camera, lens, focal
  length (with its 35 mm equivalent), aperture, shutter speed, ISO, exposure
  compensation, flash and location, with a **Show in Maps** button. Nothing
  about a photo leaves your Mac unless you press that button.
- **Adding folders:** the + at the foot of the sidebar, File › Add Folder… (⌘O),
  Settings › Folders, or drag folders from Finder onto the window or the Dock
  icon. A folder inside one already listed is already covered; a folder that
  holds listed ones replaces them.
- **Background indexing.** At launch (and with File › Scan for Changes, ⌘R)
  Tadaima looks for new, changed and removed photos and makes the missing
  thumbnails, four at a time; photos appear as they are ready. Progress shows
  under the window title. It remembers the folder you were looking at, your
  focus and which folders are open.

Formats: JPEG, PNG and HEIC (read by macOS itself, so nothing extra is
needed).

## Requirements

- macOS 14 Sonoma or later.
- Xcode 16 or later (free from the Mac App Store); Xcode 26 or later for the
  Liquid Glass look. Full Xcode is needed, not just the Command Line Tools:
  from Xcode 27, SwiftUI's `@State` is a macro whose compiler plugin
  (SwiftUIMacros) ships only with Xcode, so a Command Line Tools or swift.org
  toolchain fails with "plugin for module 'SwiftUIMacros' not found".
- No paid Apple Developer account.

## Build and run

From this folder:

```
scripts/build-app.sh            # "build/QDVC Tadaima.app", ad-hoc signed
scripts/build-app.sh --install  # …and copy it to ~/Applications
scripts/build-app.sh --universal  # arm64 + x86_64
```

or from the repository root, `scripts/build-app.sh --install`, which builds
this edition on a Mac.

`scripts/build-app.sh` builds with Xcode's toolchain even when
`xcode-select` points at the Command Line Tools: it picks the newest Xcode
installed, and says which Xcode and macOS SDK it is using (warning if the
SDK predates Liquid Glass). It also records that SDK in the app's binary,
which is what turns on Liquid Glass (`swift build` alone writes the
deployment target there, 14.0, so a `swift run` build keeps the classic
look). Check a build with
`vtool -show-build "build/QDVC Tadaima.app/Contents/MacOS/QDVCTadaima"`:
`sdk` should be 26.0 or later.

To run or test from the command line, point the tools at Xcode the same
way, once per Terminal session or for good:

```
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer
# or: sudo xcode-select --switch /Applications/Xcode.app
swift run QDVCTadaima           # build and launch (debug)
swift test                      # parity tests against the GTK edition
```

Or open the folder in Xcode and run the **QDVCTadaima** scheme.

The first time Tadaima reads a folder in Desktop, Documents, Downloads or on
an external disk, macOS asks whether it may; it only ever reads.

## Data and privacy

- Your photo folders are opened **read only**. Tadaima never writes to them.
- Preferences and remembered state (the scanned folders, which are open, the
  folder you were looking at, thumbnail size, appearance) are in the standard
  defaults domain: `defaults read org.qdvc.tadaima.mac`.
- Cache: `~/Library/Caches/org.qdvc.tadaima.mac/` — `index.json` and a
  `derivatives/` folder with one small thumbnail and one square crop per
  photo. Safe to delete at any time (or use Settings › Cache › Regenerate All
  Thumbnails); it is made again.
- No network access, except opening Maps when you press Show in Maps.

## Documentation

- [docs/DESIGN.md](docs/DESIGN.md): the design and the decisions taken.
- [docs/MAINTENANCE.md](docs/MAINTENANCE.md): code layout, behaviour shared
  with the GTK edition, tests.

## License

No license has been chosen yet. Add a `LICENSE` file before publishing.

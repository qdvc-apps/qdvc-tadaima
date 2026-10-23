# QDVC Tadaima for macOS — Maintenance Guide

This guide is for whoever maintains the app next. It explains how the code is
laid out, which behaviour is shared with the
[GTK 4 edition](../../qdvc-tadaima-gtk4) and must not drift, and how that is
tested.

The rule that matters most: **the photo folders are read only.** Nothing in
the app may write into a scanned folder. Thumbnails, crops and the index go
in the cache (`~/Library/Caches/org.qdvc.tadaima.mac/`); preferences in the
defaults domain `org.qdvc.tadaima.mac`.

## 1. Layout

```
Package.swift                 SwiftPM manifest (no Xcode project)
Sources/TadaimaCore/          the model: a port of the GTK edition's pure modules (Foundation only)
Sources/QDVCTadaima/          the SwiftUI/AppKit app
Tests/TadaimaCoreTests/       parity + unit tests; Fixtures/parity.json
tools/make_fixtures.py        regenerates the parity fixture from ../qdvc-tadaima-gtk4/qdvc
tools/make_icon.py            regenerates Resources/AppIcon.{svg,icns}
scripts/build-app.sh          builds and ad-hoc signs "QDVC Tadaima.app"
Resources/Info.plist          bundle id org.qdvc.tadaima.mac; folders as documents (Dock drops)
docs/                         DESIGN, this guide
```

`TadaimaCore` has no AppKit or SwiftUI imports and no dependencies, and builds
and tests on Linux; the app target is declared on macOS only (see
`Package.swift`).

### 1.0 Toolchain

Build with full Xcode. From Xcode 27, SwiftUI's `@State` is a macro whose
implementation (the SwiftUIMacros compiler plugin) ships with Xcode, so the
Command Line Tools or a swift.org toolchain fail with "plugin for module
'SwiftUIMacros' not found". `scripts/build-app.sh` therefore sets
`DEVELOPER_DIR` to the newest installed Xcode (found with Spotlight and in
`/Applications`) when `xcode-select` points at the Command Line Tools,
runs `xcrun swift` so that Xcode's compiler wins over any other on `PATH`,
prints the compiler and macOS SDK versions, and warns when the SDK is older
than 26 (no Liquid Glass). An explicit `DEVELOPER_DIR` is respected. The
same block is used in the other QDVC macOS apps (it was taken from
qdvc-progress-mac, unchanged but for the names); keep them in step.

AppKit decides on Liquid Glass from the SDK version recorded in the
executable's `LC_BUILD_VERSION`. `swift build` records the deployment target
there (`sdk 14.0`), so after copying the executable into the bundle the
script rewrites that field with `vtool -set-build-version macos <minos>
<sdk> -replace` to the SDK it built with, before signing. Check with
`vtool -show-build`.

The repository root's `scripts/build-app.sh` runs this script on a Mac (and
the GTK edition's elsewhere), passing every option through.

### 1.1 App icon

`tools/make_icon.py` writes `Resources/AppIcon.svg` and `Resources/AppIcon.icns`
(needs `rsvg-convert`; `brew install librsvg`). It is the GTK edition's icon
in the macOS family's style, as qdvc-progress-mac mirrors its GTK sibling: a
white photo print filling the whole tile (Apple's template geometry) with the
same landscape — sky, sun and halo, a far and a near hill — and the glossy
orange badge with a white house, placed as in the GTK icon. The GTK icon's
second, tilted print is left out, since here the print is the tile. Keep the
two icons in step. `--preview` also writes `build/icon-preview.png`.

## 2. Modules

`TadaimaCore`:

| File | Responsibility | Python origin (`qdvc-tadaima-gtk4/qdvc/`) |
| --- | --- | --- |
| `Paths.swift` | `normpath`, `dirname`, `basename`, `isWithin`, `tilde`, Python's `sorted(key=str.lower)` order, stable sorting | `os.path`, `tree.is_within`, `stream._tilde` |
| `Models.swift` | `ImageRecord`, `FolderNode` | `models.py` |
| `Tree.swift` | the filtered hierarchy and counts, find, ancestor chain, reveal chain, forget subtree | `tree.py` |
| `Stream.swift` | sections and headings, flat indexing, `StreamLayout` (geometry, hit-testing, visible ranges, keyboard moves, anchors), row ranges | `stream.py` |
| `Scanning.swift` | scanned-folder absorption, content sniffing, the folder walk | `config.normalize_scanned_folders`, `cache.looks_like_image`, `cache.scan_images` |
| `Index.swift` | cache keys (SHA-1 of the path), derivative names, the index (JSON) and its sync | `naming.py`, `cache.Index`, `cache.sync_index` |
| `CameraDetails.swift` | `ExifValues`, the camera-detail rows and their formatters, EXIF dates; `Formats` (counts, sizes, `fitWithin`, dimensions) | `metadata.camera_details` and formatters, `ui_prefs.format_count`, `ui_prefs.fit_within` |
| `Sidebar.swift` | the sidebar's visible rows for a focus and expansion, and the scrollspy's nearest visible row | the GTK window's tree-list logic (`_highlight_in_sidebar`) |

The app (`QDVCTadaima`): `TadaimaApp` (scenes, the app delegate: Dock and
Open With folder drops), `AppModel` (all state and actions, `@Observable`:
folders, tree, stream, sidebar rows and highlight, scrollspy, focus,
selection, viewer, scanning), `ContentView` (window, toolbar, inspector,
Quick Look, drops, alerts, empty states), `SidebarView`, `StreamView` (grid,
headings, cells, keyboard, photo context menu), `ViewerView` (with the
filmstrip), `InspectorView`, `Commands`, `SettingsView`, `Imaging` (the cache
location, ImageIO thumbnails and crops, the in-memory image cache,
`PhotoDetails` read from ImageIO properties), `Prefs`, `Platform`.

### 2.1 How the pieces talk

- **Scanning** (`AppModel.startScan`): the folder walk runs detached; the
  index is synced on the main actor; then `derivePending` makes thumbnails in
  a task group, four at a time, updating the index as each finishes. The
  status line updates at most five times a second; the tree and stream are
  rebuilt every 1.5 s (and at the end), so photos appear as they are ready;
  the index is saved every 500 photos and at the end. A photo ImageIO can't
  read stays pending (hidden) and is tried again by the next scan, as in the
  GTK edition. Scanning runs at launch, on ⌘R, when folders are added, and
  when the app becomes active if the last scan is more than ten minutes old.
- **Rebuilding** (`AppModel.rebuild`): tree from the index's derived photos,
  stream from the focused node; the stream is only replaced (and
  `streamGeneration` bumped) when its signature changes.
- **Rows:** `AppModel.streamRows(columns:)` caches the grid's rows per
  `streamGeneration` and column count (not observed, so building it in a view
  body is fine). Row ids are `h|<folder>` for headings and `p|<first photo>`
  for photo rows, so a heading keeps its id across column changes, and a
  photo row's id names a photo to re-anchor on (`anchorPhoto`, `rowID`).
- **Scrolling:** the model asks for scrolls with `requestScroll` (a new token
  each time); `StreamView` performs them with `ScrollViewReader` — headings to
  the top, photos into view as little as needed. A request made before the
  stream is on screen (the folder restored at launch) is performed when it
  appears.
- **Scrollspy:** `StreamView` passes its top row to
  `streamTopRowChanged`; `rehighlight` sets `highlightedRowID` to the visible
  row for the current folder. `goTo(folder:)` (user choices) sets
  `clickedFolder` and holds it for 0.8 s — counted again from when the
  stream actually performs the jump (`didScrollToFolder`), since at launch
  the request waits for the stream to appear — during which the stream's
  reports (of the jump) leave the highlight alone; the first report after
  that hands over to the scrollspy.
- **Images:** `ImageCache` is a non-isolated class, so its async methods run
  off the main thread; views ask from `.task(id:)`, which cancels when a cell
  scrolls away, and the cache checks for cancellation before decoding.

## 3. Behaviour that must be preserved

These are what the parity fixture checks; see `tools/make_fixtures.py` for
the cases.

- **Paths** as Python's `posixpath`: `normpath` (including a kept leading
  `//`), `dirname`, `basename`, `is_within` (a prefix only at a `/`), and `~`
  for the home folder in headings.
- **The tree:** only folders on the way to, or beneath, a scanned folder;
  every scanned folder present even when empty; recursive counts; children
  and photos sorted by lower-cased name in code-point order, ties kept in
  insertion order (Python's sort is stable; Swift's is not, hence
  `stableSorted`).
- **The stream:** pre-order sections for folders with photos of their own;
  a heading's context is the path from its scanned folder (" / "-joined), or
  for a scanned folder its parent directory with `~`; a folder with only
  subfolders jumps to its first descendant section.
- **Layout arithmetic:** columns `(width − 2·12 + 12) // (slot + 12)`, at
  least 1; headings 44, a 16 gap above each but the first; every geometry
  query, keyboard move and anchor as `StreamLayout` in `stream.py`.
- **Absorption:** a folder inside another listed one is dropped; order of
  first appearance kept.
- **What counts as a photo:** `.jpg/.jpeg/.png/.heic` (any case) whose first
  bytes are JPEG, PNG or a HEIF `ftyp` brand (`heic heix heif mif1 msf1 hevc
  hevx`); AppleDouble sidecars rejected.
- **Cache keys:** SHA-1 hex of the normalised path's UTF-8.
- **Camera details:** the selection, order and wording of the rows; halves
  round to even in shutter speeds and `fitWithin` (Python's `round`);
  exposure compensation omitted below 0.05 EV; flash omitted when the camera
  has none (`0x20`); location omitted at exactly 0, 0.

## 4. Deliberate differences from the GTK edition

See [DESIGN.md §5](DESIGN.md#5-decisions-and-differences-from-the-gtk-edition):
HEIC through ImageIO (no HEIF parser here), upright thumbnails and displayed
dimensions, no "screen" derivative, packages not scanned, settings applied
at once, the window subtitle instead of a status bar, appearance following
the system by default, no density setting or diagnostic switches. The cache
and its index have their own format (`PhotoIndex.version`), since each
edition keeps its own cache on its own computer.

## 5. Tests

```
swift test
```

On a Mac, run it with Xcode's toolchain (`DEVELOPER_DIR`, see §1.0), since
the package also contains the SwiftUI app target. On Linux, any Swift 5.10 or
later toolchain builds and tests `TadaimaCore` (the app target is left out).

`ParityTests` replays `Fixtures/parity.json`: paths, cache keys, extensions
and content sniffing, absorption, two libraries' trees, eight streams' sections
and headings with five layouts each (geometry, cells, hit-testing,
section lookup, visible ranges, keyboard moves from every sampled photo,
anchors re-applied at another width and slot), reveal and forget, formats,
and eight camera-detail cases. `CoreTests` covers the scanner on real files
(AppleDouble, wrong extensions, hidden folders, a folder symlink loop), the
index (sync, save and load, an older format), the sidebar's rows and
scrollspy rule, row ranges and EXIF dates.

To regenerate the fixture after a change in the GTK edition:

```
python3 tools/make_fixtures.py        # reads ../qdvc-tadaima-gtk4
```

Then run the tests; any difference is either a GTK change to port or a bug.
To add a case, add an input to the lists at the top of the script.

The app target itself has no tests: SwiftUI builds only on macOS. When
changing it on another system, it can at least be type-checked against
stand-ins for Apple's frameworks; this edition was first written that way,
and should still be built on a Mac before release.

## 6. Roadmap

- A CI workflow running `swift test` on Linux and macOS, and the GTK edition's
  tests, for the whole monorepo.
- Multiple selection in the grid (copy, drag, Quick Look of several photos).
- Sharing (the macOS share sheet) from the Photo menu.

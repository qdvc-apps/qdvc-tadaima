# QDVC Tadaima for macOS — Design

Status: **first implementation written.** This document records how the
macOS edition looks and behaves, and why.

It follows the approach of the other QDVC macOS ports (GTD EML, Nice Mail,
Bibliotheca): a pure-Swift port of the Linux edition's core with committed
parity fixtures, a SwiftUI/AppKit front-end, no Xcode project file, and an
ad-hoc-signed bundle built by `scripts/build-app.sh`. Unlike those, the Linux
edition lives in the same repository ([../qdvc-tadaima-gtk4](../../qdvc-tadaima-gtk4)),
so the parity fixture is generated straight from the code next door.

---

## 1. Principles

- **Read only.** The photo folders are never written to. Everything derived
  from them — thumbnails, square crops, the index — lives in the app's cache
  folder, which can be deleted at any time.
- **Same app, native manners.** The stream, its headings, the scrollspy, the
  folder tree and its counts, Focus on Folder, absorption of nested scanned
  folders and the camera details behave as in the GTK edition (the shared
  rules are ported and fixture-tested). Where GNOME and macOS differ —
  menus, keyboard shortcuts, settings, Quick Look, the inspector — the Mac
  edition does what Finder and Photos do.
- **Fast however big the library.** Nothing is built for photos that are not
  near the screen, and the work of making thumbnails happens off the main
  thread, on-screen photos first.

## 2. Window layout

One window, like Photos: a `NavigationSplitView` with

1. a **source-list sidebar** of the scanned folders' hierarchy;
2. the **photo stream** in the detail column (or the viewer, over it); and
3. the **Info inspector** on the right (⌘I, View › Show Info, or the toolbar
   button), the macOS 14 `inspector` column.

The toolbar has the thumbnail-size slider (as in Photos), Show All Folders
while focused, and Info. While a photo is open, Back to Library and
Previous/Next replace the slider. The window subtitle names the current folder,
or says what indexing is doing ("Making thumbnails: 120 of 3,400").

### 2.1 Sidebar

A flat `List` built from `Sidebar.rows` (TadaimaCore): rows indented by depth,
with disclosure chevrons, rather than an `OutlineGroup`, because the
scrollspy has to know exactly which folders are visible, and an outline
group keeps its expansion to itself.

- Each row: a crop of the folder's first photo (or a folder symbol), the name,
  and the recursive photo count, right-aligned in secondary text.
- **Focused:** an "Enclosing Folders" section lists the folders above the
  focus, flat and in italics; clicking one focuses there, keeping the folder
  you are viewing on screen and the folders down to the old focus open.
- **Context menu:** Focus on Folder, Show in Finder, and for a scanned folder
  Stop Scanning This Folder… (confirmed; the folder itself is untouched).
- **Footer:** a + button (Add Folder…) and the photo count, or a spinner
  while indexing — like the + at the foot of Mail's or Finder's sidebars.
- **Keyboard:** ↑/↓ move between folders (and scroll the stream to each);
  → opens and ← closes the highlighted folder.

### 2.2 The scrollspy

The sidebar and the stream follow each other, as in the GTK edition:

- **Stream → sidebar.** The stream reports its top row
  (`scrollPosition(id:)`); its folder becomes current, and the sidebar
  highlights that folder's row, or its nearest visible parent's
  (`Sidebar.visibleRow`). Scrolling never expands or collapses anything.
- **Sidebar → stream.** Choosing a row scrolls that folder's heading to the
  top — or, for a folder with only subfolders, its first descendant's.
- **Highlighting is not navigating.** The sidebar's selection is bound
  through a `Binding` whose setter is only ever called by the user; the
  model changes the highlight directly. So the scrollspy moving the
  selection never scrolls the stream. Clicking the row that is already
  highlighted still jumps (a simultaneous tap gesture), since the selection
  does not change.
- **Holding a click.** After a jump the clicked folder stays highlighted until
  the user scrolls (the stream's reports during the jump are ignored), so a
  parent with no photos of its own, or a short folder at the end that cannot
  reach the top, stays highlighted.

### 2.3 Photo stream

`StreamView`: a `LazyVStack` of rows from `AppModel.streamRows(columns:)` — a
heading, then rows of as many photos as fit — inside a `ScrollView`. The
column count comes from `StreamLayout.columns` (the same arithmetic as the
GTK edition). Only rows near the visible area are built.

- **Headings:** the folder's name (title 3, semibold), its context in
  secondary text, and the photo count on the right; right-click for Focus on
  Folder and Show in Finder.
- **Cells:** each photo's thumbnail at its own proportions, centred in a
  square slot, with a soft shadow and an accent ring when selected, both on
  the photo's real edges. Until the image arrives, a faint tile of the final
  size (known from the index) holds its place.
- **Selection and keyboard:** click selects; ←/→ step through the whole
  stream; ↑/↓ move to the same column in the row above or below, crossing
  into the neighbouring folder (`StreamLayout.neighbour`); Page Up/Down,
  Home/End. Return, ⌘↓ or a double-click opens the viewer; Space or ⌘Y
  shows Quick Look (stepping through the photo's folder).
- **Keeping your place:** when the column count changes (resizing the window
  or the thumbnails), the photo at the top stays at the top; a scan adding
  photos keeps the top row in place (`scrollPosition(id:)`).
- **Dragging** a photo out puts its file on the drag pasteboard (Finder, Mail).

### 2.4 Viewer

Double-clicking a photo shows it in place of the stream, which stays in the
view hierarchy underneath (hidden), so it is exactly where you left it on
return. The photo is decoded at about the window's size (in 512-pixel
buckets, so resizing doesn't re-decode on every step), its neighbours are
decoded ahead, and the previous photo stays up until the next is ready, so
stepping never flashes. A caption shows name · date taken · dimensions ·
size · "3 of 40"; a filmstrip of the folder runs along the bottom (click to
jump). ← and → step through **the photo's folder** (as in the GTK edition);
Esc, ⌘[ or Back returns.

### 2.5 Inspector

A grouped form: **File** (name, date taken, dimensions, size, folder, Show in
Finder) and **Camera** (the rows `CameraDetails.rows` selects, or "No camera
details in this photo."). A geotagged photo gets **Show in Maps**, which opens
Apple Maps at the spot: the only time anything about a photo leaves the Mac,
and only on that click (no inline map, which would fetch tiles by itself).

## 3. Commands

| Menu | Command | Shortcut |
| --- | --- | --- |
| File | Add Folder… | ⌘O |
| File | Scan for Changes | ⌘R |
| View | Show / Hide Info | ⌘I |
| View | Zoom In / Zoom Out / Actual Size | ⌘+ / ⌘− / ⌘0 |
| View | Focus on Current Folder | — |
| View | Focus on Enclosing Folder | ⌘↑ |
| View | Show All Folders | ⇧⌘↑ |
| View | Show / Hide Sidebar | ⌃⌘S (system) |
| Photo | Open / Back to Library | ⌘↓ / ⌘[ (or Esc) |
| Photo | Quick Look | ⌘Y (or Space) |
| Photo | Show in Finder | ⌥⌘R |
| Photo | Open with Default App | — |
| Photo | Copy Photo | ⌥⌘C |

The arrow keys are not menu shortcuts: they act in whichever of the sidebar,
stream or viewer has the focus.

## 4. Settings (⌘,)

- **General:** Appearance (Match System, Light, Dark — photos often look best
  on dark) and the thumbnail size.
- **Folders:** the scanned folders as a list with + and − beneath, as in
  System Settings; removing one asks first. Changes apply at once.
- **Cache:** where it is, how many photos and how much space, Scan for
  Changes, and Regenerate All Thumbnails… (confirmed).

The Settings window closes with ⌘W or its close button, as in Apple's apps.

## 5. Decisions and differences from the GTK edition

- **The same rules,** ported and fixture-tested: the folder tree and its
  ordering, the stream's sections and their headings, the layout arithmetic
  and keyboard moves, scanned-folder absorption, which files count as photos
  (content sniffing, AppleDouble sidecars rejected), cache keys, and the
  camera-detail selection and formatting.
- **HEIC** is read by ImageIO, so it needs nothing extra (the GTK edition
  needs pillow-heif for thumbnails, and parses the HEIF container itself for
  metadata).
- **Thumbnails are upright:** ImageIO applies EXIF orientation; dimensions are
  shown as displayed (a portrait phone photo reads 3024 × 4032).
- **Cached derivatives:** a 500-pixel thumbnail and a 128-pixel square, but
  no 2000-pixel "screen" copy: the viewer decodes the original at the
  window's size, which ImageIO does quickly, and saves disk. When the grid is
  zoomed beyond the cached thumbnail on a Retina screen, cells decode the
  original instead, so they stay sharp.
- **Packages** (such as a Photos library) are not looked inside, as Finder
  shows them as single files.
- **Settings apply immediately** (the GTK edition stages folder changes behind
  a Save button, as GNOME dialogs do).
- **No status bar:** indexing progress is in the window subtitle and the
  sidebar footer.
- **Appearance** defaults to the system's, as Mac apps do (the GTK edition
  defaults to dark).
- **Not ported:** the sidebar density setting (macOS sidebars have one size)
  and the GTK edition's diagnostic environment switches.

## 6. Identity

Repository folder `qdvc-tadaima-mac` in the `qdvc-tadaima` monorepo, bundle
identifier `org.qdvc.tadaima.mac`, app name "QDVC Tadaima", category
Photography.

# QDVC Tadaima — Maintenance

## Design philosophy

Tadaima follows the QDVC common specification, with two deliberate deviations
recorded in the app brief:

1. **No GTK 3 front-end.** Only the GTK 4 / libadwaita front-end is
   implemented. The dispatcher and preferences still expose a backend selector
   for family parity, but `--gtk3` falls through to GTK 4.
2. **No plaintext workspace.** Tadaima has no business-data workspace. The
   *filesystem itself* is the data, and it is strictly read-only. The only
   persisted state is application preferences (config) and a disposable cache
   (index + thumbnails).

The rest of the shared philosophy holds:

- **Pure core vs. toolkit views.** Everything under `qdvc/` (except
  `qdvc/gtk4/`) imports no GTK and is unit-testable without a display. Modules
  that import `gi` live only in `qdvc/gtk4/` and are prefixed `gtk4_`.
- **Lazy where it counts.** Launch stays responsive: scanning and thumbnail
  generation run in a background thread; the UI reads a lightweight index. The
  index is disposable and regenerable, versioned by `INDEX_VERSION`.

## Runtime requirements

- Python 3.10+
- PyGObject + GTK 4 + libadwaita
- PyYAML (config)
- Pillow (thumbnails; guarded import with graceful fallback)
- pillow-heif (optional; HEIC support)

## Directory & file layout

```
qdvc-tadaima-gtk4/            (the GTK 4 edition, in the qdvc-tadaima monorepo)
    qdvc_tadaima.py            Thin dispatcher (backend select before GTK import).
    qdvc/                      PURE package — no GTK imports.
        __init__.py            APP_ID, APP_NAME, __version__, constants.
        config.py              YAML config wrapper (XDG), validated accessors.
        models.py              ImageRecord, FolderNode dataclasses.
        naming.py              cache_key / extension / derivative-name helpers.
        cache.py               Cache dir, Index (JSON), scan + thumbnail gen.
        tree.py                Build filtered hierarchy; focus/ancestor logic;
                               reveal/forget helpers for sidebar expansion.
        stream.py              The photo stream: sections + arithmetic layout.
        metadata.py            Date taken, dimensions, camera details (EXIF).
        heif.py                HEIF/HEIC size + EXIF from the container.
        ui_prefs.py            SHORTCUTS table, density specs, formatting.
        platform_utils.py      open/reveal helpers (read-only OS actions).
        gtk4/                  GTK 4 front-end (all gtk4_*).
            gtk4_app.py        Adw.Application; prgname, icon, accels, app.quit.
            gtk4_window.py     Main window: split view, sidebar, gallery, full.
            gtk4_actions.py    win.* Gio.SimpleActions.
            gtk4_stream.py     The photo stream widget (virtual canvas).
            gtk4_thumbs.py     Thumbnail decoding threads + texture cache.
            gtk4_preferences.py Adw.PreferencesWindow (live-apply density).
            gtk4_shortcuts.py  Shortcuts window from SHORTCUTS.
    data/
        qdvc.Tadaima.desktop.in    Menu entry; build-app.sh fills in @BINDIR@.
        icons/hicolor/             App icon + symbolic variant (a theme root).
    scripts/build-app.sh       Check deps, run tests, stage, install/uninstall.
    tools/make_icon.py         Generates the icons in data/icons.
    docs/MAINTENANCE.md
    tests/                     Display-free tests.
    README.md
```

## Data formats

- **Config**: YAML at `$XDG_CONFIG_HOME/qdvc-tadaima/config.yml`. Keys:
  `scanned_folders`, `window`, `sidebar_width`, `density`, `color_scheme`
  (`dark`/`light`/`auto`, default `dark`), `ui_backend`, `custom_icon`. Every
  read supplies a default (no schema migration needed).
- **Index**: JSON at `$XDG_CACHE_HOME/qdvc-tadaima/index.json`, carrying
  `INDEX_VERSION`. A version mismatch discards the index (it regenerates).
- **Derivatives**: under `$XDG_CACHE_HOME/qdvc-tadaima/derivatives/`, named
  `<sha1-of-path>.<kind>.<ext>`. Small derivatives (`thumb`, `square`) are
  low-quality JPEGs (`SMALL_JPEG_QUALITY`); the `screen` preview is a
  higher-quality JPEG (`SCREEN_JPEG_QUALITY`). A 64×64 `square` is generated for
  *every* image (sidebar icons + filmstrip). Thumbnail dimensions
  (`thumb_w`/`thumb_h`) are stored so the grid can size each widget to hug the
  image. `INDEX_VERSION` is 3. On a normal launch, `has_all_derivatives` gates
  regeneration so only images actually missing a derivative are (re)processed —
  never the whole library.

Writes (config, index) are atomic (temp file + `os.replace`). No writes ever
touch a scanned folder.

## Model / load pipeline

1. `config.scanned_folders` gives the user's explicitly added roots.
2. `cache.sync_index(index, roots)` walks those roots (read-only, no symlink
   loops), adds/updates/removes `ImageRecord`s by comparing mtime + size.
3. `cache.generate_derivatives(rec, want_square=…)` builds thumb/screen (and a
   square crop for the folder chosen to represent each directory) with Pillow.
4. `tree.build_tree(records, roots)` produces the filtered `FolderNode`
   hierarchy with recursive image counts and per-folder direct images.
5. The window renders the sidebar from the tree and the gallery from the
   selected node's `own_images`. Crucially, only **fully-derived** images are
   passed to `build_tree` (via `_displayable_records`, gated by
   `has_all_derivatives`): an image is absent from both the grid and every
   folder count until all of its derivatives (thumb, screen, square) exist. It
   is never shown from its original file. Counts therefore grow as the
   background scan completes each image, and settle correctly when it finishes.

## Focus behaviour

`tree.find_node` and `tree.ancestor_chain` express "Focus on folder" as pure
data. The window keeps `focus_path` (default `/` = unfocused) and, on
`set_focus_folder`, re-renders: ancestors flat + italic, the focused node bold,
and its subtree indented beneath. The photo stream shows exactly the focused
subtree; a drill-down focus starts it at the top, while re-focusing up via an
ancestor row keeps the folder you were viewing on screen.

## Sidebar tree (expand/collapse + focus)

The sidebar is a `Gtk.ListView` over a `Gtk.TreeListModel`. The root store holds
the flattened italic ancestor rows (leaves — their child-model callback returns
`None`) followed by the bold focused node; the focused node and every normal row
below it expose their children through `_create_child_model`, which is what
gives the native expander arrowheads. `FolderItem` (in `gtk4_items.py`) wraps a
pure `FolderNode` as a `GObject`.

"Focus on folder" sets `focus_path` and calls `_populate_sidebar`, which rebuilds
the root store, then auto-expands the focused row so the change is visible. It is
driven from two places, both traced to stdout with a `[tadaima]` prefix:
clicking a flattened ancestor row, and the right-click context menu. The context
menu is a plain `Gtk.Popover` of real `Gtk.Button`s whose `clicked` handlers call
`set_focus_folder` / `reveal_in_file_manager` **directly** — the earlier
`Gio.SimpleAction` indirection (dynamically added/removed per right-click) was
the focus bug and has been removed. The right-click gesture runs in the capture
phase so the ListView cannot swallow button-3 first. In the sidebar, the → key
expands the selected folder and ← collapses it (`_on_sidebar_key`).

## Live updates during a scan

`_scan_worker` runs off the main thread. It calls `_live_refresh(True)` via
`GLib.idle_add` right after `sync_index` (structure may have changed, so the
sidebar tree is rebuilt once and subfolders appear). Every subsequent mid-scan
refresh uses `_live_refresh(rebuild_tree=False)`, which refreshes the photo
stream in place (`set_stream(keep_position=True)`: new photos appear, the view
stays where it is, nothing happens if nothing changed) and does **not** rebuild
the tree store; the
end-of-scan refresh rebuilds it once more so folder counts are final. (Frequent
mid-scan rebuilds made the tree flicker shut and reopen under the user — the
"retreat" bug — and blocked digging into folders during a scan; every rebuild
restores the remembered expansion, see *Persistent sidebar state*.) Regeneration
is gated by `has_all_derivatives`, so a normal launch only (re)generates for
images actually missing a derivative — never the whole library.

- The gallery is the photo stream (see *Photo stream*). Each thumbnail's
  `Gtk.Picture` is sized to the image's *true* scaled dimensions (from
  `thumb_w`/`thumb_h`) and centred in its square cell. This is deliberate: a
  `CONTAIN` picture in a fixed box letterboxes the image, so a `box-shadow` on
  it would float off the photo's edges. Sizing the widget to hug the image puts
  the drop shadow and selection ring (`picture.tadaima-thumb` /
  `.tadaima-thumb-selected`) on the photo's real edges. Thumbnails are never
  cropped; portraits under-fill the cell.
- **Selection vs. open.** A single click only selects; double-click or `Enter`
  opens (`_on_photo_activated`), which builds `_full_images` from **that
  photo's folder** and shows the full-view page.
- **Zoom.** `Ctrl +` / `Ctrl -` / `Ctrl 0` (`on_zoom_in/out/reset`) change
  `self._thumb_zoom` in 30px steps (clamped 80–420, persisted as
  `config.thumb_zoom`); the default 150 gives roughly ten thumbnails across a
  1920px window. Extra accelerators (`equal`, numpad variants) are registered in
  `gtk4_app` so the shortcuts work regardless of keyboard layout.
- A filename bar for the currently-selected photo sits above the status bar in
  the main view (`gallery_caption`), mirroring the caption in the full viewer.
- The full view: a filmstrip (`Gtk.Box` in a horizontal scroller) of square
  derivatives plus prev/next header buttons and a `Gtk.EventControllerKey`
  (←/→) drive `_show_full_at(idx)`. The filename + position show in a caption
  bar *beneath* the picture; the window title is left untouched.
- Arrow-key photo navigation is intentionally *not* a global accelerator (it
  would hijack the sidebar/grid); it lives on the full page's key controller and
  is recorded in `SHORTCUTS` for the shortcuts window only.
- The status bar (`Adw.ToolbarView` bottom bar) is updated from the scan
  worker via `_post_status` → `GLib.idle_add`, naming each file as it is
  processed and refreshing on a short interval so it never looks stuck.

## Scanned folders: staged edits + absorption

The Scanned folders window edits a *pending* list (`_pending_folders`), not the
live config. **Save** commits it through `Config.set_scanned_folders`, which runs
`normalize_scanned_folders` (pure): dedupe, then drop any folder that lies within
another (absorption — a parent absorbs its children). A scan starts only for
coverage that genuinely expanded; pure removals/absorption trigger none. Because
derivatives are keyed by each image's absolute path, an absorbed child's cached
derivatives are reused unchanged. Cancel/Esc discard.

## Content sniffing

`scan_images` accepts a file only if the extension matches *and*
`looks_like_image` confirms the header bytes (JPEG/PNG/HEIF signatures),
explicitly rejecting AppleDouble `._name.jpg` sidecars.

## Appearance (colour scheme)

`Config.color_scheme` (`dark`/`light`/`auto`, default `dark`) maps onto
`Adw.StyleManager.set_color_scheme` in `gtk4_app.apply_color_scheme`.
Preferences applies it live.

## Photo metadata

`qdvc/metadata.py` (pure) reads a photo's display metadata: the date taken,
pixel dimensions, human-readable file size, and the camera details.

- **Date taken** is EXIF `DateTimeOriginal`, falling back to `DateTime`, then
  the file's mtime (labelled "(file date)"). Pillow's `getexif()` returns only
  the main IFD (IFD0), and cameras store `DateTimeOriginal` in the **Exif
  sub-IFD**, so it is read with `get_ifd(0x8769)`. (Before, only IFD0 was read,
  so real photos showed `DateTime`, which editors overwrite on save.)
- **Camera details** (`camera_details`) are a deliberate selection, as
  `(label, value)` rows, only those present: Camera (make and model, without
  "Canon Canon EOS R5" repetition), Lens, Focal length (with its 35 mm
  equivalent when different), Aperture, Shutter speed, ISO, Exposure
  compensation (omitted at 0), Flash (omitted when the camera has none) and
  Location (from the GPS IFD, with altitude). Left out on purpose: technical
  tags with little meaning to a viewer (metering and exposure modes, white
  balance, colour space, software, maker notes, serial numbers).
- **HEIC.** Pillow needs pillow-heif for HEIC, and even with it an open can
  fail. Whenever Pillow can't open a HEIF file (or finds no EXIF in it),
  `qdvc/heif.py` reads the container directly: the primary item (`pitm`)'s
  `ispe` property gives the size, swapped for a 90°/270° `irot` (decoders apply
  it, so this is the size as displayed); the item of type `Exif` located by
  `iloc` (in `mdat`, or in `idat` for construction method 1) gives the EXIF
  block, whose TIFF data is loaded into a Pillow `Image.Exif`. Previously such
  files showed "Unknown dimensions". `tests/test_metadata.py` builds HEIF files
  box by box (rotation, `idat`, 16-bit `ipma` indices, `iloc` v2, truncated and
  junk files) to cover this without pillow-heif.

`_metadata_line` formats the one-line summary shown in both the gallery
caption bar and the full-view caption; `_metadata_for` remembers the last
photo's metadata, as both it and the information sidebar ask on every
selection. The `win.photo-info` action (Ctrl+I; info button left of the primary
menu in both the gallery and the viewer) toggles a right-hand `Gtk.Revealer`
(`_build_info_panel`) that **wraps both pages** (a sibling of the page
`Gtk.Stack` in `_build_ui`), so it stays open or closed consistently across
the gallery and the viewer. Its open/closed state persists via
`config.info_open`.

## Menus & shortcuts

The primary menu (in both the gallery and viewer header bars) offers Scanned
folders, **Scan for changes**, Cache status, Preferences, Keyboard Shortcuts,
and About. "Regenerate all caches" is intentionally **not** in the menu — it
lives only inside the Cache status dialog. `win.scan-changes` (Ctrl+R) runs
`sync_index` (adds new/changed files; removes vanished ones and deletes their
derivatives) then generates any missing derivatives, never rebuilding
up-to-date ones. The shortcuts window is bound to Ctrl+? (several spellings, as
? is Shift+/ on most layouts).

## Persistent sidebar state

Expanded folders, the selected folder, and the focused folder persist between
sessions via `Config.expanded_folders` / `selected_folder` / `focus_folder`.
`self._expanded` is the single source of truth for expansion (updated from each
row's `notify::expanded`). Because rebuilding the tree store collapses every
row, `_populate_sidebar` schedules `_reapply_sidebar_state` after *every*
rebuild — including the live refreshes a background scan triggers — to re-expand
the remembered folders, re-select the remembered folder and scroll it into view.
A `_restoring_state` guard stops the programmatic re-expansion from rewriting
what it is restoring.

**Torn-down rows.** When the store is rebuilt (or a parent collapses), GTK
destroys the old `Gtk.TreeListRow`s, and each one notifies `expanded` = False.
On GTK 4.14 (checked against libgtk-4 directly) a destroyed row *still returns
its item*, so the old "ignore rows without an item" check let every rebuild
read as the user collapsing every visible folder: the startup scan's rebuild
emptied `_expanded` and saved that, which is why the app reopened with the
right photos but a fully collapsed sidebar. `_on_row_expanded_changed` now
ignores a collapse from a row that is no longer `is_expandable()` (a live row
the user collapses always still is), and `_populate_sidebar` swaps the store's
contents in one `splice` under a `_rebuilding_sidebar` guard.
`tests/test_sidebar_state.py` pins this down with rows that behave like GTK's.
A *real* collapse forgets the folders open inside it too (`forget_subtree`), so
they don't spring back open at the next rebuild.

**Startup.** `_restore_session_state` calls `_reapply_sidebar_state(reveal=True)`,
which also expands every folder between the focused row and the remembered
folder (`tree.reveal_chain`), so the last folder is shown and selected even
when its parents were not saved as expanded (such as a config written by the
bug above), and then scrolls the photo stream to that folder. The sidebar
scroll waits for the window's next `after-paint` (`_scroll_sidebar_to`): before
the list has been laid out GTK cannot scroll to a row.

**Startup ordering.** GTK lays the window out at a higher priority than idle
callbacks, so the stream can lay out — and its scrollspy report the *first*
folder — before `_restore_session_state` runs. Until it has run
(`_session_restored`), `_on_stream_section` ignores reports; otherwise the
first folder would overwrite the remembered one.

## Sidebar as scrollspy

The sidebar and the stream are kept in step both ways.

- **Stream → sidebar.** `PhotoStream` calls `_on_stream_section` whenever the
  folder at the top of the view changes. The window makes it `selected_folder`
  (saved 1.5 s after scrolling settles, `_schedule_persist`, not once per
  folder passed) and calls `_highlight_in_sidebar`, which selects that folder's
  row or, if it is hidden in a collapsed folder, its **nearest visible
  parent's** — never expanding or collapsing anything — and scrolls the
  sidebar to it. `_spy_target` remembers the real folder, so the highlight
  moves down to it when you expand its parent, and back up when you collapse
  (`_rehighlight` after every user expand/collapse, and after every store
  rebuild via `_reapply_sidebar_state`).
- **Sidebar → stream.** A row selected by the user (click, or arrow keys)
  goes through `_on_tree_selected` → `_go_to_folder` →
  `PhotoStream.scroll_to_folder`: the folder's heading (or, for a folder with
  only subfolders, its first descendant's) is scrolled to the top. A selection
  made by the scrollspy is not navigation: `_spy_syncing` (and
  `_restoring_state` during restores) make `_on_tree_selected` ignore it.
  Because the spy selects rows, clicking an already-highlighted row changes no
  selection; a capture-phase click gesture on each row (`_on_row_left_click`,
  which ignores clicks on the expander arrow) navigates in that case too.
- **Holding the clicked folder.** After a jump, the stream "holds" that folder
  as current until the user scrolls (`PhotoStream.holding`), and the window
  keeps the *clicked* row highlighted meanwhile (`_clicked_folder`). Without
  that, clicking a parent with no photos of its own would immediately
  highlight its first child, and a short folder at the very end — which the
  view can't bring to the top — would be reported as the one above it.

## Photo stream

The main area is one continuous, sectioned grid of every photo under the
focused folder. Two halves:

**`qdvc/stream.py` (pure).** `build_stream(focus)` walks the focused subtree
in the sidebar's order (pre-order: a folder's own photos, then its subfolders,
sorted by name) and makes a `Section` for each folder with photos of its own:
its path, name and a **context** — the parent path from the scanned folder it
belongs to (`holiday-photos-copenhagen / nyhavn` for `boats`), or, for a
scanned folder itself, the directory it is in (`~/Desktop`). `Stream` adds flat
indexing (photo *i* of the whole stream ↔ section, by path) and a
`signature()` that changes whenever anything shown changes.

`StreamLayout(stream, width, slot)` places it all on a canvas: per section a
heading (`HEADER_HEIGHT`), then rows of square `slot × slot` cells,
`SPACING` apart, as many columns as fit, with `SECTION_GAP` above every
heading but the first. All geometry is **arithmetic per section**: it stores
only each section's heading y, first-row y, row count and end y, so a relayout
costs O(folders) — 0.2 ms for 100,000 photos — and everything else is
computed: `cell(i)`, `photo_at(x, y)` (clicks), `photos_between(y0, y1)` and
`sections_between` (what to show), `section_at(y)` (the scrollspy),
`neighbour(i, move)` (keyboard; ↑/↓ cross into the neighbouring section at the
same column, clamped to a short last row), and `anchor_at(y)` /
`y_for_anchor` (keeping your place when the layout changes: anchored on the
photo row, or heading, at the top of the view).

**`gtk4/gtk4_stream.py`: a virtual canvas.** A `Gtk.Fixed` as tall as the
layout sits in a `Gtk.Viewport` in a `Gtk.ScrolledWindow`. Only photos and
headings within a screen above and below the view have widgets
(`_update_visible`, on every scroll; skipped when the visible row range hasn't
changed); they are recycled through pools, so scrolling 50,000 photos uses the
same ~100 widgets. A thumbnail is a `Gtk.Picture` sized to the photo's
proportions (from `thumb_w/thumb_h` in the index, via `ui_prefs.fit_within`)
and centred in its cell, so the shadow and selection ring
(`tadaima-thumb-selected`) sit on the photo's real edges; until its texture
arrives it shows the faint `tadaima-thumb-loading` tile at that same size.
Things checked against real libgtk-4 (4.14, under Xvfb):

- The horizontal policy is **EXTERNAL**, not NEVER. With NEVER, the canvas's
  widest tile becomes the window's minimum width, so it could never be
  shrunk — and the stream never relayout narrower. With EXTERNAL the minimum
  is 0 and the viewport's horizontal adjustment page size is the visible
  width, which is how the stream learns its width (`_on_hadj_changed`; it
  relayouts only when the column count changes).
- A canvas millions of pixels tall works. But a new height reaches the
  vertical adjustment only at the next allocation, and a `set_value` beyond
  the old height is **clamped**. So `_scroll_to_y` defers a scroll target the
  scrollbar can't reach yet (`_pending_y`) and applies it from the
  adjustment's `changed` signal.
- The viewport's `scroll-to-focus` is off: the canvas is one huge focusable
  widget, and scrolling "to" it would jump.

Selection, keyboard (`_on_key`: arrows, Page Up/Down, Home/End, Enter) and
clicks (`_on_pressed`: `photo_at` hit-testing, double-click opens) are handled
by the stream itself. The scrollspy's "current" folder is the one whose heading
is at or above `SPY_OFFSET` px below the top of the view, the last one when
scrolled to the very bottom, or the held folder after `scroll_to_folder`
(see *Sidebar as scrollspy*). Zooming (`set_slot`) keeps the selected photo at
the same height on screen if visible, else the top row; a scan refresh
(`set_stream(keep_position=True)`) keeps the top row in place even when
photos are added above it, and is a no-op when the signature is unchanged.

**Thumbnails** (`gtk4/gtk4_thumbs.py`). `ThumbDecoder` decodes cached
thumbnail JPEGs on two daemon threads with
`GdkPixbuf.Pixbuf.new_from_file_at_scale`, straight to display size
(gdk-pixbuf's loaders are thread-safe; PyGObject drops the GIL while they
run), and hands results back in batches from one idle callback that yields
after `FRAME_BUDGET_S` (8 ms) and returns False once empty (an idle that keeps
returning True spins a core; see *Viewer CPU*). Its queue is **replaced** on
every scroll (`set_queue`): photos on screen first, then those just off it, so
whatever you're looking at loads next and photos flicked past are never
decoded. Decodes in flight are not re-queued; `clear()` discards them.
Decoded textures go into `TextureCache`, an LRU **capped by bytes** (160 MB of
pixels) rather than by count, since a 420 px thumbnail is 7× a 150 px one.
Cache keys include the slot size; a failed decode isn't retried until the next
stream refresh.

**Before this design** the main area showed one folder at a time in a
`Gtk.FlowBox`: a FlowBox makes a widget per photo, which first froze the UI on
big folders (fixed by filling it progressively) and cannot scale to a whole
library; it was replaced by the canvas above.

## Info sidebar

The `win.photo-info` action toggles a right-hand `Gtk.Revealer`
(`_build_info_panel`) that slides in. It shows metadata only — name, date
taken, dimensions, file size, path (selectable) — and, in a **Camera** group,
the camera details from `metadata.camera_details` (rows built per photo by
`_set_camera_rows`; "No camera details in this photo." when there are none).
A geotagged photo's Location row has a button that opens the spot on
OpenStreetMap (`_open_map`, via `Gtk.UriLauncher`) — the only time Tadaima
sends anything about a photo off the machine, and only on that click. It
deliberately does **not** render the photo itself. `_update_info_panel`
refreshes it on selection change while open; a collapsed `SLIDE_LEFT` revealer
reserves no width, so the stream gets the full pane when the panel is closed.

## Full-view focus

Opening the viewer moves focus to the (focusable) picture via
`grab_focus`, so the "Back to Library" button is not auto-focused and the
←/→ key controller on the full page receives keystrokes. The back button is
`focus_on_click=False`.

## Shutdown

`do_close_request` persists window size and sidebar state, sets `_closing`
(after which the idle callbacks `_reapply_sidebar_state` and `_live_refresh`
no-op), and calls `app.quit()` so the process exits cleanly even if the daemon
scan thread or a stray GSource is still around. The detail area must actually
*receive* width, too: the detail `Gtk.Stack` and the detail widget in the
gallery's `detail_row` box are `hexpand=True`, the info revealer is
`hexpand=False`.

## Viewer CPU (spinning idle) — ROOT CAUSE & fix

> A longer, beginner-friendly writeup of this bug and the debugging story lives
> in `docs/2026-07-25-the-idle-callback-that-never-slept.md`.

Confirmed cause of the ~100%-of-one-core spin that started on opening a photo:
`_on_thumb_activated` scheduled `GLib.idle_add(self._pic_area.grab_focus)` to
move keyboard focus onto the image. `Gtk.Widget.grab_focus` **returns `True`**
on success, and `GLib.idle_add` *keeps re-running any callback that returns a
truthy value*. So the idle source re-armed on every main-loop iteration and
called `grab_focus` forever — a Python callback dispatched via
GObject-introspection/libffi straight from `g_main_context_iteration`. That is
exactly what the evidence showed: `QDVC_LOOP_DEBUG` reported `CPU 100% of one
core, 0 paints/sec` (a busy loop that draws nothing), and `py-spy dump --native`
showed the hot stack as `g_main_context_iteration → libffi → gi/_gi →
gi_callable_info_*` with no GTK render/layout frames at all.

Fix: wrap the focus call so the idle returns `False` and runs exactly once
(`_focus_pic_once`). **Rule for this codebase:** never pass a GTK method
directly to `GLib.idle_add`/`timeout_add`; many (like `grab_focus`,
`set_visible`) return truthy and will re-arm the source. Always use a wrapper
that returns `False`, or `GLib.SOURCE_REMOVE`.

Earlier theories in this file (stuck stack crossfade / revealer transition,
`Gtk.Picture` texture/layout loops) did *not* cause this and are retained only
as diagnostic history; the crossfade was still switched off because it feels
snappier, and the `_pic_area` wrapper / fixed caption footprint remain as sound
layout hygiene.

## Viewer CPU (layout-loop) — earlier theory (superseded)

Symptom: opening the photo viewer pushed CPU to ~13% and it stayed there.
`py-spy top` showed the main thread 100% inside `Gio.Application.run` with 0%
in any app Python — i.e. the time was in GTK/GSK C code, not our code. It
survived both `GSK_RENDERER=cairo` and `--no-filmstrip`, which ruled out the GL
driver and the filmstrip. That profile is the signature of a **layout
measure/allocate cycle that never settles**: a widget keeps renegotiating size,
so GTK schedules a new frame every tick.

The cause was the full-view image: a `Gtk.Picture` with `can_shrink=True` +
`CONTAIN` + `vexpand`, stacked in a vertical `Gtk.Box` directly beside the
variable-height caption label. The picture's height-for-width and the caption's
size could disagree indefinitely. Fix: the picture now lives in a dedicated
wrapper box (`_pic_area`, `hexpand`/`vexpand`, `overflow=HIDDEN`) that takes its
size from the window layout rather than the image's intrinsic size, and the
caption bar is `vexpand=False` with a fixed footprint. With a definite
allocation the layout settles in one pass and GTK stops requesting frames.
Focus for ←/→ navigation moved from the picture to `_pic_area` (a focus ring on
the picture could itself provoke redraws).

## Diagnosing idle CPU / I/O use

If the app uses noticeable CPU or disk while idle (no scan running):

- **Rendering?** Run `QDVC_FRAME_DEBUG=1 python3 qdvc_tadaima.py`. A passive
  frame-clock monitor logs `[tadaima][frames] N paints/sec (visible page: …)`
  **only when frames are actually drawn**. If lines appear only after you open
  a photo (visible page: full), the viewer is driving a continuous redraw.
  Cross-check with `GTK_DEBUG=interactive` → Visual → "Show Graphic Updates".

- **Python loop or C rendering?** Run `QDVC_STACK_DEBUG=1 python3
  qdvc_tadaima.py`. A background thread prints the main thread's Python stack
  every 0.5s. If the same app function (e.g. in `_show_full_at`,
  `_update_info_panel`, `read_metadata`) keeps appearing, that Python code is
  the culprit. If the stack is always parked in the GLib main loop
  (`…main_context_iteration`/`run`), the CPU is in GTK/GSK C code
  (rendering/layout), not our Python — pursue it with the frame monitor and the
  inspector instead.

- **Is it background I/O / the scan?** Run `QDVC_NO_BACKGROUND=1 python3
  qdvc_tadaima.py`. This skips all background scanning and derivative work, so
  the app sits fully idle after launch. Leave it untouched: if CPU is still
  high, the cost is *not* our background I/O or scan thread.

- **How much CPU, and is it rendering?** Run `QDVC_LOOP_DEBUG=1 python3
  qdvc_tadaima.py`. Once a second it prints `[tadaima][loop] CPU N% of one
  core, P paints/sec`. This uses a plain 1s timeout and `time.process_time()`,
  so (unlike an earlier idle-based probe) it does **not** itself spin the loop.
  Read it as: `CPU ~0%` idle = healthy; `CPU ~100% of one core` = a busy loop.
  If that busy loop also shows many `paints/sec`, it is a render loop; if
  `0 paints/sec`, it is a non-render busy loop (a spinning GSource or a tight
  callback). It also tags a stuck `stack`/`revealer` transition when present.

- **Name the C function.** The plain `py-spy dump`/`top` we used only unwinds
  Python and collapses everything into `Gio.Application.run`. To see the actual
  C culprit, use the native unwinder while the CPU is high:
  `sudo py-spy dump --native --pid <pid>` (or `py-spy top --native`). That names
  the GTK/GLib/GDK/driver function actually burning the core.

- **Isolate the gallery repainter.** A frame log of `N paints/sec (visible
  page: gallery)` with no interaction means a gallery widget is repainting.
  Binary-search it with these env switches (all opt-in): `QDVC_NO_SHADOW=1`
  drops the thumbnail drop-shadow CSS (tests shadow rasterisation);
  `QDVC_PLAIN_THUMBS=1` renders thumbnails as `Gtk.Image` instead of
  `Gtk.Picture` (tests the Picture/paintable path). If the repaints stop under
  one of these, that feature is the cause.

- **Isolate the filmstrip.** Run `python3 qdvc_tadaima.py --no-filmstrip`
  (sets `QDVC_NO_FILMSTRIP=1`). The photo viewer then omits the filmstrip
  entirely — prev/next navigation stays on a thin bar — so you can A/B whether
  the filmstrip is responsible for viewer CPU use. If CPU is normal with the
  flag and high without it, the filmstrip (its per-photo `Gtk.Picture`s /
  scroller) is the cause.

- **Full-size images use `Gdk.Texture`, not `set_filename`.** `_show_full_at`
  and the filmstrip load images via `Gdk.Texture.new_from_filename` (immutable
  GPU textures the renderer samples cheaply) rather than
  `Gtk.Picture.set_filename`. A pixbuf-backed paintable combined with
  `can_shrink` + `CONTAIN`/`COVER` scaling can make some GL drivers
  re-scale/re-upload the texture on every composite — a steady 10–15% CPU cost
  that persists because the (crossfade) `Gtk.Stack` keeps the viewer page
  realized. `on_back` additionally clears the full picture's paintable and the
  filmstrip so nothing large lingers.
- **Disk writes?** Run `QDVC_IO_DEBUG=1 python3 qdvc_tadaima.py`. Every
  `config.save()` logs a line. If these appear while you sit idle or merely
  drag the sidebar divider, config is being rewritten too often. `Config.set`
  now skips writes when the value is unchanged, and `_on_paned_moved` only
  persists a genuinely new sidebar width, so repeated identical notifications
  no longer hit disk.
- **Interpreting `btop`:** the process IO/R and IO/W columns are *cumulative
  totals since launch*, not live rates — a few hundred KiB after loading
  thumbnails and writing the index/config once is normal and does not indicate
  ongoing activity. Likewise a GTK4 app normally shows ~15–30 threads (GL/render
  worker pools, the GIO thread pool, GLib workers); the count alone is not a
  problem — most are parked. To see *live* activity use `py-spy dump --pid
  <pid>` (Python stacks) or `strace -f -p <pid> -e trace=read,write,openat`.

## The magic numbers

`THUMB_MAX_PX = 500`, `SCREEN_MAX_PX = 2000`, `SQUARE_PX = 32` live in
`qdvc/__init__.py`. Change them there; bump `INDEX_VERSION` if the change should
force regeneration.

## Common maintenance tasks — where to touch

- **Add a supported format**: `SUPPORTED_EXTENSIONS` in `qdvc/__init__.py`
  (and ensure Pillow can decode it).
- **Add a shortcut/command**: add to `ui_prefs.SHORTCUTS`, install a `win.*`
  action in `gtk4_actions.install_actions`, reference it from the menu/header in
  `gtk4_window`.
- **Change sidebar density**: `ui_prefs.DENSITY_SPECS` + `_apply_density`.
- **Change cache layout**: `qdvc/cache.py` (bump `INDEX_VERSION`).

## Parity with the macOS edition

The macOS edition (`../qdvc-tadaima-mac`) ports this edition's pure modules
(`paths` via `os.path`, `tree.py`, `stream.py`, `config.normalize_scanned_folders`,
`cache.looks_like_image`, `naming.cache_key`, `metadata.camera_details` and its
formatters, `ui_prefs.fit_within` / `format_count`) to Swift, and replays a
fixture generated from them. After changing any of those, run
`python3 ../qdvc-tadaima-mac/tools/make_fixtures.py` and port the change: the
Swift tests (`swift test` in that folder) list every difference.

## Testing without a display

- **Pure-model tests** (`tests/test_tree.py`, `tests/test_core.py`): build temp
  folders/images and assert on tree, config, index, and derivatives — no
  display.
- **Sidebar state** (`tests/test_sidebar_state.py`): runs the real
  `MainWindow` expansion/restore methods against stand-in tree rows that
  behave like GTK's (including torn-down rows).
- **Sidebar as scrollspy** (also `tests/test_sidebar_state.py`): the window's
  `_on_stream_section` / `_go_to_folder` / `_on_tree_selected` against those
  rows: nearest-visible-parent highlighting, the startup gate, held clicks,
  and that the spy's own selections don't navigate.
- **Photo stream** (`tests/test_stream.py`): the pure layout (geometry,
  hit-testing, keyboard neighbours, anchors, an O(folders) relayout budget),
  and the real `PhotoStream` against a stand-in toolkit — adjustments that
  clamp and signal like GTK's, and an `allocate()` step for GTK's next layout
  pass — covering recycling, the scrollspy, deferred scrolls, holding,
  resizing, scans adding photos above, keyboard, zoom and decoding. Also the
  real `ThumbDecoder` threads (queue replacement, discarding in-flight work)
  and the byte-capped `TextureCache`.
- **Metadata** (`tests/test_metadata.py`): HEIF files built box by box, read
  without pillow-heif; JPEG camera details and the date-taken fix; formatting.
- **Import smoke test** (`tests/test_import_smoke.py`): a permissive fake `gi`
  lets every `qdvc.gtk4.*` module import, exercising class bodies and top-level
  code.

Pre-ship check:

```sh
python3 -m py_compile qdvc/*.py qdvc/gtk4/*.py qdvc_tadaima.py
for t in tests/test_*.py; do python3 "$t"; done
```

`scripts/build-app.sh` runs the same tests and refuses to install if any fail.

## Deployment

The repository root's `scripts/build-app.sh` runs this edition's script on
Linux (and the macOS edition's on a Mac), passing every option through.

`scripts/build-app.sh --install` checks the dependencies, runs the tests,
stages a copy in `build/` and installs it per user: the program to
`~/.local/share/qdvc-tadaima/app/`, a launcher to `~/.local/bin/qdvc-tadaima`,
`qdvc.Tadaima.desktop` (from `data/qdvc.Tadaima.desktop.in`) and the icons into
`~/.local/share`. The installed copy is independent of the checkout. If the app
is running, the script quits it first through the `app.quit` action over D-Bus
(the window closes normally, so its state is saved) and starts it again after.
It also removes the hand-written `qdvc-tadaima.desktop` launcher that earlier
versions of the README described. `--uninstall` removes everything it installed
but never the config or cache. `set_prgname("qdvc-tadaima")` must match the
launcher's `StartupWMClass`.

## App icon

`tools/make_icon.py` writes `data/icons/hicolor/scalable/apps/qdvc.Tadaima.svg`
and its 16 × 16 symbolic variant; edit the script, not the SVGs. It follows the
QDVC family's GNOME icons (a light object with a darker bottom edge plus a round
badge, on the Adwaita palette): two photo prints, the front one a landscape,
with an orange badge holding a house (tadaima: "I'm home"). The app looks the
icon up by its app ID and, when running from a checkout, adds `data/icons` to
the icon theme's search path; if neither has it, it falls back to the stock
`emblem-photos`.

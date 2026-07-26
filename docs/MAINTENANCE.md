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
qdvc-tadaima/
    qdvc_tadaima.py            Thin dispatcher (backend select before GTK import).
    qdvc/                      PURE package — no GTK imports.
        __init__.py            APP_ID, APP_NAME, __version__, constants.
        config.py              YAML config wrapper (XDG), validated accessors.
        models.py              ImageRecord, FolderNode dataclasses.
        naming.py              cache_key / extension / derivative-name helpers.
        cache.py               Cache dir, Index (JSON), scan + thumbnail gen.
        tree.py                Build filtered hierarchy; focus/ancestor logic.
        ui_prefs.py            SHORTCUTS table, density specs, formatting.
        platform_utils.py      open/reveal helpers (read-only OS actions).
        gtk4/                  GTK 4 front-end (all gtk4_*).
            gtk4_app.py        Adw.Application; prgname, icon, accels.
            gtk4_window.py     Main window: split view, sidebar, gallery, full.
            gtk4_actions.py    win.* Gio.SimpleActions.
            gtk4_preferences.py Adw.PreferencesWindow (live-apply density).
            gtk4_shortcuts.py  Shortcuts window from SHORTCUTS.
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
and its subtree indented beneath.

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
refresh, and the end-of-scan refresh, use `_live_refresh(rebuild_tree=False)`,
which refreshes only the open folder's thumbnail grid and does **not** rebuild
the tree store — rebuilding it mid-scan collapsed whatever the user had expanded
(the "retreat" bug) and blocked digging into folders during a scan. Regeneration
is gated by `has_all_derivatives`, so a normal launch only (re)generates for
images actually missing a derivative — never the whole library.

- The gallery uses a `Gtk.FlowBox` (wrapping grid). It is **non-homogeneous**,
  `halign=FILL`, `hexpand=True`, inside a `ScrolledWindow` with
  `propagate_natural_width(False)`, so it uses the whole viewport width and
  packs as many columns as fit rather than clamping the column count.
- Each thumbnail's `Gtk.Picture` is sized to the image's *true* scaled
  dimensions (from `thumb_w`/`thumb_h`) and centered in a fixed square cell
  wrapper. This is deliberate: a `CONTAIN` picture in a fixed box letterboxes
  the image, so a `box-shadow` on it would float off the photo's edges. Sizing
  the widget to hug the image puts the drop shadow and selection border
  (`picture.tadaima-thumb` / `…:selected picture.tadaima-thumb`) on the photo's
  real edges. Thumbnails are never cropped; portraits under-fill the cell.
- **Selection vs. open.** The FlowBox uses `activate-on-single-click(False)`:
  a single click only selects; double-click activates. `_on_grid_key` opens the
  selected thumbnail on `Enter`. Activation builds `_full_images` and shows the
  full-view page.
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

`qdvc/metadata.py` (pure) reads a photo's display metadata: EXIF
`DateTimeOriginal` (falling back to `DateTime`, then filesystem mtime, the
fallback labelled "(file date)"), pixel dimensions, and human-readable file
size. `_metadata_line` formats the one-line summary shown in both the gallery
filename bar and the full-view caption. The `win.photo-info` action (Ctrl+I;
info button left of the primary menu in both the gallery and the viewer) toggles
a right-hand `Gtk.Revealer` (`_build_info_panel`) that **wraps both pages** (a
sibling of the page `Gtk.Stack` in `_build_ui`), so it stays open or closed
consistently across the gallery and the viewer and shows the selected photo's
metadata in either. Its open/closed state persists via `config.info_open`. It
shows metadata fields only — name, date taken, dimensions, file size, path
(selectable) — and deliberately does **not** render the photo. `_update_info_panel`
refreshes it on selection change and on viewer navigation while open.

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
the remembered folders and re-select the remembered folder. Without this, the
startup scan would wipe the just-restored expansion. A `_restoring_state` guard
stops the programmatic re-expansion from rewriting what it is restoring.

## Thumbnail grid — the slot model

Each image occupies a fixed-width **slot** (`SLOT_WIDTH`, default 150px, scaled
by the Ctrl +/-/0 zoom via `config.thumb_zoom`). The grid is a `Gtk.FlowBox`
with `homogeneous=False`; every `Gtk.FlowBoxChild` is a `slot_w × slot_w`
square, so all slots are identical — FlowBox packs `floor(pane / (slot_w +
spacing))` slots per row, rows and columns are regular, and the thumbnail is
centred within the square both horizontally and vertically. The slot caps
**both** the width and the height of a thumbnail at `slot_w`, so a very tall
image scales down to fit (becoming narrow) instead of producing a super-tall
row. Only fully-derived images reach the grid (see below), so the thumbnail is
always the cached derivative (`rec.thumb_path`) — there is no original-file
fallback.

A subtle but critical detail that caused repeated trouble: a `Gtk.Picture` fed a
*file* reports the full image's pixel dimensions as its natural size, and GTK
lays FlowBox out on that — producing one giant column, per-folder-varying slot
widths, and a runaway measure/allocate cycle that pegs a CPU core. The fix is to
load the thumbnail derivative into a `GdkPixbuf`, scale it **once** to fit the
slot (`scale_simple`, width and height both ≤ `slot_w`, never upscaled beyond
1.0), wrap it in a `Gdk.Texture`, and hand that to the Picture. The Picture's
natural size is then exactly the scaled size — bounded, stable, no layout thrash.
Each slot is a `slot_w`-wide box of that natural height with the thumbnail
centred; uniform width means FlowBox packs a predictable column count.

## Info sidebar

The `win.photo-info` action toggles a right-hand `Gtk.Revealer`
(`_build_info_panel`) that slides in. It shows metadata fields only — name, date
taken, dimensions, file size, path (selectable) — and deliberately does **not**
render the photo itself. `_update_info_panel` refreshes it on selection change
while open; a collapsed `SLIDE_LEFT` revealer reserves no width, so the grid
gets the full pane when the panel is closed.

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

## Testing without a display

- **Pure-model tests** (`tests/test_tree.py`, `tests/test_core.py`): build temp
  folders/images and assert on tree, config, index, and derivatives — no
  display.
- **Import smoke test** (`tests/test_import_smoke.py`): a permissive fake `gi`
  lets every `qdvc.gtk4.*` module import, exercising class bodies and top-level
  code.

Pre-ship check:

```sh
python3 -m py_compile qdvc/*.py qdvc/gtk4/*.py qdvc_tadaima.py
python3 tests/test_tree.py
python3 tests/test_core.py
python3 tests/test_import_smoke.py
```

## Deployment

Copy the tree anywhere and run `python3 qdvc_tadaima.py`, or install the
`.desktop` launcher (see README). `set_prgname("qdvc-tadaima")` must match the
launcher's `StartupWMClass`.

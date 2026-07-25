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
   selected node's `own_images`.

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
filename bar and the full-view caption. The **info button** (left of the primary
menu in both the gallery and the viewer, action `win.photo-info`) opens an
`Adw.MessageDialog` with the same fields plus the full path; it is enabled only
when a photo is selected.

## Persistent sidebar state

Expanded folders, the selected folder, and the focused folder persist between
sessions via `Config.expanded_folders` / `selected_folder` / `focus_folder`.
Expansion is tracked per row through `notify::expanded`; selection and focus on
change. All three are re-applied on startup by `_restore_session_state` (guarded
by `_restoring_state` so the programmatic restore doesn't rewrite what it's
restoring) and re-saved on window close.

## Thumbnail grid columns

The grid packs as many columns as fit at the current thumbnail size. The
mechanism: `Gtk.FlowBox` with `homogeneous=True`, and every `Gtk.FlowBoxChild`
filling a rigid `cell × cell` wrapper so all children report an identical size —
the condition FlowBox needs to compute `floor(width / (cell + spacing))`
columns. The picture inside keeps its true aspect (centred, non-expanding), so
it is never stretched. `cell` is `self._thumb_zoom` (Ctrl +/-/0).

## Full-view focus

Opening the viewer moves focus to the (focusable) picture via
`grab_focus`, so the "Back to Library" button is not auto-focused and the
←/→ key controller on the full page receives keystrokes. The back button is
`focus_on_click=False`.

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

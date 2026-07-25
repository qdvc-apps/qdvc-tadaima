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
- **Thumbnails**: under `$XDG_CACHE_HOME/qdvc-tadaima/thumbnails/`, named
  `<sha1-of-path>.<kind>.<ext>`. Small derivatives (`thumb`, `square`) are
  low-quality JPEGs (`SMALL_JPEG_QUALITY`) to save disk space; the `screen`
  preview is a higher-quality JPEG (`SCREEN_JPEG_QUALITY`). `INDEX_VERSION` was
  bumped to 2 when the format moved from PNG to JPEG, so old caches invalidate
  and regenerate cleanly.

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
the root store. It is driven from two places, both traced to stdout with a
`[tadaima]` prefix for debugging: clicking a flattened ancestor row, and the
right-click context action `win.ctx-focus` (installed fresh per right-click with
the target path bound in a closure).

## Live updates during a scan

`_scan_worker` runs off the main thread. It calls `_live_refresh` via
`GLib.idle_add` right after `sync_index` (so subfolders appear as soon as the
walk finds them) and then again every `LIVE_REFRESH_SECONDS` while thumbnails are
generated (so images appear as they are processed). `_live_refresh` rebuilds the
tree from the current index and re-renders the open folder, preserving selection.

- The gallery uses a `Gtk.FlowBox` (wrapping grid, `homogeneous` cells of
  `THUMB_CELL` px). Each thumbnail is a `Gtk.Picture` with
  `ContentFit.CONTAIN`, so images keep their aspect ratio and under-fill the
  cell — they are never cropped. The only cropped image is the 32px square
  sidebar folder icon.
- Double-activating a thumbnail builds `_full_images` from the current folder
  and shows the full-view page. A filmstrip (`Gtk.Box` in a horizontal
  scroller) plus prev/next header buttons and a `Gtk.EventControllerKey`
  (←/→) drive `_show_full_at(idx)`. The filename + position show in a caption
  bar *beneath* the picture; the window title is left untouched.
- Arrow-key navigation is intentionally *not* a global accelerator (it would
  hijack the sidebar/grid); it lives on the full page's key controller and is
  recorded in `SHORTCUTS` for the shortcuts window only.
- The status bar (`Adw.ToolbarView` bottom bar) is updated from the scan
  worker via `_post_status` → `GLib.idle_add`, naming each file as it is
  processed and refreshing on a short interval so it never looks stuck.

## Appearance (colour scheme)

`Config.color_scheme` (`dark`/`light`/`auto`, default `dark`) maps onto
`Adw.StyleManager.set_color_scheme` in `gtk4_app.apply_color_scheme`.
Preferences applies it live.

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

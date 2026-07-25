# QDVC Tadaima

A read-only photo browser for Linux, built with Python 3 + GTK 4 / libadwaita.

Tadaima shows the photos in folders you explicitly add. It **never modifies**
anything in those folders — it opens images for reading only, and writes
thumbnails and its index exclusively to a disposable cache under
`XDG_CACHE_HOME`.

## What it does

- **Expandable tree sidebar.** A resizable sidebar shows a folder hierarchy
  filtered down to just the paths leading to (and beneath) the folders you have
  added, with expand/collapse arrows on any folder that has subfolders. The main
  area shows the photos in the selected folder as a wrapping grid of thumbnails,
  each with a small drop shadow.
- **Uncropped thumbnails.** Thumbnails keep the original photo's proportions —
  a portrait stays a portrait — and the drop shadow and selection border sit on
  the photo's real edges. Use `Ctrl +` / `Ctrl -` to make thumbnails larger or
  smaller and `Ctrl 0` to reset; the default shows roughly ten across on a
  typical 1920-wide window.
- **Full view with a filmstrip.** Single-click a thumbnail to select it;
  double-click (or select and press `Enter`) to open it full-window. A filmstrip
  of square thumbnails runs along the top; prev/next arrows sit on either side,
  and the ← / → keys step through photos. The filename and position (e.g.
  "3 of 40") appear centered in a bar beneath the picture. The window title
  never changes. Click **Back to Library** (top-left) or press `Esc` to return,
  with your selection intact.
- **Selected-photo filename.** In the main view, the name of the currently
  selected photo shows in a bar just above the status bar.
- **Folder counts.** Every folder shows, on the right, how many images it
  contains recursively (children, grandchildren, and deeper).
- **Photo-crop folder icons.** A folder that has images directly inside it gets
  a small square crop of one of those photos in place of the plain folder icon,
  at the same size so the layout stays aligned. (Only this tiny icon is
  cropped; grid thumbnails never are.)
- **Focus on folder.** Right-click any folder for a menu with **Focus on
  folder** (makes it the root of the view, with its ancestors listed flat and in
  italics above it) and **Locate on disk** (reveals it in your file manager).
  Click a flattened ancestor to re-focus there; the top level `/` is the
  default, unfocused view. Use the → key to expand a selected folder in the
  sidebar and ← to collapse it.
- **Status bar.** A bar at the bottom names the photo currently being processed
  during a background scan and updates continuously, so you can see work is
  progressing.
- **Managing folders.** Add and remove scanned folders from the **Scanned
  folders** window (main menu, or `Ctrl+O`). Changes are staged and only take
  effect when you press **Save**; press `Esc` or **Cancel** to discard. Adding a
  folder that contains one already listed absorbs the child (the parent covers
  it), and existing thumbnails are reused rather than regenerated.
- **Background indexing.** On launch, Tadaima quietly scans your folders for
  changes in the background, generating thumbnails only for images that don't
  already have them. Subfolders and photos appear in the UI as soon as they are
  found and processed, and you can browse and expand folders freely while a scan
  runs.
- **Appearance.** Dark mode by default (best for photos); switch to Light or
  Automatic (follow the system) in Preferences.
- **Density.** The sidebar defaults to a genuinely tight **Compact** layout
  suited to laptops and workstations. Switch to **Relaxed** (libadwaita's roomy
  default) in Preferences.
- **Cache tools.** The main menu offers **Cache status…** and **Regenerate all
  caches**.

Supported formats: JPEG, PNG, HEIC (HEIC is best-effort; see below).

## Requirements

- Python **3.10+**
- **PyGObject** with **GTK 4** and **libadwaita**
  (`gir1.2-gtk-4.0`, `gir1.2-adw-1`, `python3-gi`)
- **PyYAML**
- **Pillow** for thumbnail generation (optional but strongly recommended;
  without it, Tadaima falls back to showing source images directly)
- **pillow-heif** (optional) for HEIC support

On Debian/Ubuntu:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml python3-pil
pip install --user pillow-heif   # optional, for HEIC
```

## Install & run

```sh
python3 qdvc_tadaima.py
```

The `--gtk4` flag and the `ui_backend` preference exist for parity with the
QDVC app family. Only the GTK 4 front-end is implemented in Tadaima; `--gtk3`
prints a note and falls through to GTK 4.

## `.desktop` launcher

Install a launcher at
`~/.local/share/applications/qdvc-tadaima.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=QDVC Tadaima
Comment=Read-only photo browser for folders you choose
Exec=python3 /full/path/to/qdvc_tadaima.py %U
Path=/full/path/to
Icon=emblem-photos
Terminal=false
Categories=Graphics;Viewer;GTK;
StartupNotify=true
StartupWMClass=qdvc-tadaima
```

`StartupWMClass=qdvc-tadaima` must match the `GLib.set_prgname("qdvc-tadaima")`
call in the app. After editing, refresh and validate:

```sh
update-desktop-database ~/.local/share/applications
desktop-file-validate ~/.local/share/applications/qdvc-tadaima.desktop
```

## Data & privacy

- Your photo folders are opened **read-only**. Tadaima never writes to them.
- Preferences: `$XDG_CONFIG_HOME/qdvc-tadaima/config.yml`
  (`~/.config/qdvc-tadaima/config.yml`).
- Cache (index + derivatives): `$XDG_CACHE_HOME/qdvc-tadaima/`
  (`~/.cache/qdvc-tadaima/`), with generated images under a `derivatives/`
  subfolder. Safe to delete at any time; it regenerates. Files whose contents
  are not a real supported image (e.g. macOS `._name.jpg` sidecar files) are
  skipped during scanning even if their extension matches.

# QDVC Tadaima

A read-only photo browser for Linux, built with Python 3 + GTK 4 / libadwaita.

Tadaima shows the photos in folders you explicitly add. It **never modifies**
anything in those folders — it opens images for reading only, and writes
thumbnails and its index exclusively to a disposable cache under
`XDG_CACHE_HOME`.

## What it does

- **Master–detail layout.** A resizable sidebar shows a folder hierarchy
  filtered down to just the paths leading to (and beneath) the folders you have
  added. The main area shows the photos in the selected folder as thumbnails,
  each with a small drop shadow.
- **Folder counts.** Every folder shows, on the right, how many images it
  contains recursively (children, grandchildren, and deeper).
- **Photo-crop folder icons.** A folder that has images directly inside it gets
  a small square crop of one of those photos in place of the plain folder icon,
  at the same size so the layout stays aligned.
- **Focus on folder.** Right-click any folder for a menu with **Focus on
  folder** (makes it the root of the view, with its ancestors listed flat and in
  italics above it) and **Locate on disk** (reveals it in your file manager).
  Click a flattened ancestor to re-focus there; the top level `/` is the
  default, unfocused view.
- **Full-screen view.** Double-click a thumbnail (or press it and it fills the
  window). Click the back arrow or press `Esc` to return, with your selection
  intact.
- **Background indexing.** On launch, Tadaima quietly scans your folders for
  changes in the background and generates thumbnails as needed.
- **Density.** The sidebar defaults to a tight **Compact** layout suited to
  laptops and workstations. Switch to **Relaxed** (libadwaita's default padding)
  in Preferences.
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
Icon=image-x-generic
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
- Cache (index + thumbnails): `$XDG_CACHE_HOME/qdvc-tadaima/`
  (`~/.cache/qdvc-tadaima/`). Safe to delete at any time; it regenerates.

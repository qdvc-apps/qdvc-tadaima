# QDVC Tadaima

A read-only photo browser: it shows the photos in the folders you choose as one
continuous stream, a heading per folder, with a folder sidebar that follows
along as you scroll. It **never changes anything in those folders** — it only
reads them, and keeps its thumbnails and index in a cache of its own.
"Tadaima" is what you call out in Japanese when you come home.

This repository holds two native editions of the same app:

| Edition | Folder | Built with |
| --- | --- | --- |
| Linux (GNOME) | [qdvc-tadaima-gtk4](qdvc-tadaima-gtk4) | Python 3, GTK 4 and libadwaita |
| macOS | [qdvc-tadaima-mac](qdvc-tadaima-mac) | Swift, SwiftUI and AppKit |

## Install

On either system, from the repository root:

```sh
scripts/build-app.sh --install
```

It builds the edition for the computer it runs on — the macOS edition on a
Mac, the GTK 4 edition anywhere else — and passes every option through to
that edition's own `scripts/build-app.sh` (run that directly for its
`--help`). Requirements and details are in each edition's README:

- **Linux:** Python 3.10+, PyGObject with GTK 4 and libadwaita, PyYAML and
  Pillow (pillow-heif for HEIC thumbnails). Installs per user into
  `~/.local`, with a menu entry and icon.
- **macOS:** macOS 14 or later and Xcode 16 or later (26 or later for the
  Liquid Glass look). Builds an ad-hoc signed app into `~/Applications`; no
  Apple Developer account needed.

## What the editions share

Both are the same app: the stream and its headings, the sidebar's tree,
counts and scrollspy, Focus on Folder, the rules for which folders are
scanned and which files count as photos, the thumbnail layout and keyboard
moves, and the camera details shown. The macOS edition's core
(`qdvc-tadaima-mac/Sources/TadaimaCore`) is a Swift port of the GTK edition's
pure Python modules (`qdvc-tadaima-gtk4/qdvc`), and its tests replay a parity
fixture generated from that Python by `qdvc-tadaima-mac/tools/make_fixtures.py`.
When changing one of those rules in the GTK edition, regenerate the fixture
and port the change; the Swift tests say what differs.

Each edition keeps its own settings and cache on its own computer, and each
follows its platform's conventions for menus, shortcuts, settings and the
look of things.

## Repository layout

```
scripts/build-app.sh     builds the edition for this OS
qdvc-tadaima-gtk4/       the Linux edition (its own README, docs, tests, build script)
qdvc-tadaima-mac/        the macOS edition (its own README, docs, tests, build script)
vibe-coding/             logs of the conversations the app was built in
```

"""The photo stream: every photo under the focused folder, one continuous,
sectioned grid. Pure — no GTK.

:func:`build_stream` walks the focused subtree in the sidebar's order (a
folder's own photos, then each subfolder's, depth first) and makes a
:class:`Section` for every folder that has photos of its own. A
:class:`StreamLayout` places those sections on a canvas of a given width:
a heading, then rows of square slots. All geometry is arithmetic per
section, so laying out a library of 50,000 photos touches only its folders,
and the widget can map scroll offsets to sections (the sidebar's scrollspy),
clicks to photos and photos to rectangles without measuring anything.
"""

from __future__ import annotations

import os
from bisect import bisect_right
from dataclasses import dataclass, field

from .models import FolderNode, ImageRecord
from .tree import is_within

# Geometry defaults (px).
MARGIN = 12            # around the whole stream
SPACING = 12           # between slots, horizontally and vertically
HEADER_HEIGHT = 44     # a section heading
SECTION_GAP = 16       # extra space above every heading but the first


@dataclass
class Section:
    """One folder's own photos in the stream."""

    path: str
    name: str
    context: str                  # dimmed text beside the name ("" for none)
    records: list[ImageRecord] = field(default_factory=list)


def _tilde(path: str, home: str | None) -> str:
    if home and is_within(home, path):
        rest = os.path.relpath(path, home)
        return "~" if rest == "." else os.path.join("~", rest)
    return path


def build_stream(focus: FolderNode, home: str | None = None) -> "Stream":
    """Sections for every folder at or below *focus* with photos of its own,
    in pre-order (the order of the sidebar tree).

    A heading's context is the folder's parent path, counted from the scanned
    folder it belongs to ("holiday-photos-copenhagen / nyhavn" for "boats"),
    or, for a scanned folder itself, the directory it lives in ("~/Desktop").
    """
    if home is None:
        home = os.path.expanduser("~")
    sections: list[Section] = []

    def visit(node: FolderNode, chain: list[str] | None) -> None:
        # *chain*: folder names from the scanned folder down to node's parent,
        # or None while above (or outside) any scanned folder.
        if node.is_scanned_root or chain is None:
            context = _tilde(os.path.dirname(node.path), home)
            if node.path == os.path.dirname(node.path):  # the filesystem root
                context = ""
            below = [node.name] if node.is_scanned_root else None
        else:
            context = " / ".join(chain)
            below = chain + [node.name]
        if node.own_images:
            sections.append(Section(node.path, node.name, context, list(node.own_images)))
        for child in node.sorted_children():
            visit(child, below)

    visit(focus, None)
    return Stream(sections)


class Stream:
    """The sections plus flat indexing: photo *i* of the whole stream."""

    def __init__(self, sections: list[Section]) -> None:
        self.sections = sections
        self.starts: list[int] = []      # flat index of each section's first photo
        self.records: list[ImageRecord] = []
        self._flat_of_path: dict[str, int] = {}
        self._section_of_path: dict[str, int] = {}
        for i, sec in enumerate(sections):
            self.starts.append(len(self.records))
            self._section_of_path[os.path.normpath(sec.path)] = i
            for rec in sec.records:
                self._flat_of_path[rec.path] = len(self.records)
                self.records.append(rec)

    def __len__(self) -> int:
        return len(self.records)

    def signature(self) -> tuple:
        """Changes whenever a photo is added, removed or changed, or a heading
        changes: equal signatures mean an identical stream."""
        return tuple(
            (s.path, s.context, tuple((r.path, r.mtime, r.size, r.thumb_w, r.thumb_h)
                                      for r in s.records))
            for s in self.sections
        )

    def section_of(self, flat: int) -> int:
        return bisect_right(self.starts, flat) - 1

    def index_of(self, photo_path: str) -> int | None:
        return self._flat_of_path.get(photo_path)

    def section_index(self, folder_path: str) -> int | None:
        return self._section_of_path.get(os.path.normpath(folder_path))

    def first_section_within(self, folder_path: str) -> int | None:
        """The section to scroll to for a sidebar folder: the folder's own, or
        (for a folder with only subfolders) its first descendant's."""
        exact = self.section_index(folder_path)
        if exact is not None:
            return exact
        for i, sec in enumerate(self.sections):  # pre-order: first match wins
            if is_within(folder_path, sec.path):
                return i
        return None


class StreamLayout:
    """Geometry of a :class:`Stream` at a given canvas *width* and *slot* size.

    Slots are *slot* × *slot* squares; a thumbnail is centred in its slot.
    Coordinates are canvas pixels, y growing downwards."""

    def __init__(self, stream: Stream, width: int, slot: int,
                 spacing: int = SPACING, margin: int = MARGIN,
                 header_height: int = HEADER_HEIGHT, section_gap: int = SECTION_GAP):
        self.stream = stream
        self.width = width
        self.slot = slot
        self.spacing = spacing
        self.margin = margin
        self.header_height = header_height
        self.pitch = slot + spacing
        usable = max(0, width - 2 * margin)
        self.columns = max(1, (usable + spacing) // self.pitch)
        self.header_ys: list[int] = []   # top of each heading
        self.rows_ys: list[int] = []     # top of each section's first row
        self.end_ys: list[int] = []      # bottom of each section's last row
        self.row_counts: list[int] = []
        y = margin
        for i, sec in enumerate(stream.sections):
            if i:
                y += section_gap
            self.header_ys.append(y)
            y += header_height
            rows = -(-len(sec.records) // self.columns)
            self.rows_ys.append(y)
            self.row_counts.append(rows)
            y += rows * self.pitch - (spacing if rows else 0)
            self.end_ys.append(y)
        self.height = y + margin

    # ------------------------------------------------------------ geometry
    def cell(self, flat: int) -> tuple[int, int, int, int]:
        """(x, y, w, h) of photo *flat*'s slot."""
        s = self.stream.section_of(flat)
        local = flat - self.stream.starts[s]
        row, col = divmod(local, self.columns)
        return (self.margin + col * self.pitch, self.rows_ys[s] + row * self.pitch,
                self.slot, self.slot)

    def row_of(self, flat: int) -> tuple[int, int, int]:
        """(section, row within section, column) of photo *flat*."""
        s = self.stream.section_of(flat)
        row, col = divmod(flat - self.stream.starts[s], self.columns)
        return s, row, col

    def photo_at(self, x: float, y: float) -> int | None:
        """The photo whose slot contains (x, y), if any."""
        s = bisect_right(self.rows_ys, y) - 1
        if s < 0 or y >= self.end_ys[s]:
            return None
        row, dy = divmod(int(y - self.rows_ys[s]), self.pitch)
        col, dx = divmod(int(x - self.margin), self.pitch)
        if x < self.margin or dy >= self.slot or dx >= self.slot or col >= self.columns:
            return None
        local = row * self.columns + col
        if local >= len(self.stream.sections[s].records):
            return None
        return self.stream.starts[s] + local

    def section_at(self, y: float) -> int | None:
        """The section showing at canvas height *y*: the last one whose heading
        starts at or above it (the scrollspy's notion of "current")."""
        if not self.header_ys:
            return None
        return max(0, bisect_right(self.header_ys, y) - 1)

    def sections_between(self, y0: float, y1: float) -> range:
        """Sections with any part (heading or photos) in [y0, y1)."""
        if not self.header_ys:
            return range(0)
        first = max(0, bisect_right(self.end_ys, y0 - 1))
        last = bisect_right(self.header_ys, y1 - 1)
        return range(first, max(first, last))

    def photos_between(self, y0: float, y1: float) -> list[int]:
        """Flat indices of the photos whose slots overlap [y0, y1), in order."""
        out: list[int] = []
        for s in self.sections_between(y0, y1):
            n = len(self.stream.sections[s].records)
            if not n:
                continue
            top = self.rows_ys[s]
            r0 = max(0, int((y0 - top) // self.pitch))
            r1 = min(self.row_counts[s], int((y1 - top - 1) // self.pitch) + 1)
            if r1 <= r0:
                continue
            start = self.stream.starts[s]
            out.extend(range(start + r0 * self.columns,
                             start + min(n, r1 * self.columns)))
        return out

    # ------------------------------------------------------------ keyboard
    def neighbour(self, flat: int, move: str, page_rows: int = 1) -> int:
        """Photo reached from *flat* by *move*: "left"/"right" step through the
        whole stream; "up"/"down" go to the row above/below in the same
        column (into the neighbouring section at a section's edge);
        "page_up"/"page_down" repeat that *page_rows* times; "home"/"end"."""
        total = len(self.stream)
        if total == 0:
            return flat
        if move == "left":
            return max(0, flat - 1)
        if move == "right":
            return min(total - 1, flat + 1)
        if move == "home":
            return 0
        if move == "end":
            return total - 1
        if move in ("up", "down", "page_up", "page_down"):
            steps = page_rows if move.startswith("page") else 1
            up = move in ("up", "page_up")
            for _ in range(max(1, steps)):
                nxt = self._vertical(flat, up)
                if nxt == flat:
                    break
                flat = nxt
            return flat
        raise ValueError(move)

    def _vertical(self, flat: int, up: bool) -> int:
        s, row, col = self.row_of(flat)
        starts, cols = self.stream.starts, self.columns
        if up:
            if row > 0:
                return flat - cols
            if s == 0:
                return flat
            prev = s - 1
            while prev >= 0 and not self.stream.sections[prev].records:
                prev -= 1
            if prev < 0:
                return flat
            n = len(self.stream.sections[prev].records)
            last_row = (n - 1) // cols
            return starts[prev] + min(n - 1, last_row * cols + col)
        n = len(self.stream.sections[s].records)
        if (row + 1) * cols < n:
            return starts[s] + min(n - 1, (row + 1) * cols + col)
        nxt = s + 1
        while nxt < len(starts) and not self.stream.sections[nxt].records:
            nxt += 1
        if nxt >= len(starts):
            return flat
        return starts[nxt] + min(len(self.stream.sections[nxt].records) - 1, col)

    # ------------------------------------------------------------- anchors
    def anchor_at(self, y: float):
        """Something to keep at canvas height *y* across a relayout (another
        width or slot size, or photos added above): the photo row there, or
        the section heading. Returns None for an empty stream."""
        s = self.section_at(y)
        if s is None:
            return None
        sec = self.stream.sections[s]
        if y >= self.rows_ys[s] and sec.records:
            row = min(self.row_counts[s] - 1, int((y - self.rows_ys[s]) // self.pitch))
            local = row * self.columns
            frac = (y - self.rows_ys[s] - row * self.pitch) / self.pitch
            return ("photo", sec.records[local].path, frac)
        return ("section", sec.path, y - self.header_ys[s])

    def y_for_anchor(self, anchor) -> float | None:
        if anchor is None:
            return None
        kind, key, offset = anchor
        if kind == "photo":
            flat = self.stream.index_of(key)
            if flat is None:
                return None
            return self.cell(flat)[1] + offset * self.pitch
        s = self.stream.section_index(key)
        if s is None:
            return None
        return self.header_ys[s] + offset

    def section_top(self, s: int) -> int:
        return self.header_ys[s]

"""The photo stream: every photo under the focused folder in one scrolling,
sectioned grid, with a heading per folder.

A virtual canvas. The whole stream is laid out arithmetically by
:class:`qdvc.stream.StreamLayout`, and a ``Gtk.Fixed`` as tall as that layout
sits in a ``Gtk.Viewport``; only the photos and headings within a screen of
the visible area have widgets, recycled as you scroll. So a folder tree of
50,000 photos costs the same to show as one of 50, and because the geometry
is exact the stream can tell which folder is at the top (the sidebar's
scrollspy) and scroll a folder's heading to the top without measuring.

Thumbnails are decoded on background threads (:mod:`.gtk4_thumbs`), what is
on screen first, and kept in a texture cache. Until a picture arrives its slot
shows a faint placeholder of the picture's final size, so nothing moves.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Gdk, Gtk, Pango

from ..stream import HEADER_HEIGHT, Stream, StreamLayout
from ..ui_prefs import fit_within, thumb_key
from .gtk4_thumbs import TextureCache, ThumbDecoder

# The scrollspy's "current" line, this far below the top of the view: a
# heading counts as reached once it is this close to the top.
SPY_OFFSET = 24
# Space left above a heading scrolled to the top, and around a photo
# scrolled into view by the keyboard.
SCROLL_PAD = 8


class PhotoStream:
    """The stream widget (:attr:`widget`) and its state.

    Callbacks, all on the main thread:
      *on_selection(rec_or_None)* when the selected photo changes;
      *on_activate(rec)* on double-click or Enter;
      *on_section(section_or_None)* when the folder at the top changes.
    """

    def __init__(
        self,
        on_selection: Callable,
        on_activate: Callable,
        on_section: Callable,
        plain_thumbs: bool = False,
        decoder: ThumbDecoder | None = None,
        slot: int = 150,
    ) -> None:
        self._on_selection = on_selection
        self._on_activate = on_activate
        self._on_section = on_section
        self._plain = plain_thumbs

        self.canvas = Gtk.Fixed()
        self.canvas.set_focusable(True)
        self.canvas.add_css_class("tadaima-stream")
        viewport = Gtk.Viewport()
        # The canvas is one huge focusable widget: never scroll "to" it.
        viewport.set_scroll_to_focus(False)
        viewport.set_child(self.canvas)
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        # EXTERNAL, not NEVER: NEVER would make the canvas's width (its tiles'
        # extent) the window's minimum width, so it could never shrink and
        # the stream never relayout narrower.
        self.scroller.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_child(viewport)
        self.widget = self.scroller

        self.vadj = self.scroller.get_vadjustment()
        self.hadj = self.scroller.get_hadjustment()
        self.vadj.connect("value-changed", self._on_scrolled)
        self.vadj.connect("changed", self._on_vadj_changed)
        self.hadj.connect("changed", self._on_hadj_changed)

        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self._on_pressed)
        self.canvas.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.canvas.add_controller(keys)

        self._decoder = decoder or ThumbDecoder(self._on_decoded)
        self._cache = TextureCache()
        self._pointer = Gdk.Cursor.new_from_name("pointer")

        self.stream: Stream | None = None
        self._signature = None
        self._layout: StreamLayout | None = None
        self._layout_version = 0        # bumped on every relayout
        self._width = 0
        self._slot = slot
        self._tiles: dict = {}          # photo path -> bound tile
        self._tiles_by_key: dict = {}   # thumbnail key -> bound tile
        self._tile_pool: list = []
        self._headers: dict = {}        # section path -> bound heading
        self._header_pool: list = []
        self._failed: set = set()       # keys whose decode failed
        self._last_range = None
        self._selected_path: str | None = None
        self._section_path: str | None = None
        # A heading scrolled to the top on request (a sidebar click) stays
        # "current" until the user scrolls, even if the view could not reach it
        # (a short section at the end) — so the clicked folder stays selected.
        self._hold: int | None = None
        self._hold_value: float | None = None
        self._pending_y: float | None = None
        self._pending_folder: str | None = None

    # ================================================================ API
    def set_stream(self, stream: Stream, keep_position: bool) -> None:
        """Show *stream*. With *keep_position* (a scan's refresh of the same
        view) the photos at the top of the view stay put; otherwise (a new
        focus) it starts at the top."""
        signature = stream.signature()
        if keep_position and self.stream is not None and signature == self._signature:
            return  # nothing changed
        anchor = None
        if keep_position and self._layout is not None and self._hold is None:
            anchor = self._layout.anchor_at(self.vadj.get_value())
        hold_path = None
        if keep_position and self._hold is not None and self.stream is not None:
            hold_path = self.stream.sections[self._hold].path
        self.stream = stream
        self._signature = signature
        self._failed.clear()
        self._hold = None
        if not keep_position:
            self._pending_folder = None
        if self._selected_path is not None and stream.index_of(self._selected_path) is None:
            self._selected_path = None
            self._on_selection(None)
        self._relayout(anchor=anchor, top=not keep_position)
        if hold_path is not None and self._layout is not None:
            s = stream.first_section_within(hold_path)
            if s is not None:
                self.scroll_to_section(s)

    def set_slot(self, slot: int) -> None:
        """Change the thumbnail size, keeping the selected photo (if on
        screen) or the photos at the top of the view where they are."""
        if slot == self._slot:
            return
        old, self._slot = self._layout, slot
        self._release_all()
        if old is None:
            return
        value = self.vadj.get_value()
        page = self.vadj.get_page_size()
        flat = self._selected_flat()
        if flat is not None:
            y = old.cell(flat)[1]
            if value <= y < value + page:
                offset = y - value
                self._relayout(anchor=None)
                self._scroll_to_y(self._layout.cell(flat)[1] - offset)
                return
        self._relayout(anchor=old.anchor_at(value))

    def scroll_to_folder(self, folder_path: str) -> bool:
        """Scroll *folder_path*'s photos (or its first subfolder's) to the top.
        Before the first layout, this happens as soon as there is one."""
        if self.stream is None:
            return False
        s = self.stream.first_section_within(folder_path)
        if s is None:
            return False
        if self._layout is None:
            self._pending_folder = folder_path
            return True
        self.scroll_to_section(s)
        return True

    def scroll_to_section(self, s: int) -> None:
        self._hold = s
        y = max(0, self._layout.section_top(s) - SCROLL_PAD)
        self._hold_value = self._clamp(y)
        self._scroll_to_y(y)
        self._update_spy()

    def select_path(self, photo_path: str, scroll: bool = True) -> None:
        if self.stream is None:
            return
        flat = self.stream.index_of(photo_path)
        if flat is not None:
            self._select(flat, scroll)

    @property
    def selected_record(self):
        flat = self._selected_flat()
        return None if flat is None else self.stream.records[flat]

    @property
    def holding(self) -> bool:
        """True while a folder scrolled to on request is still in place."""
        return self._hold is not None

    @property
    def current_section(self):
        s = self._current_section_index()
        return None if s is None else self.stream.sections[s]

    def grab_focus(self) -> None:
        self.canvas.grab_focus()

    def cancel(self) -> None:
        """Stop decoding (window closing)."""
        self._decoder.clear()

    # ============================================================ layout
    def _relayout(self, anchor=None, top: bool = False) -> None:
        if self.stream is None or self._width <= 0:
            return
        self._layout = StreamLayout(self.stream, self._width, self._slot)
        self._layout_version += 1
        self.canvas.set_size_request(-1, self._layout.height)
        # Re-place (or drop) the widgets that are already showing.
        for path in list(self._tiles):
            if self.stream.index_of(path) is None:
                self._release_tile(path)
            else:
                self._place_tile(self._tiles[path])
        for path in list(self._headers):
            s = self.stream.section_index(path)
            if s is None:
                self._release_header(path)
            else:
                self._fill_header(self._headers[path], s)
        self._last_range = None
        if self._pending_folder is not None:
            folder, self._pending_folder = self._pending_folder, None
            if self.scroll_to_folder(folder):
                return
        if self._hold is not None and self._hold < len(self.stream.sections):
            self.scroll_to_section(self._hold)
            return
        y = 0.0 if top else self._layout.y_for_anchor(anchor)
        if y is None:
            y = self.vadj.get_value()
        self._scroll_to_y(y)
        self._update_visible()
        self._update_spy()

    def _clamp(self, y: float) -> float:
        page = self.vadj.get_page_size()
        return max(0.0, min(float(y), max(0.0, self._layout.height - page)))

    def _scroll_to_y(self, y: float) -> None:
        """Scroll so canvas height *y* is at the top — now if the scrollbar
        already spans the new layout, else once it does (the canvas's new
        height only reaches the adjustment at the next allocation)."""
        y = self._clamp(y)
        if y <= self.vadj.get_upper() - self.vadj.get_page_size() + 0.5:
            self._pending_y = None
            if abs(self.vadj.get_value() - y) > 0.5:
                self.vadj.set_value(y)
            else:
                self._update_visible()
        else:
            self._pending_y = y

    def _on_vadj_changed(self, adj) -> None:
        if self._pending_y is not None:
            self._scroll_to_y(self._pending_y)
        self._last_range = None
        self._update_visible()
        self._update_spy()

    def _on_hadj_changed(self, adj) -> None:
        width = int(adj.get_page_size())
        if width <= 0 or width == self._width:
            return
        old = self._layout
        self._width = width
        if old is None:
            self._relayout(top=True)
            return
        cols = StreamLayout(Stream([]), width, self._slot).columns
        if cols != old.columns:
            self._relayout(anchor=old.anchor_at(self.vadj.get_value()))
        else:  # same grid; only the headings' width changes
            for path, header in self._headers.items():
                self._fill_header(header, self.stream.section_index(path))

    def _on_scrolled(self, adj) -> None:
        if (self._hold is not None and self._pending_y is None
                and abs(adj.get_value() - self._hold_value) > 1):
            self._hold = None  # the user scrolled away from the requested folder
        self._update_visible()
        self._update_spy()

    # ============================================================ visible
    def _update_visible(self) -> None:
        layout = self._layout
        if layout is None:
            return
        value = self.vadj.get_value()
        page = self.vadj.get_page_size() or 800
        y0, y1 = value - page, value + 2 * page
        rng = (int(y0 // layout.pitch), int(y1 // layout.pitch), self._layout_version)
        if rng == self._last_range:
            return
        self._last_range = rng
        records = self.stream.records
        flats = layout.photos_between(y0, y1)
        wanted = {records[f].path: f for f in flats}
        for path in [p for p in self._tiles if p not in wanted]:
            self._release_tile(path)
        for path, flat in wanted.items():
            if path not in self._tiles:
                self._bind_tile(records[flat])
        sections = layout.sections_between(y0, y1)
        shown = {self.stream.sections[s].path: s for s in sections}
        for path in [p for p in self._headers if p not in shown]:
            self._release_header(path)
        for path, s in shown.items():
            if path not in self._headers:
                header = self._header_pool.pop() if self._header_pool else self._new_header()
                header.set_visible(True)
                self._headers[path] = header
                self._fill_header(header, s)
        # Decode what's missing: on screen first, then the screen above and below.
        on_screen = set(layout.photos_between(value, value + page))
        first, rest = [], []
        for f in flats:
            rec = records[f]
            key = self._key(rec)
            if key in self._cache or key in self._failed or not rec.thumb_path:
                continue
            job = (key, rec.thumb_path, self._display_size(rec), self._slot)
            (first if f in on_screen else rest).append(job)
        self._decoder.set_queue(first + rest)

    def _key(self, rec):
        return (thumb_key(rec), self._slot)

    def _display_size(self, rec):
        if rec.thumb_w > 0 and rec.thumb_h > 0:
            return fit_within(rec.thumb_w, rec.thumb_h, self._slot)
        return None  # unknown until decoded

    # -------------------------------------------------------------- tiles
    def _new_tile(self):
        if self._plain:
            tile = Gtk.Image()
        else:
            tile = Gtk.Picture()
            tile.set_can_shrink(False)
            tile.set_content_fit(Gtk.ContentFit.FILL)
        tile.add_css_class("tadaima-thumb")
        tile.set_cursor(self._pointer)
        self.canvas.put(tile, 0, 0)
        return tile

    def _bind_tile(self, rec) -> None:
        tile = self._tile_pool.pop() if self._tile_pool else self._new_tile()
        key = self._key(rec)
        tile._path, tile._key = rec.path, key
        texture = self._cache.get(key)
        if texture is not None:
            tile._size = (texture.get_width(), texture.get_height())
            self._set_paintable(tile, texture)
            tile.remove_css_class("tadaima-thumb-loading")
        else:
            tile._size = self._display_size(rec) or (self._slot, self._slot)
            self._set_paintable(tile, None)
            tile.add_css_class("tadaima-thumb-loading")
        self._size_tile(tile)
        tile.set_tooltip_text(rec.name)
        if rec.path == self._selected_path:
            tile.add_css_class("tadaima-thumb-selected")
        else:
            tile.remove_css_class("tadaima-thumb-selected")
        tile.set_visible(True)
        self._tiles[rec.path] = tile
        self._tiles_by_key[key] = tile
        self._place_tile(tile)

    def _set_paintable(self, tile, texture) -> None:
        if isinstance(tile, Gtk.Picture):
            tile.set_paintable(texture)
        elif texture is not None:
            tile.set_from_paintable(texture)
        else:
            tile.clear()

    def _size_tile(self, tile) -> None:
        w, h = tile._size
        if isinstance(tile, Gtk.Picture):
            tile.set_size_request(w, h)
        else:
            tile.set_pixel_size(max(w, h))

    def _place_tile(self, tile) -> None:
        flat = self.stream.index_of(tile._path)
        x, y, slot, _ = self._layout.cell(flat)
        w, h = tile._size
        self.canvas.move(tile, x + (slot - w) // 2, y + (slot - h) // 2)

    def _release_tile(self, path: str) -> None:
        tile = self._tiles.pop(path)
        if self._tiles_by_key.get(tile._key) is tile:
            del self._tiles_by_key[tile._key]
        tile.set_visible(False)
        self._set_paintable(tile, None)
        self._tile_pool.append(tile)

    def _release_all(self) -> None:
        for path in list(self._tiles):
            self._release_tile(path)
        self._last_range = None

    def _on_decoded(self, key, pixbuf) -> None:
        if pixbuf is None:
            self._failed.add(key)
            return
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self._cache.put(key, texture)
        tile = self._tiles_by_key.get(key)
        if tile is None:
            return
        self._set_paintable(tile, texture)
        tile.remove_css_class("tadaima-thumb-loading")
        size = (texture.get_width(), texture.get_height())
        if size != tile._size:  # its size wasn't known in advance
            tile._size = size
            self._size_tile(tile)
            self._place_tile(tile)

    # ----------------------------------------------------------- headings
    def _new_header(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_baseline_position(Gtk.BaselinePosition.BOTTOM)
        box.add_css_class("tadaima-stream-heading")
        baseline = getattr(Gtk.Align, "BASELINE_FILL", None) or Gtk.Align.BASELINE
        title = Gtk.Label(xalign=0.0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_valign(baseline)
        title.add_css_class("tadaima-stream-title")
        context = Gtk.Label(xalign=0.0)
        context.set_hexpand(True)
        context.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        context.set_valign(baseline)
        context.add_css_class("tadaima-stream-context")
        context.add_css_class("dim-label")
        box.append(title)
        box.append(context)
        box._title, box._context = title, context
        self.canvas.put(box, 0, 0)
        return box

    def _fill_header(self, header, s: int) -> None:
        sec = self.stream.sections[s]
        header._title.set_text(sec.name)
        header._context.set_text(sec.context)
        header._context.set_visible(bool(sec.context))
        header.set_tooltip_text(sec.path)
        layout = self._layout
        header.set_size_request(max(1, layout.width - 2 * layout.margin),
                                HEADER_HEIGHT - SCROLL_PAD)
        self.canvas.move(header, layout.margin, layout.section_top(s))

    def _release_header(self, path: str) -> None:
        header = self._headers.pop(path)
        header.set_visible(False)
        self._header_pool.append(header)

    # ========================================================== scrollspy
    def _current_section_index(self):
        layout = self._layout
        if layout is None or not self.stream.sections:
            return None
        if self._hold is not None:
            return self._hold
        value = self.vadj.get_value()
        page = self.vadj.get_page_size()
        if page and layout.height > page and value + page >= layout.height - 1:
            return len(self.stream.sections) - 1  # at the very bottom
        return layout.section_at(value + SPY_OFFSET)

    def _update_spy(self) -> None:
        sec = self.current_section
        path = None if sec is None else sec.path
        if path != self._section_path:
            self._section_path = path
            self._on_section(sec)

    # ========================================================== selection
    def _selected_flat(self):
        if self._selected_path is None or self.stream is None:
            return None
        return self.stream.index_of(self._selected_path)

    def _select(self, flat: int, scroll: bool) -> None:
        rec = self.stream.records[flat]
        if rec.path != self._selected_path:
            old = self._tiles.get(self._selected_path)
            if old is not None:
                old.remove_css_class("tadaima-thumb-selected")
            self._selected_path = rec.path
            new = self._tiles.get(rec.path)
            if new is not None:
                new.add_css_class("tadaima-thumb-selected")
            self._on_selection(rec)
        if scroll:
            self._ensure_visible(flat)

    def _ensure_visible(self, flat: int) -> None:
        layout = self._layout
        if layout is None:
            return
        s, row, _ = layout.row_of(flat)
        y = layout.cell(flat)[1]
        top = (layout.section_top(s) if row == 0 else y) - SCROLL_PAD
        bottom = y + layout.slot + SCROLL_PAD
        value, page = self.vadj.get_value(), self.vadj.get_page_size()
        self._hold = None
        if top < value:
            self._scroll_to_y(top)
        elif bottom > value + page:
            self._scroll_to_y(bottom - page)

    def _on_pressed(self, gesture, n_press, x, y) -> None:
        self.canvas.grab_focus()
        if self._layout is None:
            return
        flat = self._layout.photo_at(x, y)
        if flat is None:
            return
        self._select(flat, scroll=False)
        if n_press == 2:
            self._on_activate(self.stream.records[flat])

    _MOVES = {
        "Left": "left", "Right": "right", "Up": "up", "Down": "down",
        "Home": "home", "End": "end", "Page_Up": "page_up", "Page_Down": "page_down",
        "KP_Left": "left", "KP_Right": "right", "KP_Up": "up", "KP_Down": "down",
    }

    def _on_key(self, controller, keyval, keycode, state) -> bool:
        if self._layout is None or not len(self.stream):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        name = Gdk.keyval_name(keyval)
        if name in ("Return", "KP_Enter"):
            rec = self.selected_record
            if rec is not None:
                self._on_activate(rec)
                return True
            return False
        move = self._MOVES.get(name)
        if move is None:
            return False
        flat = self._selected_flat()
        if flat is None:  # nothing selected yet: start at the top of the view
            value = self.vadj.get_value()
            visible = self._layout.photos_between(value, value + self.vadj.get_page_size())
            flat = visible[0] if visible else 0
        else:
            page_rows = max(1, int(self.vadj.get_page_size() // self._layout.pitch) - 1)
            flat = self._layout.neighbour(flat, move, page_rows)
        self._select(flat, scroll=True)
        return True

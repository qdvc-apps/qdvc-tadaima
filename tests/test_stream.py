"""The photo stream: its pure layout, and the PhotoStream widget's behaviour.

The widget runs against a small stand-in toolkit (installed into
qdvc.gtk4.gtk4_stream): adjustments that clamp and signal like GTK's, a canvas
that records where children are placed, and an ``allocate()`` step standing in
for GTK's next layout pass — the only time a new canvas height reaches the
scrollbar (verified against libgtk-4 4.14: a scroll beyond the old height is
clamped until then).
"""

import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_import_smoke import _install_fake_gi  # noqa: E402

_install_fake_gi()

from qdvc.gtk4 import gtk4_stream, gtk4_thumbs  # noqa: E402
from qdvc.models import ImageRecord  # noqa: E402
from qdvc.stream import StreamLayout, build_stream  # noqa: E402
from qdvc.tree import build_tree  # noqa: E402
from qdvc.ui_prefs import fit_within  # noqa: E402

ROOT = "/home/james/Desktop/holiday-photos-copenhagen"


# ------------------------------------------------------------- stand-in toolkit
class Widget:
    def __init__(self, *a, **k):
        self.css, self.visible, self.size, self.tooltip = set(), True, (-1, -1), None

    def add_css_class(self, c):
        self.css.add(c)

    def remove_css_class(self, c):
        self.css.discard(c)

    def set_visible(self, v):
        self.visible = v

    def set_size_request(self, w, h):
        self.size = (w, h)

    def set_tooltip_text(self, t):
        self.tooltip = t

    def __getattr__(self, name):  # every other setter is a no-op
        if name.startswith(("set_", "append", "add_controller", "grab_focus", "connect")):
            return lambda *a, **k: None
        raise AttributeError(name)


class Picture(Widget):
    paintable = None

    def set_paintable(self, p):
        self.paintable = p


class Image(Widget):
    pass


class Label(Widget):
    text = ""

    def set_text(self, t):
        self.text = t


class Fixed(Widget):
    def __init__(self):
        super().__init__()
        self.children = {}

    def put(self, child, x, y):
        self.children[child] = (x, y)

    def move(self, child, x, y):
        self.children[child] = (x, y)


class Adjustment:
    def __init__(self, page=0):
        self.value, self.upper, self.page = 0.0, float(page), float(page)
        self.handlers = {}

    def connect(self, signal, cb):
        self.handlers[signal] = cb

    def get_value(self):
        return self.value

    def get_upper(self):
        return self.upper

    def get_page_size(self):
        return self.page

    def set_value(self, v):
        v = max(0.0, min(float(v), self.upper - self.page))
        if v != self.value:
            self.value = v
            self.handlers["value-changed"](self)


class ScrolledWindow(Widget):
    def __init__(self):
        super().__init__()
        self.h, self.v = Adjustment(), Adjustment()

    def get_hadjustment(self):
        return self.h

    def get_vadjustment(self):
        return self.v


class Texture:
    def __init__(self, w, h):
        self.w, self.h = w, h

    def get_width(self):
        return self.w

    def get_height(self):
        return self.h


NS = types.SimpleNamespace
gtk4_stream.Gtk = NS(
    Fixed=Fixed, Viewport=Widget, ScrolledWindow=ScrolledWindow, Picture=Picture,
    Image=Image, Box=Widget, Label=Label, GestureClick=Widget, EventControllerKey=Widget,
    PolicyType=NS(EXTERNAL=3, AUTOMATIC=0), Orientation=NS(HORIZONTAL=0),
    BaselinePosition=NS(BOTTOM=2), Align=NS(BASELINE_FILL=5), ContentFit=NS(FILL=0),
)
gtk4_stream.Pango = NS(EllipsizeMode=NS(END=3, MIDDLE=2))
gtk4_stream.Gdk = NS(
    Cursor=NS(new_from_name=lambda n: n),
    Texture=NS(new_for_pixbuf=lambda pb: Texture(*pb)),
    ModifierType=NS(CONTROL_MASK=4, ALT_MASK=8),
    keyval_name=lambda k: k,  # tests pass key names as keyvals
)


class FakeDecoder:
    def __init__(self):
        self.queue, self.cleared = [], 0

    def set_queue(self, jobs):
        self.queue = list(jobs)

    def clear(self):
        self.cleared += 1


# --------------------------------------------------------------------- fixtures
def _rec(folder, i, w=500, h=375):
    path = f"{folder}/{i:04}.jpg"
    return ImageRecord(path=path, mtime=0.0, size=1, key=path,
                       thumb_path=f"/cache/{i}-{os.path.basename(folder)}.jpg",
                       thumb_w=w, thumb_h=h)


def _library(counts=(("", 8), ("nyhavn", 14), ("nyhavn/boats", 9), ("rundetarn", 3))):
    recs = []
    for sub, n in counts:
        folder = f"{ROOT}/{sub}".rstrip("/")
        recs += [_rec(folder, i) for i in range(n)]
    return build_tree(recs, [ROOT])


def _stream_of(tree):
    return build_stream(tree, home="/home/james")


class Harness:
    def __init__(self, width=1000, page=600):
        self.selected, self.activated, self.sections = [], [], []
        self.decoder = FakeDecoder()
        self.ps = gtk4_stream.PhotoStream(
            on_selection=self.selected.append,
            on_activate=self.activated.append,
            on_section=lambda s: self.sections.append(s and s.name),
            decoder=self.decoder,
        )
        self.ps.vadj.page = float(page)
        self._width = width

    def show_window(self):
        """GTK's first allocation: the width arrives, then the height."""
        self.ps.hadj.page = float(self._width)
        self.ps._on_hadj_changed(self.ps.hadj)
        self.allocate()

    def allocate(self):
        """GTK's next layout pass: the canvas's height reaches the scrollbar."""
        adj = self.ps.vadj
        adj.upper = max(float(self.ps.canvas.size[1]), adj.page)
        if adj.value > adj.upper - adj.page:
            adj.set_value(adj.upper - adj.page)
        adj.handlers["changed"](adj)

    def scroll(self, y):
        self.ps.vadj.set_value(y)

    def shown_tiles(self):
        return {t._path for t in self.ps._tiles.values() if t.visible}

    def tile(self, path):
        return self.ps._tiles[path]


# ------------------------------------------------------------------ pure layout
def test_sections_follow_the_sidebar_order_with_context():
    stream = _stream_of(_library())
    assert [(s.name, s.context, len(s.records)) for s in stream.sections] == [
        ("holiday-photos-copenhagen", "~/Desktop", 8),
        ("nyhavn", "holiday-photos-copenhagen", 14),
        ("boats", "holiday-photos-copenhagen / nyhavn", 9),
        ("rundetarn", "holiday-photos-copenhagen", 3),
    ]
    # Folders without photos of their own (home, james, Desktop) get none.
    assert stream.first_section_within("/home/james") == 0
    assert stream.first_section_within(ROOT + "/nyhavn") == 1
    assert stream.first_section_within("/elsewhere") is None


def test_focus_limits_the_stream():
    tree = _library()
    from qdvc.tree import find_node
    stream = build_stream(find_node(tree, ROOT + "/nyhavn"), home="/home/james")
    assert [s.name for s in stream.sections] == ["nyhavn", "boats"]


def test_layout_geometry_and_hit_testing():
    stream = _stream_of(_library())
    L = StreamLayout(stream, 1000, 150)
    assert L.columns == 6                      # (1000 - 24 + 12) // 162
    assert L.header_ys[0] == 12 and L.rows_ys[0] == 56
    # 8 photos -> 2 rows: 56 + 2*162 - 12 = 368; the next heading 16 lower.
    assert L.end_ys[0] == 368 and L.header_ys[1] == 384
    assert L.cell(7) == (12 + 162, 56 + 162, 150, 150)
    assert L.photo_at(12 + 162 + 10, 56 + 162 + 10) == 7
    assert L.photo_at(12 + 162 * 2 + 10, 56 + 162 + 10) is None   # past the last photo
    assert L.photo_at(12 + 155, 60) is None                        # in the gap
    assert L.photo_at(20, 30) is None                              # on a heading
    assert L.section_at(0) == 0 and L.section_at(383) == 0 and L.section_at(384) == 1


def test_visible_ranges():
    stream = _stream_of(_library())
    L = StreamLayout(stream, 1000, 150)
    assert L.photos_between(0, 300) == list(range(0, 8))
    assert list(L.sections_between(0, 300)) == [0]
    # Spanning the end of the first section and the start of the second.
    got = L.photos_between(300, 600)
    assert got[0] == 6 and 8 in got and 14 in got
    assert list(L.sections_between(300, 600)) == [0, 1]


def test_keyboard_neighbours_cross_sections():
    stream = _stream_of(_library())
    L = StreamLayout(stream, 1000, 150)
    assert L.neighbour(7, "right") == 8                 # next section
    assert L.neighbour(8, "left") == 7
    assert L.neighbour(7, "down") == 8 + 1              # same column, next section
    assert L.neighbour(8 + 5, "up") == 7                # column 5 -> last photo above
    assert L.neighbour(1, "up") == 1                    # top edge
    assert L.neighbour(0, "page_down", page_rows=3) == 8 + 6
    assert L.neighbour(5, "end") == len(stream) - 1


def test_anchor_survives_a_relayout():
    stream = _stream_of(_library())
    wide, narrow = StreamLayout(stream, 1000, 150), StreamLayout(stream, 520, 150)
    y = wide.rows_ys[2] + 170               # inside boats' second row
    anchor = wide.anchor_at(y)
    assert anchor[0] == "photo" and anchor[1].endswith("boats/0006.jpg")
    new_y = narrow.y_for_anchor(anchor)
    flat = stream.index_of(anchor[1])
    assert narrow.cell(flat)[1] <= new_y < narrow.cell(flat)[1] + narrow.pitch
    assert wide.anchor_at(wide.header_ys[1] + 5) == ("section", ROOT + "/nyhavn", 5)


def test_signature_detects_changes():
    a, b = _stream_of(_library()), _stream_of(_library())
    assert a.signature() == b.signature()
    c = _stream_of(_library((("", 8), ("nyhavn", 15))))
    assert a.signature() != c.signature()


def test_big_library_layout_is_cheap():
    counts = tuple((f"y{y}/m{m:02}", 200) for y in range(10) for m in range(1, 13))
    stream = _stream_of(_library(counts))  # 24,000 photos in 120 folders
    t = time.perf_counter()
    for width in range(600, 1600, 10):     # dragging the window edge
        StreamLayout(stream, width, 150)
    assert (time.perf_counter() - t) / 100 < 0.005, "a relayout should be ~O(folders)"


# --------------------------------------------------------------------- widget
def test_only_the_visible_neighbourhood_gets_widgets():
    counts = (("", 2000),)
    h = Harness()
    h.ps.set_stream(_stream_of(_library(counts)), keep_position=False)
    assert h.ps._tiles == {}              # no width yet: nothing laid out
    h.show_window()
    n = len(h.ps._tiles)
    assert 0 < n < 120, n                 # ~3 screens' worth, not 2000
    assert h.ps.canvas.size[1] > 300 * 162
    # Decoding: what's on screen comes first.
    first = h.decoder.queue[0][0][0][0]
    assert first.endswith("/0000.jpg")
    assert h.decoder.queue[0][2] == fit_within(500, 375, 150)
    # Mid-stream: about three screens of rows (one above, one below).
    h.scroll(150 * 162)
    rows = int(3 * 600 / 162) + 2
    assert len(h.ps._tiles) <= rows * 6
    assert ROOT + "/0900.jpg" in h.shown_tiles()
    assert ROOT + "/0000.jpg" not in h.ps._tiles
    # Scrolling on reuses those widgets rather than making new ones.
    created = len(h.ps.canvas.children)
    for y in range(150 * 162, 250 * 162, 700):
        h.scroll(y)
    assert len(h.ps.canvas.children) == created


def test_scrollspy_reports_the_folder_at_the_top():
    h = Harness()
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    assert h.sections[-1] == "holiday-photos-copenhagen"
    L = h.ps._layout
    h.scroll(L.header_ys[1] - gtk4_stream.SPY_OFFSET)
    assert h.sections[-1] == "nyhavn"
    h.scroll(L.header_ys[2] - gtk4_stream.SPY_OFFSET - 5)
    assert h.sections[-1] == "nyhavn"
    h.scroll(L.height)                    # the bottom: the last folder
    assert h.sections[-1] == "rundetarn"


def test_sidebar_click_before_layout_and_hold_until_the_user_scrolls():
    h = Harness(page=400)
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    # Startup: the remembered folder is requested before the window has a size.
    assert h.ps.scroll_to_folder(ROOT + "/nyhavn/boats") is True
    h.show_window()
    L = h.ps._layout
    assert h.ps.vadj.value == L.header_ys[2] - gtk4_stream.SCROLL_PAD
    assert h.sections[-1] == "boats"
    # A short folder at the end can't reach the top; it still counts as current.
    h.ps.scroll_to_folder(ROOT + "/rundetarn")
    assert h.ps.holding and h.sections[-1] == "rundetarn"
    # A parent without photos of its own goes to its first subfolder.
    h.ps.scroll_to_folder("/home/james")
    assert h.ps.vadj.value == 4 and h.sections[-1] == "holiday-photos-copenhagen"
    h.scroll(L.header_ys[1] - gtk4_stream.SPY_OFFSET)   # the user scrolls
    assert not h.ps.holding and h.sections[-1] == "nyhavn"


def test_scroll_target_waits_for_the_new_height():
    h = Harness(page=600)
    h.ps.set_stream(_stream_of(_library((("", 4),))), keep_position=False)
    h.show_window()
    # A scan adds a big folder below; the user clicks it straight away.
    h.ps.set_stream(_stream_of(_library((("", 4), ("zz", 300)))), keep_position=True)
    h.ps.scroll_to_folder(ROOT + "/zz")
    target = h.ps._layout.header_ys[1] - gtk4_stream.SCROLL_PAD
    assert h.ps.vadj.value == 0           # the scrollbar doesn't span it yet
    h.allocate()
    assert h.ps.vadj.value == target


def test_narrowing_the_window_keeps_your_place():
    h = Harness(width=1000)
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    L = h.ps._layout
    h.scroll(L.rows_ys[2] + 162)          # boats, second row, at the top
    top_photo = h.ps.stream.records[L.photos_between(h.ps.vadj.value, h.ps.vadj.value + 1)[0]]
    h.ps.hadj.page = 520.0
    h.ps._on_hadj_changed(h.ps.hadj)
    h.allocate()
    L2 = h.ps._layout
    assert L2.columns == 3
    y = L2.cell(h.ps.stream.index_of(top_photo.path))[1]
    assert y <= h.ps.vadj.value < y + L2.pitch
    # Tiles were moved to the new grid.
    x, ty = h.ps.canvas.children[h.tile(top_photo.path)]
    cx, cy, slot, _ = L2.cell(h.ps.stream.index_of(top_photo.path))
    assert (x, ty) == (cx + (slot - 150) // 2, cy + (slot - 112) // 2)


def test_scan_adding_photos_above_keeps_the_view():
    h = Harness(page=400)
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    L = h.ps._layout
    h.scroll(L.rows_ys[2])
    before = L.photos_between(h.ps.vadj.value, h.ps.vadj.value + 1)[0]
    path = h.ps.stream.records[before].path
    bigger = _library((("", 8), ("nyhavn", 40), ("nyhavn/boats", 9), ("rundetarn", 3)))
    h.ps.set_stream(_stream_of(bigger), keep_position=True)
    h.allocate()
    L2 = h.ps._layout
    assert L2.cell(h.ps.stream.index_of(path))[1] == h.ps.vadj.value


def test_click_keyboard_and_activation():
    h = Harness()
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    L = h.ps._layout
    x, y, _, _ = L.cell(3)
    h.ps._on_pressed(None, 1, x + 5, y + 5)
    assert h.selected[-1].path.endswith("/0003.jpg")
    assert "tadaima-thumb-selected" in h.tile(h.selected[-1].path).css
    h.ps._on_key(None, "Down", 0, 0)          # 3 -> 7: the short row below's last
    assert h.selected[-1].path.endswith("copenhagen/0007.jpg")
    assert "tadaima-thumb-selected" not in h.tile(ROOT + "/0003.jpg").css
    h.ps._on_key(None, "Down", 0, 0)          # -> next folder, same column (1)
    assert h.selected[-1].path.endswith("nyhavn/0001.jpg")
    h.ps._on_key(None, "End", 0, 0)           # scrolls the last photo into view
    last = h.selected[-1]
    assert last.path.endswith("rundetarn/0002.jpg")
    y = L.cell(h.ps.stream.index_of(last.path))[1]
    assert h.ps.vadj.value <= y and y + L.slot <= h.ps.vadj.value + h.ps.vadj.page
    h.ps._on_key(None, "Return", 0, 0)
    assert h.activated == [last]
    h.ps._on_pressed(None, 2, *L.cell(0)[:2])             # double-click
    assert h.activated[-1].path.endswith("/0000.jpg")
    assert h.ps._on_key(None, "Down", 0, 4) is False      # Ctrl+Down: not ours


def test_selection_dropped_when_its_photo_goes():
    h = Harness()
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    h.ps.select_path(ROOT + "/rundetarn/0002.jpg")
    h.ps.set_stream(_stream_of(_library((("", 8),))), keep_position=True)
    assert h.selected[-1] is None


def test_decoded_thumbnails_and_failures():
    h = Harness()
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    job = h.decoder.queue[0]
    key = job[0]
    tile = h.ps._tiles_by_key[key]
    assert "tadaima-thumb-loading" in tile.css and tile.size == (150, 112)
    h.ps._on_decoded(key, (150, 112))
    assert tile.paintable.get_width() == 150 and "tadaima-thumb-loading" not in tile.css
    assert key in h.ps._cache
    bad = h.decoder.queue[1][0]
    h.ps._on_decoded(bad, None)
    h.ps._last_range = None
    h.ps._update_visible()
    queued = [j[0] for j in h.decoder.queue]
    assert key not in queued and bad not in queued          # cached / failed


def test_zoom_keeps_the_selected_photo_in_place():
    h = Harness()
    h.ps.set_stream(_stream_of(_library()), keep_position=False)
    h.show_window()
    L = h.ps._layout
    h.scroll(L.rows_ys[1])
    flat = L.photos_between(h.ps.vadj.value + 200, h.ps.vadj.value + 201)[0]
    h.ps._select(flat, scroll=False)
    offset = L.cell(flat)[1] - h.ps.vadj.value
    h.ps.set_slot(240)
    h.allocate()
    L2 = h.ps._layout
    assert L2.cell(flat)[1] - h.ps.vadj.value == offset
    assert all(t._key[1] == 240 for t in h.ps._tiles.values())


def test_unknown_thumbnail_size_is_fixed_when_decoded():
    tree = build_tree([_rec(ROOT, 0, w=0, h=0)], [ROOT])
    h = Harness()
    h.ps.set_stream(_stream_of(tree), keep_position=False)
    h.show_window()
    key, _path, size, _box = h.decoder.queue[0]
    assert size is None
    tile = h.ps._tiles_by_key[key]
    assert tile.size == (150, 150)
    h.ps._on_decoded(key, (100, 150))
    assert tile.size == (100, 150)
    x, y = h.ps.canvas.children[tile]
    assert x == 12 + 25


# ------------------------------------------------------- decoding and caching
def test_decoder_threads_queue_replacement_and_clear():
    delivered = []
    gate = threading.Event()

    def decode(path, size, box):
        if path.startswith("/slow"):
            gate.wait(5)
        return (10, 10)

    dec = gtk4_thumbs.ThumbDecoder(lambda k, pb: delivered.append(k), decode=decode, threads=2)
    dec.set_queue([(("k", i), f"/cache/{i}", None, 150) for i in range(20)])
    deadline = time.monotonic() + 5
    while len(dec._results) < 20 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert dec._drain() is False
    assert sorted(delivered) == [("k", i) for i in range(20)]
    # Replacing the queue drops jobs not yet started.
    delivered.clear()
    dec.set_queue([(("slow", 0), "/slow/0", None, 150), (("slow", 1), "/slow/1", None, 150)]
                  + [(("old", i), f"/cache/o{i}", None, 150) for i in range(50)])
    time.sleep(0.05)                          # both threads now blocked on /slow
    dec.set_queue([(("new", 0), "/cache/n", None, 150)])
    gate.set()
    deadline = time.monotonic() + 5
    while dec.pending() and time.monotonic() < deadline:
        time.sleep(0.01)
        dec._drain()
    assert ("new", 0) in delivered and not any(k[0] == "old" for k in delivered)
    # clear() also discards decodes in flight.
    gate.clear()
    delivered.clear()
    dec.set_queue([(("slow", 2), "/slow/2", None, 150)])
    time.sleep(0.05)
    dec.clear()
    gate.set()
    time.sleep(0.1)
    dec._drain()
    assert delivered == []


def test_texture_cache_is_capped_by_bytes():
    cache = gtk4_thumbs.TextureCache(budget=3 * 100 * 100 * 4)
    for i in range(5):
        cache.put(i, Texture(100, 100))
    assert len(cache) == 3 and 0 not in cache and 4 in cache
    cache.get(2)                              # recently used survives
    cache.put(5, Texture(100, 100))
    assert 2 in cache and 3 not in cache
    assert cache.bytes == 3 * 100 * 100 * 4


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"ALL STREAM TESTS PASSED ({len(tests)})")

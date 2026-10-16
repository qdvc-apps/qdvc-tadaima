"""Progressive thumbnail grid, without a display.

Runs ThumbGrid against a list-backed stand-in for Gtk.FlowBox, and the real
ThumbDecoder threads with a stub decode function (imported against the fake
`gi` from test_import_smoke).
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_import_smoke import _install_fake_gi  # noqa: E402

_install_fake_gi()

from qdvc.gtk4 import gtk4_thumbgrid as tg  # noqa: E402
from qdvc.models import ImageRecord  # noqa: E402
from qdvc.ui_prefs import fit_within, thumb_key  # noqa: E402


def _rec(name, mtime=0.0):
    return ImageRecord(path=f"/photos/{name}", mtime=mtime, size=1, key=name,
                       thumb_path=f"/cache/{name}", thumb_w=500, thumb_h=375)


class FakeFlow:
    def __init__(self):
        self.children = []
        self.selected = None

    def insert(self, child, i):
        child.flow = self
        self.children.insert(i, child)

    def remove(self, child):
        self.children.remove(child)

    def remove_all(self):
        self.children.clear()

    def select_child(self, child):
        self.selected = child


class FakeChild:
    def __init__(self, rec, box):
        self.rec = rec
        self._disp_size = fit_within(rec.thumb_w, rec.thumb_h, box)

    def get_index(self):
        return self.flow.children.index(self)


class FakeDecoder:
    def __init__(self):
        self.jobs = []
        self.cleared = 0

    def submit(self, key, path, size, box):
        self.jobs.append((key, path, size, box))

    def clear(self):
        self.cleared += 1
        self.jobs.clear()

    def pending(self):
        return len(self.jobs)


def _grid():
    flow, decoder, textures = FakeFlow(), FakeDecoder(), []
    grid = tg.ThumbGrid(flow, FakeChild, lambda c, t, w, h: textures.append((c, w, h)),
                        decoder=decoder)
    return grid, flow, decoder, textures


def _fill_all(grid):
    calls = 1
    while grid._fill():
        calls += 1
    return calls


def test_fit_within():
    assert fit_within(500, 375, 150) == (150, 112)
    assert fit_within(375, 500, 150) == (112, 150)
    assert fit_within(100, 50, 150) == (100, 50)  # never enlarged
    assert fit_within(0, 0, 150) == (150, 150)


def test_big_folder_fills_in_over_many_idles():
    grid, flow, decoder, _ = _grid()
    recs = [_rec(f"{i:04}.jpg") for i in range(300)]
    old_budget = tg.FRAME_BUDGET_S
    tg.FRAME_BUDGET_S = -1  # every idle callback runs out of time at once
    try:
        grid.show(recs, 150)
        assert flow.children == []  # nothing built synchronously
        assert grid._fill() is True
        assert len(flow.children) == 1  # one slot, then back to the main loop
        calls = 1 + _fill_all(grid)
    finally:
        tg.FRAME_BUDGET_S = old_budget
    assert calls == 300
    assert [c.rec.path for c in flow.children] == [r.path for r in recs]
    # Every slot asked for its thumbnail at its display size.
    assert len(decoder.jobs) == 300
    assert decoder.jobs[0] == (thumb_key(recs[0]), "/cache/0000.jpg", (150, 112), 150)


def test_scan_update_keeps_existing_slots():
    grid, flow, decoder, _ = _grid()
    a, b, d = _rec("a.jpg"), _rec("b.jpg"), _rec("d.jpg")
    grid.show([a, b, d], 150)
    _fill_all(grid)
    before = {c.rec.path: c for c in flow.children}
    flow.select_child(before["/photos/d.jpg"])
    decoder.jobs.clear()

    # The scan finds c.jpg; b.jpg has gone.
    c = _rec("c.jpg")
    grid.update([a, c, d])
    _fill_all(grid)
    assert [ch.rec.path for ch in flow.children] == ["/photos/a.jpg", "/photos/c.jpg",
                                                    "/photos/d.jpg"]
    assert flow.children[0] is before["/photos/a.jpg"]  # not rebuilt
    assert flow.children[2] is before["/photos/d.jpg"]
    assert flow.selected is before["/photos/d.jpg"]  # selection untouched
    assert [j[1] for j in decoder.jobs] == ["/cache/c.jpg"]  # only the new one loads


def test_update_with_no_changes_does_nothing():
    grid, flow, decoder, _ = _grid()
    recs = [_rec("a.jpg"), _rec("b.jpg")]
    grid.show(recs, 150)
    _fill_all(grid)
    grid._fill_source = 0
    grid.update([_rec("a.jpg"), _rec("b.jpg")])  # equal records, new objects
    assert grid._fill_source == 0  # no fill scheduled


def test_changed_file_gets_a_new_slot():
    grid, flow, decoder, _ = _grid()
    grid.show([_rec("a.jpg")], 150)
    _fill_all(grid)
    old = flow.children[0]
    grid.update([_rec("a.jpg", mtime=5.0)])
    _fill_all(grid)
    assert flow.children[0] is not old and len(flow.children) == 1


def test_late_and_failed_thumbnails():
    grid, flow, decoder, textures = _grid()
    a, b = _rec("a.jpg"), _rec("b.jpg")
    grid.show([a, b], 150)
    _fill_all(grid)
    pixbuf = type("PB", (), {"get_width": lambda s: 150, "get_height": lambda s: 112})()
    grid._deliver(thumb_key(a), pixbuf)
    assert textures == [(flow.children[0], 150, 112)]
    # A decode that failed (file missing) marks its slot to be rebuilt...
    grid._deliver(thumb_key(b), None)
    assert flow.children[1]._failed
    # ...and a thumbnail for a slot that no longer exists is ignored.
    grid.show([_rec("z.jpg")], 150)
    grid._deliver(thumb_key(a), pixbuf)
    assert len(textures) == 1
    assert decoder.cleared >= 1  # opening another folder drops queued decodes


def test_select_path_waits_for_its_slot():
    grid, flow, decoder, _ = _grid()
    recs = [_rec(f"{i:02}.jpg") for i in range(10)]
    old_budget = tg.FRAME_BUDGET_S
    tg.FRAME_BUDGET_S = -1
    try:
        grid.show(recs, 150)
        grid._fill()
        grid.select_path("/photos/07.jpg")  # e.g. back from the viewer
        assert flow.selected is None
        _fill_all(grid)
    finally:
        tg.FRAME_BUDGET_S = old_budget
    assert flow.selected.rec.path == "/photos/07.jpg"


def test_decoder_threads_deliver_in_batches_and_clear_drops_old_work():
    delivered = []
    gate = threading.Event()

    def decode(path, size, box):
        if path.startswith("/slow"):
            gate.wait(5)
        return ("pixbuf", path)

    dec = tg.ThumbDecoder(lambda k, pb: delivered.append(k), decode=decode, threads=2)
    for i in range(20):
        dec.submit(("k", i), f"/cache/{i}", (150, 112), 150)
    deadline = time.monotonic() + 5
    while len(dec._results) < 20 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert dec._drain() is False  # delivered everything, nothing left scheduled
    assert sorted(delivered) == [("k", i) for i in range(20)]
    assert dec._drain_scheduled is False

    # Work in flight when the folder changes is thrown away.
    delivered.clear()
    dec.submit(("old", 0), "/slow/0", None, 150)
    time.sleep(0.05)
    dec.clear()
    gate.set()
    dec.submit(("new", 0), "/cache/new", None, 150)
    deadline = time.monotonic() + 5
    while not dec._results and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    dec._drain()
    assert delivered == [("new", 0)]


def test_drain_yields_when_over_budget():
    dec = tg.ThumbDecoder(lambda k, pb: None, decode=lambda *a: None, threads=1)
    dec._results.extend([(i, None) for i in range(3)])
    dec._drain_scheduled = True
    old_budget = tg.FRAME_BUDGET_S
    tg.FRAME_BUDGET_S = -1
    try:
        assert dec._drain() is True  # one delivered, the rest next idle
        assert len(dec._results) == 2
        assert dec._drain() is True
        assert dec._drain() is True
        assert dec._drain() is False
    finally:
        tg.FRAME_BUDGET_S = old_budget
    assert dec._drain_scheduled is False


def test_window_scan_refresh_updates_instead_of_rebuilding():
    import types

    from qdvc.gtk4.gtk4_window import MainWindow
    from qdvc.models import FolderNode

    calls = []
    w = object.__new__(MainWindow)
    w._thumb_grid = types.SimpleNamespace(
        show=lambda recs, box: calls.append(("show", len(recs))),
        update=lambda recs: calls.append(("update", len(recs))),
    )
    w.detail_stack = types.SimpleNamespace(set_visible_child_name=lambda n: None)
    w.detail_scroller = types.SimpleNamespace(get_vadjustment=lambda: None)
    w._thumb_zoom = 150
    w._shown_folder_path = None
    folder = FolderNode(path="/photos", name="photos", own_images=[_rec("a.jpg")])
    w._show_folder_photos(folder)            # the user opens the folder
    folder.own_images.append(_rec("b.jpg"))
    w._refresh_folder_photos(folder)         # a scan finds another photo
    other = FolderNode(path="/other", name="other", own_images=[_rec("c.jpg")])
    w._refresh_folder_photos(other)          # a different folder: start over
    assert calls == [("show", 1), ("update", 2), ("show", 1)]


if __name__ == "__main__":
    test_fit_within()
    test_big_folder_fills_in_over_many_idles()
    test_scan_update_keeps_existing_slots()
    test_update_with_no_changes_does_nothing()
    test_changed_file_gets_a_new_slot()
    test_late_and_failed_thumbnails()
    test_select_path_waits_for_its_slot()
    test_decoder_threads_deliver_in_batches_and_clear_drops_old_work()
    test_drain_yields_when_over_budget()
    test_window_scan_refresh_updates_instead_of_rebuilding()
    print("ALL THUMB GRID TESTS PASSED")

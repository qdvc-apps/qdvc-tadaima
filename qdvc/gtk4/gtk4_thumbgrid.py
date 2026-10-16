"""Progressive thumbnail grid: opening a big folder never blocks the UI.

Filling a Gtk.FlowBox with hundreds of photos in one go froze the window for
seconds — every thumbnail was decoded, scaled and wrapped in widgets on the
main thread before the next frame. Here the work is split:

* :class:`ThumbGrid` adds the photo slots a few at a time from an idle
  callback, stopping after ``FRAME_BUDGET_S`` so input and redraws get a turn.
  Each slot is created at its final size (the index records every thumbnail's
  pixel size), so the layout doesn't jump as pictures arrive.
* :class:`ThumbDecoder` decodes the cached thumbnails on background threads,
  straight to display size, and hands them back to the main thread in batches.

``ThumbGrid.update`` reconciles the grid with a new photo list for the *same*
folder (as a background scan finds photos), keeping the slots that are still
there — selection, scroll position and loaded pictures included.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from gi.repository import Gdk, GdkPixbuf, GLib

from ..ui_prefs import fit_within, thumb_key

# Main-thread work per idle callback before yielding to input and redraws.
FRAME_BUDGET_S = 0.008
# Decoding threads (GdkPixbuf's JPEG/PNG loaders are thread-safe, and
# PyGObject releases the GIL while they run).
DECODE_THREADS = 2


def decode_thumbnail(path: str, size: tuple[int, int] | None, box: int):
    """Load *path* as a GdkPixbuf at *size* (width, height), or, when the size
    is unknown, at its own size shrunk to fit a *box* square. Thread-safe."""
    if size is not None:
        return GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size[0], size[1], False)
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = fit_within(pb.get_width(), pb.get_height(), box)
    if (w, h) != (pb.get_width(), pb.get_height()):
        pb = pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
    return pb


class ThumbDecoder:
    """Decodes thumbnails on background threads, in the order requested.

    Results go to *deliver(key, pixbuf_or_None)* on the main thread, a batch
    per idle callback. :meth:`clear` drops every queued job and any result not
    yet delivered (e.g. when the user opens another folder)."""

    def __init__(
        self,
        deliver: Callable[[object, object], None],
        decode: Callable = decode_thumbnail,
        threads: int = DECODE_THREADS,
    ) -> None:
        self._deliver = deliver
        self._decode = decode
        self._n_threads = threads
        self._threads: list[threading.Thread] = []
        self._cond = threading.Condition()
        self._jobs: deque = deque()
        self._results: deque = deque()
        self._generation = 0
        self._drain_scheduled = False

    def submit(self, key, path: str, size: tuple[int, int] | None, box: int) -> None:
        with self._cond:
            if len(self._threads) < self._n_threads:
                t = threading.Thread(target=self._work, daemon=True,
                                     name="tadaima-thumbs")
                self._threads.append(t)
                t.start()
            self._jobs.append((key, path, size, box))
            self._cond.notify()

    def clear(self) -> None:
        with self._cond:
            self._generation += 1  # in-flight decodes are discarded on return
            self._jobs.clear()
            self._results.clear()

    def pending(self) -> int:
        with self._cond:
            return len(self._jobs) + len(self._results)

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._jobs:
                    self._cond.wait()
                key, path, size, box = self._jobs.popleft()
                generation = self._generation
            try:
                pixbuf = self._decode(path, size, box)
            except Exception:  # missing or unreadable file: leave the placeholder
                pixbuf = None
            with self._cond:
                if generation != self._generation:
                    continue
                self._results.append((key, pixbuf))
                if not self._drain_scheduled:
                    self._drain_scheduled = True
                    GLib.idle_add(self._drain)

    def _drain(self) -> bool:
        deadline = time.monotonic() + FRAME_BUDGET_S
        while True:
            with self._cond:
                if not self._results:
                    # Reset under the lock, so a worker that finishes now
                    # schedules a fresh drain instead of being missed.
                    self._drain_scheduled = False
                    return False
                key, pixbuf = self._results.popleft()
            self._deliver(key, pixbuf)
            if time.monotonic() > deadline:
                return True  # more next idle, after input and redraws


class ThumbGrid:
    """Fills *flow* (a Gtk.FlowBox) progressively.

    *make_child(rec, box)* builds a placeholder Gtk.FlowBoxChild for a photo,
    with ``_disp_size`` set to its display (width, height), or None if unknown.
    *set_texture(child, texture, width, height)* puts a decoded thumbnail into
    it. Children also get ``_key`` (see :func:`thumb_key`) and ``_failed``."""

    def __init__(self, flow, make_child, set_texture, decoder: ThumbDecoder | None = None):
        self.flow = flow
        self._make_child = make_child
        self._set_texture = set_texture
        self._decoder = decoder or ThumbDecoder(self._deliver)
        self._children: dict = {}   # key -> FlowBoxChild in the flow box
        self._desired: list = []    # records to show, in display order
        self._desired_keys: list = []
        self._cursor = 0            # flow matches _desired[:_cursor]
        self._box = 0
        self._fill_source = 0
        self._select_path: str | None = None

    # ------------------------------------------------------------ public
    def show(self, records, box: int) -> None:
        """Start over with *records* (another folder, or a new slot size)."""
        self.cancel()
        if hasattr(self.flow, "remove_all"):  # GTK 4.12+
            self.flow.remove_all()
        else:
            for child in list(self._children.values()):
                self.flow.remove(child)
        self._children.clear()
        self._box = box
        self._start(records)

    def update(self, records) -> None:
        """Show *records* for the same folder, keeping slots still in it."""
        keys = [thumb_key(r) for r in records]
        if keys == self._desired_keys:
            return
        wanted = set(keys)
        for key in [k for k, c in self._children.items()
                    if k not in wanted or getattr(c, "_failed", False)]:
            self.flow.remove(self._children.pop(key))
        self._start(records, keys)

    def cancel(self) -> None:
        if self._fill_source:
            GLib.source_remove(self._fill_source)
            self._fill_source = 0
        self._decoder.clear()
        self._select_path = None

    def select_path(self, path: str) -> None:
        """Select *path*'s slot now, or as soon as it has been created."""
        child = self.child_for_path(path)
        if child is not None:
            self.flow.select_child(child)
        elif any(r.path == path for r in self._desired):
            self._select_path = path

    def child_for_path(self, path: str):
        for key, child in self._children.items():
            if key[0] == path:
                return child
        return None

    @property
    def loading(self) -> bool:
        return bool(self._fill_source) or self._decoder.pending() > 0

    # ---------------------------------------------------------- internals
    def _start(self, records, keys=None) -> None:
        self._desired = list(records)
        self._desired_keys = keys if keys is not None else [thumb_key(r) for r in records]
        self._cursor = 0
        if not self._fill_source and self._desired:
            self._fill_source = GLib.idle_add(self._fill)

    def _fill(self) -> bool:
        deadline = time.monotonic() + FRAME_BUDGET_S
        while self._cursor < len(self._desired):
            i = self._cursor
            rec, key = self._desired[i], self._desired_keys[i]
            child = self._children.get(key)
            if child is None:
                child = self._make_child(rec, self._box)
                child._key = key
                child._failed = False
                self.flow.insert(child, i)
                self._children[key] = child
                if rec.thumb_path:
                    self._decoder.submit(key, rec.thumb_path,
                                         getattr(child, "_disp_size", None), self._box)
                if self._select_path == rec.path:
                    self._select_path = None
                    self.flow.select_child(child)
            elif child.get_index() != i:  # same photo, moved (e.g. renamed sibling)
                self.flow.remove(child)
                self.flow.insert(child, i)
            self._cursor += 1
            if self._cursor < len(self._desired) and time.monotonic() > deadline:
                return True  # carry on next idle, after input and redraws
        self._fill_source = 0
        return False

    def _deliver(self, key, pixbuf) -> None:
        child = self._children.get(key)
        if child is None:
            return  # its slot is gone (folder changed or photo removed)
        if pixbuf is None:
            child._failed = True  # rebuilt by the next update()
            return
        self._set_texture(child, Gdk.Texture.new_for_pixbuf(pixbuf),
                          pixbuf.get_width(), pixbuf.get_height())

"""Thumbnail decoding off the main thread, and a cache of the results.

:class:`ThumbDecoder` decodes cached thumbnail files on background threads,
straight to display size, and hands them back to the main thread in batches
from an idle callback that stops after ``FRAME_BUDGET_S`` so input and
redraws get a turn. Its queue can be replaced wholesale (:meth:`set_queue`):
the photo stream does that on every scroll, so whatever is on screen now
is decoded next and photos scrolled past are never decoded at all.

:class:`TextureCache` keeps recently shown thumbnails as ``Gdk.Texture``\\ s,
so scrolling back is instant.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Callable

from gi.repository import GdkPixbuf, GLib

from ..ui_prefs import fit_within

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
    """Decodes thumbnails on background threads, in queue order.

    A job is ``(key, path, size, box)``. Results go to
    *deliver(key, pixbuf_or_None)* on the main thread. :meth:`clear` drops
    every queued job and every result not yet delivered, and discards decodes
    still in flight."""

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
        self._inflight: set = set()
        self._results: deque = deque()
        self._generation = 0
        self._drain_scheduled = False

    def submit(self, key, path: str, size: tuple[int, int] | None, box: int) -> None:
        with self._cond:
            self._ensure_threads()
            self._jobs.append((key, path, size, box))
            self._cond.notify()

    def set_queue(self, jobs) -> None:
        """Replace every queued job with *jobs* (most wanted first). Jobs whose
        key is being decoded right now are skipped; that result still comes."""
        with self._cond:
            self._jobs = deque(j for j in jobs if j[0] not in self._inflight)
            if self._jobs:
                self._ensure_threads()
                self._cond.notify_all()

    def clear(self) -> None:
        with self._cond:
            self._generation += 1  # in-flight decodes are discarded on return
            self._jobs.clear()
            self._results.clear()

    def pending(self) -> int:
        with self._cond:
            return len(self._jobs) + len(self._inflight) + len(self._results)

    def _ensure_threads(self) -> None:  # called with the lock held
        while len(self._threads) < self._n_threads:
            t = threading.Thread(target=self._work, daemon=True, name="tadaima-thumbs")
            self._threads.append(t)
            t.start()

    def _work(self) -> None:
        while True:
            with self._cond:
                while not self._jobs:
                    self._cond.wait()
                key, path, size, box = self._jobs.popleft()
                self._inflight.add(key)
                generation = self._generation
            try:
                pixbuf = self._decode(path, size, box)
            except Exception:  # missing or unreadable file: leave the placeholder
                pixbuf = None
            with self._cond:
                self._inflight.discard(key)
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


class TextureCache:
    """The most recently used thumbnails, as textures, up to *budget* bytes
    of pixels (a 150 px thumbnail is about 70 KB; a 420 px one about 530 KB)."""

    def __init__(self, budget: int = 160 * 1024 * 1024) -> None:
        self.budget = budget
        self.bytes = 0
        self._items: OrderedDict = OrderedDict()   # key -> (texture, bytes)

    def get(self, key):
        item = self._items.get(key)
        if item is None:
            return None
        self._items.move_to_end(key)
        return item[0]

    def put(self, key, texture) -> None:
        size = max(1, texture.get_width() * texture.get_height() * 4)
        old = self._items.pop(key, None)
        if old is not None:
            self.bytes -= old[1]
        self._items[key] = (texture, size)
        self.bytes += size
        while self.bytes > self.budget and len(self._items) > 1:
            _key, (_tex, freed) = self._items.popitem(last=False)
            self.bytes -= freed

    def clear(self) -> None:
        self._items.clear()
        self.bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key) -> bool:
        return key in self._items

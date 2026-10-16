"""Sidebar expansion memory and startup restore, without a display.

Drives the real MainWindow methods (imported against the fake `gi` from
test_import_smoke) with stand-ins for Gtk.TreeListModel and its rows. The fake
rows copy what libgtk-4 4.14 does when a row is torn down — the store rebuilt,
or a parent collapsed: it notifies ``expanded`` = False while ``get_item()``
still returns the item and ``is_expandable()`` turns False.
"""

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_import_smoke import _install_fake_gi  # noqa: E402

_install_fake_gi()

from qdvc.gtk4.gtk4_items import FolderItem  # noqa: E402
from qdvc.gtk4.gtk4_window import MainWindow  # noqa: E402
from qdvc.models import ImageRecord  # noqa: E402
from qdvc.tree import build_tree  # noqa: E402

PHOTOS = "/home/james/Pictures/2024"


class FakeRow:
    def __init__(self, item):
        self.item = item
        self.expanded = False
        self.alive = True
        self.children = None  # FakeRows, created on first expansion

    def get_item(self):
        return self.item

    def get_expanded(self):
        return self.alive and self.expanded

    def is_expandable(self):
        return self.alive and bool(self.item.node.children)

    def set_expanded(self, value):
        self.expanded = value


class FakeTreeModel:
    """Flattens root rows + the children of expanded rows, like TreeListModel."""

    def __init__(self, items):
        self.roots = [FakeRow(i) for i in items]

    def _flat(self):
        out = []

        def walk(rows):
            for r in rows:
                out.append(r)
                if r.get_expanded():
                    if r.children is None:
                        r.children = [
                            FakeRow(FolderItem(c)) for c in r.item.node.sorted_children()
                        ]
                    walk(r.children)

        walk(self.roots)
        return out

    def get_n_items(self):
        return len(self._flat())

    def get_item(self, i):
        return self._flat()[i]


def _window(tree_root, expanded=(), pending=None):
    w = object.__new__(MainWindow)
    w.config = types.SimpleNamespace(expanded_folders=[], selected_folder=None,
                                     focus_folder=None)
    w.tree_root = tree_root
    w.focus_path = "/"
    w.selected_folder = None
    w._pending_selected = pending
    w._expanded = set(expanded)
    w._restoring_state = False
    w._rebuilding_sidebar = False
    w._closing = False
    w._shown_folder_path = None
    w._tree_model = FakeTreeModel([FolderItem(tree_root, style="focused")])
    w.selected_positions = []
    w._selection = types.SimpleNamespace(set_selected=w.selected_positions.append)
    w.scrolled_to = []
    w._scroll_sidebar_to = w.scrolled_to.append
    return w


def _tree():
    return build_tree(
        [ImageRecord(path=f"{PHOTOS}/a.jpg", mtime=0.0, size=1, key="a"),
         ImageRecord(path="/home/james/Other/b.jpg", mtime=0.0, size=1, key="b")],
        ["/home/james"],
    )


def test_rebuild_teardown_does_not_forget_expanded_folders():
    w = _window(_tree(), expanded={"/", "/home", "/home/james"})
    row = FakeRow(FolderItem(w.tree_root))
    row.expanded = True
    # The store is rebuilt: GTK tears the row down and notifies expanded=False,
    # still handing back its item (the bug: this used to be read as a collapse).
    row.alive = False
    w._on_row_expanded_changed(row, None)
    assert w._expanded == {"/", "/home", "/home/james"}
    assert w.config.expanded_folders == []  # nothing persisted either


def test_rebuild_guard_ignores_notifications():
    w = _window(_tree(), expanded={"/home"})
    w._rebuilding_sidebar = True
    row = FakeRow(FolderItem(w.tree_root))
    w._on_row_expanded_changed(row, None)  # live row, but mid-rebuild
    assert w._expanded == {"/home"}


def test_user_expand_and_collapse_are_remembered():
    w = _window(_tree(), expanded={"/home/james", "/home/james/Pictures"})
    home = FakeRow(FolderItem(w.tree_root.children[0]))  # /home
    home.expanded = True
    w._on_row_expanded_changed(home, None)
    assert "/home" in w._expanded
    assert w.config.expanded_folders == ["/home", "/home/james", "/home/james/Pictures"]
    # The user collapses /home: it and everything open inside it are forgotten.
    home.expanded = False
    w._on_row_expanded_changed(home, None)
    assert w._expanded == set()
    assert w.config.expanded_folders == []


def test_startup_reveals_and_selects_the_last_folder():
    # A config whose expansion was lost (as the bug saved it): only the
    # selected folder is known. Startup must open the way to it and select it.
    w = _window(_tree(), expanded=(), pending=PHOTOS)
    w._reapply_sidebar_state(reveal=True)
    rows = [w._tree_model.get_item(i) for i in range(w._tree_model.get_n_items())]
    paths = [r.get_item().node.path for r in rows]
    assert paths == ["/", "/home", "/home/james", "/home/james/Other",
                     "/home/james/Pictures", PHOTOS]
    assert w.selected_positions == [paths.index(PHOTOS)]
    assert w.selected_folder.path == PHOTOS
    assert w.scrolled_to == [PHOTOS]
    assert w._expanded == {"/", "/home", "/home/james", "/home/james/Pictures"}
    # Not a user action, so nothing is written back to config mid-restore.
    assert w.config.expanded_folders == []


def test_restore_without_reveal_keeps_collapsed_parents_collapsed():
    w = _window(_tree(), expanded={"/"}, pending=PHOTOS)
    w._reapply_sidebar_state()
    assert w.selected_positions == []  # its parents are closed, so no row
    assert w.scrolled_to == []


if __name__ == "__main__":
    test_rebuild_teardown_does_not_forget_expanded_folders()
    test_rebuild_guard_ignores_notifications()
    test_user_expand_and_collapse_are_remembered()
    test_startup_reveals_and_selects_the_last_folder()
    test_restore_without_reveal_keeps_collapsed_parents_collapsed()
    print("ALL SIDEBAR STATE TESTS PASSED")

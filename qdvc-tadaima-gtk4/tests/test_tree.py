"""Display-free pure-model tests for the tree/focus logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdvc.models import ImageRecord  # noqa: E402
from qdvc.tree import (  # noqa: E402
    ancestor_chain,
    build_tree,
    find_node,
    forget_subtree,
    is_within,
    reveal_chain,
)


def _rec(path):
    return ImageRecord(path=path, mtime=0.0, size=1, key="k" + path)


def test_filtered_hierarchy_and_counts():
    roots = [
        "/home/james/Desktop/holiday-photos-copenhagen",
        "/home/james/wedding-photos",
        "/home/james/some-project/project/project-diagrams",
    ]
    records = [
        _rec("/home/james/Desktop/holiday-photos-copenhagen/nyhavn/a.jpg"),
        _rec("/home/james/Desktop/holiday-photos-copenhagen/nyhavn/boats/b.jpg"),
        _rec("/home/james/wedding-photos/c.png"),
        _rec("/home/james/wedding-photos/d.png"),
        _rec("/home/james/some-project/project/project-diagrams/e.jpg"),
    ]
    root = build_tree(records, roots)

    james = find_node(root, "/home/james")
    assert james is not None
    # james has exactly 3 filtered subfolders.
    assert {c.name for c in james.children} == {
        "Desktop",
        "wedding-photos",
        "some-project",
    }
    assert james.image_count == 5  # recursive

    wedding = find_node(root, "/home/james/wedding-photos")
    assert wedding.image_count == 2
    assert len(wedding.own_images) == 2

    nyhavn = find_node(
        root, "/home/james/Desktop/holiday-photos-copenhagen/nyhavn"
    )
    assert nyhavn.image_count == 2  # a.jpg + boats/b.jpg
    assert len(nyhavn.own_images) == 1  # only a.jpg directly


def test_ancestor_chain_for_focus():
    roots = ["/home/james/Desktop/holiday-photos-copenhagen"]
    records = [_rec("/home/james/Desktop/holiday-photos-copenhagen/x.jpg")]
    root = build_tree(records, roots)

    chain = ancestor_chain(root, "/home/james/Desktop")
    names = [n.name for n in chain]
    # Root, home, james above Desktop.
    assert names[-1] == "james"
    assert "home" in names


def test_is_within():
    assert is_within("/home/james", "/home/james")
    assert is_within("/home/james", "/home/james/Pictures/2024")
    assert is_within("/", "/home")
    assert not is_within("/home/james", "/home/jamesbond")
    assert not is_within("/home/james/Pictures", "/home/james")


def test_reveal_chain_opens_every_parent_below_the_focus():
    # Unfocused view: "/" is the top row, so it and every folder down to the
    # target's parent must be expanded for the target to have a row.
    assert reveal_chain("/", "/home/james/Pictures/2024") == [
        "/",
        "/home",
        "/home/james",
        "/home/james/Pictures",
    ]
    # Focused on james: ancestors above the focus are flat rows, never expanded.
    assert reveal_chain("/home/james", "/home/james/Pictures/2024") == [
        "/home/james",
        "/home/james/Pictures",
    ]
    assert reveal_chain("/home/james", "/home/james/Pictures") == ["/home/james"]
    # Nothing to open for the focus itself or for a folder outside it.
    assert reveal_chain("/home/james", "/home/james") == []
    assert reveal_chain("/home/james", "/srv/photos") == []
    assert reveal_chain("/home/james", "/home/jamesbond/x") == []


def test_forget_subtree():
    expanded = {"/", "/home", "/home/james", "/home/james/Pictures", "/home/jamesbond"}
    forget_subtree(expanded, "/home/james")
    assert expanded == {"/", "/home", "/home/jamesbond"}


if __name__ == "__main__":
    test_filtered_hierarchy_and_counts()
    test_ancestor_chain_for_focus()
    test_is_within()
    test_reveal_chain_opens_every_parent_below_the_focus()
    test_forget_subtree()
    print("ALL TREE TESTS PASSED")

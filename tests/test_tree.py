"""Display-free pure-model tests for the tree/focus logic."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qdvc.models import ImageRecord  # noqa: E402
from qdvc.tree import ancestor_chain, build_tree, find_node  # noqa: E402


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


if __name__ == "__main__":
    test_filtered_hierarchy_and_counts()
    test_ancestor_chain_for_focus()
    print("ALL TREE TESTS PASSED")

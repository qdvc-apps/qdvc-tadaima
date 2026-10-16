"""Build the filtered folder hierarchy shown in the sidebar. Pure — no GTK.

The hierarchy mirrors the real filesystem but is filtered down to only the
paths leading to the user's scanned folders (and everything beneath them). Each
node carries a recursive image count and the images that live directly in it.

The "Focus on folder" transformation is also expressed here, purely as data:
given a full tree and a focus path, it returns the flattened ancestor chain plus
the focused subtree.
"""

from __future__ import annotations

import os

from .models import FolderNode, ImageRecord


def build_tree(records: list[ImageRecord], scanned_roots: list[str]) -> FolderNode:
    """Build the full filtered hierarchy rooted at ``/`` (or a common root).

    Only directories on the path to — or beneath — a scanned root appear. Image
    counts are recursive; ``own_images`` holds images directly in each folder.
    """
    root = FolderNode(path=os.path.sep, name=os.path.sep)
    nodes: dict[str, FolderNode] = {root.path: root}
    scanned_set = {os.path.normpath(p) for p in scanned_roots}

    def ensure_node(path: str) -> FolderNode:
        path = os.path.normpath(path)
        if path in nodes:
            return nodes[path]
        parent_path = os.path.dirname(path)
        if parent_path == path:  # reached filesystem root
            node = FolderNode(path=path, name=path)
            nodes[path] = node
            return node
        parent = ensure_node(parent_path)
        name = os.path.basename(path) or path
        node = FolderNode(path=path, name=name)
        if path in scanned_set:
            node.is_scanned_root = True
        parent.add_child(node)
        nodes[path] = node
        return node

    # Make sure every scanned root exists even if it has no images.
    for sroot in scanned_roots:
        ensure_node(sroot)

    # Place each image in its directory node.
    for rec in records:
        d = os.path.dirname(rec.path)
        node = ensure_node(d)
        node.own_images.append(rec)

    # Compute recursive image counts bottom-up.
    _compute_counts(root)

    # Collapse the chain of single-child directories above the shallowest
    # scanned root? No — the spec wants the real hierarchy preserved (home →
    # james → ...), so we keep every ancestor directory as its own node.
    _sort_recursive(root)
    return root


def _compute_counts(node: FolderNode) -> int:
    total = len(node.own_images)
    for child in node.children:
        total += _compute_counts(child)
    node.image_count = total
    return total


def _sort_recursive(node: FolderNode) -> None:
    node.children.sort(key=lambda n: n.name.lower())
    node.own_images.sort(key=lambda r: r.name.lower())
    for child in node.children:
        _sort_recursive(child)


def find_node(root: FolderNode, path: str) -> FolderNode | None:
    """Locate a node by absolute path within *root*."""
    target = os.path.normpath(path)
    if os.path.normpath(root.path) == target:
        return root
    stack = [root]
    while stack:
        n = stack.pop()
        if os.path.normpath(n.path) == target:
            return n
        stack.extend(n.children)
    return None


def is_within(ancestor: str, descendant: str) -> bool:
    """True if *descendant* is *ancestor* or lies beneath it."""
    a = os.path.normpath(ancestor)
    d = os.path.normpath(descendant)
    if a == d:
        return True
    return d.startswith(a.rstrip(os.sep) + os.sep)


def reveal_chain(focus_path: str, target: str) -> list[str]:
    """Folders to expand, top-down, so *target* shows in a tree whose top row
    is *focus_path*: the focus itself down to *target*'s parent. Empty when
    *target* is the focus or lies outside it."""
    focus = os.path.normpath(focus_path)
    target = os.path.normpath(target)
    if target == focus or not is_within(focus, target):
        return []
    chain: list[str] = []
    p = os.path.dirname(target)
    while is_within(focus, p):
        chain.append(p)
        parent = os.path.dirname(p)
        if p == focus or parent == p:
            break
        p = parent
    chain.reverse()
    return chain


def forget_subtree(expanded: set[str], path: str) -> None:
    """Drop *path* and every folder beneath it from the *expanded* set (in
    place): collapsing a folder forgets what was open inside it."""
    expanded.difference_update([p for p in expanded if is_within(path, p)])


def ancestor_chain(root: FolderNode, focus_path: str) -> list[FolderNode]:
    """Flat list of ancestor nodes from the tree root down to (but excluding)
    the focused node, in order. Used to render the italic flattened header."""
    target = os.path.normpath(focus_path)
    chain: list[FolderNode] = []

    def dfs(node: FolderNode, acc: list[FolderNode]) -> bool:
        if os.path.normpath(node.path) == target:
            chain.extend(acc)
            return True
        for child in node.children:
            if dfs(child, acc + [node]):
                return True
        return False

    dfs(root, [])
    return chain

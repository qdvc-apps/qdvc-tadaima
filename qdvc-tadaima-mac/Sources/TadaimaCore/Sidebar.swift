import Foundation

/// The sidebar as data: which rows are visible for a focus and a set of
/// expanded folders, and which row stands for a folder that is hidden inside
/// a collapsed one (the scrollspy's "nearest visible parent"). Kept out of
/// the views so it is unit-tested with the rest of the core.
public struct SidebarRow: Identifiable, Hashable, Sendable {
    public enum Kind: Hashable, Sendable {
        /// A folder above the focus, listed flat: click to focus there.
        case enclosing
        /// The focused folder (the top of the tree; "/" when unfocused).
        case focused
        /// A folder inside the focus.
        case folder
    }

    public let path: String
    public let name: String
    public let kind: Kind
    /// Indentation level within the tree (0 for the focused folder and the
    /// enclosing folders above it).
    public let depth: Int
    /// Photos here and below.
    public let count: Int
    public let hasChildren: Bool
    public let isExpanded: Bool
    /// The photo used as the folder's icon, if it has photos of its own.
    public let icon: ImageRecord?

    public var id: String { kind == .enclosing ? "enclosing:" + path : path }
}

public enum Sidebar {
    /// The rows to show: the enclosing folders (when focused), the focused
    /// folder, then every folder below it whose parents are all expanded.
    public static func rows(root: FolderNode, focus: String, expanded: Set<String>) -> [SidebarRow] {
        let focusNode = FolderTree.find(root, focus) ?? root
        var rows: [SidebarRow] = []
        if Paths.normpath(focusNode.path) != Paths.normpath(root.path) {
            for node in FolderTree.ancestorChain(root, focusNode.path) {
                rows.append(row(node, kind: .enclosing, depth: 0, expanded: false))
            }
        }
        func add(_ node: FolderNode, depth: Int, kind: SidebarRow.Kind) {
            let open = expanded.contains(Paths.normpath(node.path)) && !node.children.isEmpty
            rows.append(row(node, kind: kind, depth: depth, expanded: open))
            guard open else { return }
            for child in node.sortedChildren { add(child, depth: depth + 1, kind: .folder) }
        }
        add(focusNode, depth: 0, kind: .focused)
        return rows
    }

    private static func row(_ node: FolderNode, kind: SidebarRow.Kind, depth: Int, expanded: Bool) -> SidebarRow {
        SidebarRow(path: node.path, name: node.name == "/" ? "/" : node.name, kind: kind, depth: depth,
                   count: node.imageCount, hasChildren: !node.children.isEmpty, isExpanded: expanded,
                   icon: node.iconImage)
    }

    /// The row that stands for *path*: its own if visible, else its nearest
    /// visible parent's within the focus. Never an enclosing row. Nil when
    /// *path* is outside the focus.
    public static func visibleRow(for path: String, in rows: [SidebarRow], focus: String) -> SidebarRow? {
        let byPath = Dictionary(rows.filter { $0.kind != .enclosing }.map { (Paths.normpath($0.path), $0) },
                                uniquingKeysWith: { first, _ in first })
        let focusPath = Paths.normpath(focus)
        var p = Paths.normpath(path)
        guard Paths.isWithin(focusPath, p) else { return nil }
        while true {
            if let row = byPath[p] { return row }
            let parent = Paths.dirname(p)
            if p == focusPath || parent == p { return nil }
            p = parent
        }
    }
}
